"""
流式 VAD + ASR + 声纹识别管道
完全对齐 D:\InsightEye\app\streaming_pipeline.py（模式二：多人自动识别）

核心架构：
- feed_audio: 接收音频流，内部包含 VAD 检测、说话人边界检测、片段合并
- StreamingVAD: 静音超时触发分段 + embedding 滑动窗口说话人变化检测
- 合并逻辑：等相同说话人片段累积（或超时）→ 合并音频后送 ASR
- ASR 结果通过 on_transcript 回调发送
- 去噪（RNNoise） + MacBERT 文本纠错后处理
"""

import asyncio
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np
import torch

from app.asr.model_manager import SpeakerEmbeddingExtractor

# ============== 配置（对齐 InsightEye）==============

@dataclass
class StreamingChangeDetectorConfig:
    """流式说话人切换检测配置（超高灵敏度版 - 极速响应）"""

    embedding_interval_ms: float = 100.0
    embedding_min_for_detection: int = 5

    change_distance_threshold: float = 0.10
    change_confirm_count: int = 1
    min_change_interval_ms: float = 800.0

    use_dynamic_threshold: bool = True
    dynamic_factor: float = 0.3

    use_sliding_window: bool = True
    sliding_window_size: int = 4

    detect_jump_only: bool = True
    jump_threshold: float = 0.08
    jump_confirm_count: int = 1

    min_segment_duration_ms: float = 2000.0
    warmup_duration_ms: float = 1500.0

    offline_changepoint: bool = True
    cp_min_segment_ms: float = 800.0
    cp_penalty: float = 0.2

    def __post_init__(self):
        assert self.change_confirm_count >= 1


CHANGE_DETECTOR_CONFIG = StreamingChangeDetectorConfig()


VAD_SAMPLE_RATE = 16000
VAD_WINDOW_SIZE = 512
VAD_THRESHOLD = 0.30

MIN_SPEECH_DURATION_MS = 600
MIN_SILENCE_DURATION_MS = 500
MIN_SPEECH_ENERGY_THRESHOLD = 0.005

SEGMENT_OVERLAP_TAIL_MS = 200

SPEAKER_SIMILARITY_THRESHOLD = 0.5
MAX_SPEAKERS = 10

SPEAKER_TOP_GAP_THRESHOLD = 0.03
SPEAKER_SHORT_AUDIO_THRESHOLD_MS = 2000
SPEAKER_STRICT_GAP_THRESHOLD = 0.03

DENOISE_ENABLED = True
DENOISE_BACKEND = "rnnoise"


# ============== 数据结构（对齐 InsightEye）==============

@dataclass
class TranscriptDelta:
    text: str = ""
    is_final: bool = False
    start_ms: int = 0
    end_ms: int = 0
    speaker_id: Optional[str] = None
    speaker_name: Optional[str] = None
    speaker_label: Optional[str] = None
    speaker_confidence: float = 0.0
    segment_reason: Optional[str] = None
    speaker_candidates: Optional[List[Tuple[str, str, float]]] = None
    registered_speaker_sims: Optional[Dict[str, float]] = None
    recognized_role: Optional[str] = None
    interviewer_sim: float = 0.0
    candidate_sim: float = 0.0
    uncertain_speaker: bool = False
    speaker_uncertain_reason: Optional[str] = None
    speaker_multi_window_stats: Optional[dict] = None
    corrected_text: Optional[str] = None
    was_corrected: bool = False
    correction_errors: Optional[List] = None


@dataclass
class SpeechSegment:
    audio_data: np.ndarray
    start_ms: int
    end_ms: int
    segment_reason: str = "unknown"
    overlap_tail: Optional[np.ndarray] = None
    speaker_id: Optional[str] = None
    speaker_label: Optional[str] = None
    speaker_name: Optional[str] = None
    speaker_uncertain: bool = False
    speaker_uncertain_reason: Optional[str] = None
    speaker_sims: Optional[Dict[str, float]] = None
    embedding: Optional[np.ndarray] = None
    _pending: bool = True  # 是否仍在缓冲区中等待提交（防并发重复提交）


# ============== 流式 VAD（对齐 InsightEye StreamingVAD）==============

class StreamingVAD:
    """
    流式 VAD 检测器
    对齐 InsightEye app/streaming_pipeline.py StreamingVAD
    """

    def __init__(self, vad_model, sample_rate: int = 16000):
        self.vad_model = vad_model
        self.sample_rate = sample_rate
        self.window_samples = VAD_WINDOW_SIZE
        self.reset()
        self.min_speech_samples = int(MIN_SPEECH_DURATION_MS * sample_rate / 1000)
        self.min_silence_samples = int(MIN_SILENCE_DURATION_MS * sample_rate / 1000)
        self.min_energy_threshold = MIN_SPEECH_ENERGY_THRESHOLD

        self._buffer: np.ndarray = np.array([], dtype=np.float32)
        self._total_samples_processed = 0

        self._embedding_buffer: List[np.ndarray] = []
        self._embedding_timestamps_ms: List[int] = []
        self._last_embedding_extract_ms = 0
        self._embedding_extractor = None
        self._embedding_extraction_interval_samples = int(
            CHANGE_DETECTOR_CONFIG.embedding_interval_ms * sample_rate / 1000
        )
        self._pending_audio_lock = threading.Lock()
        self._embedding_lock = threading.Lock()
        self._last_change_detected_ms = -999999
        self._pending_audio_for_embedding: List[np.ndarray] = []
        self._prev_segment_tail: Optional[np.ndarray] = None

        # 说话人变化冷却期（防止碎片化分段）
        # 检测到换人后，等待一段时间再允许下一次强制分段
        self._speaker_change_cooldown_until_ms: int = 0
        self._speaker_change_cooldown_ms: int = 1500  # 冷却 1.5 秒
        self._last_confirmed_speaker_label: Optional[str] = None  # 冷却期内记录当前说话人

    def reset(self):
        self.state = "idle"
        self.speech_buffer: List[np.ndarray] = []
        self.speech_start_sample = 0
        self.silence_samples = 0
        self._buffer = np.array([], dtype=np.float32)
        self._total_samples_processed = 0
        self._embedding_buffer = []
        self._embedding_timestamps_ms = []
        self._last_embedding_extract_ms = 0
        self._last_change_detected_ms = -999999
        self._pending_audio_for_embedding = []
        self._prev_segment_tail: Optional[np.ndarray] = None
        self._speaker_change_cooldown_until_ms = 0
        self._last_confirmed_speaker_label = None

    def feed(self, audio_chunk: np.ndarray) -> Optional[SpeechSegment]:
        self._buffer = np.concatenate([self._buffer, audio_chunk])

        while len(self._buffer) >= VAD_WINDOW_SIZE:
            chunk = self._buffer[:VAD_WINDOW_SIZE]
            self._buffer = self._buffer[VAD_WINDOW_SIZE:]

            is_speech = self._detect_speech(chunk)

            if is_speech and self._embedding_extractor is not None:
                self._pending_audio_for_embedding.append(chunk.copy())

            result = self._update_state(is_speech, chunk)
            if result:
                return result

            self._total_samples_processed += VAD_WINDOW_SIZE

        return None

    def _detect_speech(self, chunk: np.ndarray) -> bool:
        energy = np.mean(chunk ** 2)
        if energy < self.min_energy_threshold:
            return False

        if self.vad_model is None:
            return True

        try:
            tensor = torch.from_numpy(chunk).float().unsqueeze(0)
            model_device = next(self.vad_model.parameters(), torch.zeros(0, device='cpu')).device
            tensor = tensor.to(model_device)
            prob = self.vad_model(tensor, self.sample_rate).item()
            return prob > VAD_THRESHOLD
        except Exception as e:
            print(f"[VAD] 检测失败: {e}")
            return False

    def set_embedding_extractor(self, extractor):
        self._embedding_extractor = extractor

    def extract_pending_embeddings(self) -> List[np.ndarray]:
        if self._embedding_extractor is None:
            return []

        with self._pending_audio_lock:
            if not self._pending_audio_for_embedding:
                return []

            audio = np.concatenate(self._pending_audio_for_embedding)
            self._pending_audio_for_embedding = []

            extracted = []
            interval = self._embedding_extraction_interval_samples
            while len(audio) >= interval:
                chunk = audio[:interval]
                audio = audio[interval:]

                try:
                    emb = self._embedding_extractor.extract(chunk)
                    if emb is not None and len(emb) > 0:
                        emb_norm = np.linalg.norm(emb)
                        if emb_norm < 1e-6:
                            print(f"[VAD] 跳过零向量 embedding (norm={emb_norm:.2e})")
                            continue
                        ts = int(self._total_samples_processed * 1000 / self.sample_rate)
                        with self._embedding_lock:
                            self._embedding_buffer.append(emb)
                            self._embedding_timestamps_ms.append(ts)
                        extracted.append(emb)
                        self._last_embedding_extract_ms = ts
                except Exception as e:
                    print(f"[VAD] Embedding 提取失败: {e}")

            if len(audio) > 0:
                self._pending_audio_for_embedding.insert(0, audio)

            return extracted

    def _find_changepoint_offline(
        self,
        timestamps_ms: np.ndarray,
        distances: np.ndarray,
        start_ms: int,
        end_ms: int,
    ) -> Tuple[int, int]:
        cfg = CHANGE_DETECTOR_CONFIG
        if not cfg.offline_changepoint or len(distances) < 4:
            return start_ms, end_ms

        n = len(distances)
        step_ms = cfg.embedding_interval_ms

        def binary_seg(data: np.ndarray, penalty: float) -> List[int]:
            def cost(seg: np.ndarray) -> float:
                if len(seg) < 2:
                    return 0.0
                return float(np.var(seg)) * len(seg)

            def find_single_cp(seq: np.ndarray, start: int, stop: int) -> Tuple[float, int]:
                best_gain = 0.0
                best_cp = -1
                total_cost = cost(seq[start:stop])
                if total_cost <= 0:
                    return 0.0, -1
                for t in range(start + 2, stop - 2):
                    left = seq[start:t]
                    right = seq[t:stop]
                    gain = total_cost - cost(left) - cost(right) - penalty
                    if gain > best_gain:
                        best_gain = gain
                        best_cp = t
                return best_gain, best_cp

            cps = []
            stack = [(0, n)]
            while stack:
                start_, stop_ = stack.pop()
                gain, cp = find_single_cp(data, start_, stop_)
                if cp < 0:
                    continue
                cps.append(cp)
                stack.append((start_, cp))
                stack.append((cp, stop_))
            cps.sort()
            return cps

        cps = binary_seg(distances, cfg.cp_penalty)
        if not cps:
            return start_ms, end_ms

        cp_idx = cps[-1]
        cp_ms = int(timestamps_ms[cp_idx])
        cp_offset_from_start = cp_ms - start_ms
        if cp_offset_from_start < 0:
            cp_offset_from_start = 0

        trim_start_sample = int(cp_offset_from_start / 1000 * self.sample_rate)
        trim_start_ms = start_ms + trim_start_sample
        new_duration_ms = end_ms - trim_start_ms
        if new_duration_ms < cfg.cp_min_segment_ms:
            return start_ms, end_ms

        return trim_start_ms, end_ms

    def _detect_speaker_change_by_embedding(self) -> Tuple[bool, Optional[dict]]:
        cfg = CHANGE_DETECTOR_CONFIG

        with self._embedding_lock:
            emb_buffer = list(self._embedding_buffer)
            ts_list = list(self._embedding_timestamps_ms)

        n = len(emb_buffer)
        if n < cfg.embedding_min_for_detection:
            return False, None

        timestamps_arr = np.array(ts_list)
        elapsed_ms = int(timestamps_arr[-1] - timestamps_arr[0])
        if elapsed_ms < cfg.warmup_duration_ms:
            return False, None

        norms = np.array([np.linalg.norm(e) for e in emb_buffer])
        valid_mask = norms > 1e-6
        embs_raw = np.array(emb_buffer)[valid_mask]
        embs_valid = np.array([
            e / (np.linalg.norm(e) + 1e-8) for e in embs_raw
        ])
        timestamps_valid = np.array(ts_list)[valid_mask]

        if len(embs_valid) < cfg.embedding_min_for_detection:
            return False, None

        segment_duration_ms = int(timestamps_arr[-1] - timestamps_arr[0])
        if segment_duration_ms < cfg.min_segment_duration_ms:
            return False, None

        current_time_ms = int(timestamps_valid[-1])
        if current_time_ms - self._last_change_detected_ms < cfg.min_change_interval_ms:
            return False, None

        n_v = len(embs_valid)

        global_reference = np.median(embs_valid, axis=0)
        global_reference = global_reference / (np.linalg.norm(global_reference) + 1e-8)

        global_distances = np.array([
            float(1.0 - np.clip(np.dot(emb, global_reference), -1.0, 1.0))
            for emb in embs_valid
        ])

        if cfg.use_sliding_window and n_v >= cfg.sliding_window_size:
            window_size = cfg.sliding_window_size
            window_start = max(0, n_v - window_size - cfg.change_confirm_count)
            window_embs = embs_valid[window_start:window_start + window_size]
            window_ref = np.median(window_embs, axis=0)
            window_ref = window_ref / (np.linalg.norm(window_ref) + 1e-8)

            window_distances = np.array([
                float(1.0 - np.clip(np.dot(emb, window_ref), -1.0, 1.0))
                for emb in embs_valid
            ])
        else:
            window_distances = global_distances
            window_ref = global_reference

        pairwise_dists = []
        for i in range(n_v):
            for j in range(i + 1, n_v):
                d = float(1.0 - np.clip(np.dot(embs_valid[i], embs_valid[j]), -1.0, 1.0))
                pairwise_dists.append(d)
        pairwise_dists = np.array(pairwise_dists)
        max_pairwise = float(np.max(pairwise_dists)) if len(pairwise_dists) > 0 else 0.0

        mean_d = float(np.mean(window_distances))
        std_d = float(np.std(window_distances)) + 1e-8

        if cfg.use_dynamic_threshold:
            dynamic_threshold = mean_d + cfg.dynamic_factor * std_d
            threshold = max(cfg.change_distance_threshold, dynamic_threshold)
            threshold = min(threshold, 0.85)
        else:
            threshold = cfg.change_distance_threshold

        confirm_count = cfg.change_confirm_count
        if n_v < confirm_count + 2:
            return False, None

        recent_distances = window_distances[-confirm_count:]

        jump_detected = False
        if cfg.detect_jump_only and len(window_distances) >= 3:
            baseline = np.mean(window_distances[:-confirm_count]) if len(window_distances) > confirm_count + 1 else mean_d
            recent_high = recent_distances - baseline
            jump_count = sum(1 for d in recent_high if d > cfg.jump_threshold)
            early_mean = np.mean(window_distances[:-confirm_count]) if len(window_distances) > confirm_count + 1 else mean_d
            recent_mean = np.mean(recent_distances)
            is_jump = (recent_mean - early_mean) > cfg.jump_threshold * 1.2
            cluster_evidence = max_pairwise > cfg.jump_threshold * 1.5
            jump_detected = (jump_count >= confirm_count) and is_jump and cluster_evidence
        else:
            all_exceed = all(d > threshold for d in recent_distances)
            recent_mean = float(np.mean(recent_distances))
            recent_exceeds_mean = recent_mean > mean_d + 0.03
            cluster_bimodal = max_pairwise > threshold * 0.8
            jump_detected = all_exceed and recent_exceeds_mean and cluster_bimodal

        global_recent = global_distances[-confirm_count:]
        global_mean = float(np.mean(global_distances))
        global_exceed = np.mean(global_recent) > global_mean + 0.05
        global_threshold = global_mean + 0.4 * float(np.std(global_distances))
        global_threshold = min(global_threshold, 0.70)
        global_confirmed = np.mean(global_recent) > global_threshold

        change_confirmed = jump_detected and (global_exceed or global_confirmed)

        if change_confirmed:
            self._last_change_detected_ms = current_time_ms

            emb_distances = list(zip(
                [int(t) for t in timestamps_valid],
                [float(d) for d in window_distances]
            ))

            first_drift = None
            for ts, d in emb_distances:
                if d > threshold:
                    first_drift = (ts, d)
                    break

            early_dist = np.mean(window_distances[:-confirm_count]) if len(window_distances) > confirm_count + 2 else mean_d
            jump_magnitude = float(np.mean(recent_distances) - early_dist)

            print(f"[VAD-DBG] *** SPEAKER CHANGE *** 检测时刻={current_time_ms}ms")
            print(f"[VAD-DBG]   跳变检测: recent_mean={np.mean(recent_distances):.3f}, "
                  f"baseline={early_dist:.3f}, jump_mag={jump_magnitude:.3f}, "
                  f"jump_threshold={cfg.jump_threshold:.3f}")
            print(f"[VAD-DBG]   阈值: window_thr={threshold:.3f}, global_thr={global_threshold:.3f}")
            print(f"[VAD-DBG]   双簇证据: pair_max={max_pairwise:.3f}, threshold={threshold:.3f}")
            print(f"[VAD-DBG]   embedding 分布: 共{len(embs_valid)}个, "
                  f"最早={int(timestamps_valid[0])}ms, 最新={int(timestamps_valid[-1])}ms, "
                  f"跨度={int(timestamps_valid[-1]-timestamps_valid[0])}ms")

            debug_info = {
                "current_time_ms": current_time_ms,
                "segment_duration_ms": segment_duration_ms,
                "d": float(recent_distances[-1]),
                "mean_d": float(mean_d),
                "pair_max": float(max_pairwise),
                "threshold": float(threshold),
                "valid_count": len(embs_valid),
                "first_drift": first_drift,
                "emb_distances": emb_distances,
                "timestamps_first": int(timestamps_arr[0]),
                "timestamps_last": int(timestamps_arr[-1]),
                "total_samples_in_buffer": int(self._total_samples_processed - self.speech_start_sample),
                "speech_start_sample": int(self.speech_start_sample),
                "total_processed": int(self._total_samples_processed),
                "confirm_count": int(confirm_count),
                "jump_magnitude": jump_magnitude,
                "baseline": early_dist,
                "distances": window_distances.tolist(),
                "timestamps_ms_arr": timestamps_valid.tolist(),
                "global_distances": global_distances.tolist(),
                "global_threshold": float(global_threshold),
            }
            return True, debug_info

        return False, None

    def _update_state(self, is_speech: bool, chunk: np.ndarray) -> Optional[SpeechSegment]:
        if self.state == "idle":
            if is_speech:
                self.state = "speech"
                self.speech_start_sample = self._total_samples_processed
                self.speech_buffer = []
                self.silence_samples = 0
                self.speech_buffer.append(chunk)
                print(f"[VAD] 语音开始 (样本={self.speech_start_sample})")

        elif self.state == "speech":
            self.speech_buffer.append(chunk)

            current_time_ms = int(self._total_samples_processed * 1000 / self.sample_rate)
            is_in_cooldown = current_time_ms < self._speaker_change_cooldown_until_ms

            voice_changed, change_info = self._detect_speaker_change_by_embedding()
            if voice_changed and len(self.speech_buffer) >= 3:
                if is_in_cooldown:
                    # 冷却期内检测到变化：抑制分段，继续累积音频
                    print(f"[VAD-COOL] ⏸️ 冷却期内 [{current_time_ms}ms < {self._speaker_change_cooldown_until_ms}ms]，"
                          f"抑制分段，继续缓冲")
                else:
                    total_samples = sum(len(x) for x in self.speech_buffer)
                    audio_data = np.concatenate(self.speech_buffer)
                    start_ms = int(self.speech_start_sample * 1000 / self.sample_rate)
                    end_ms = int((self.speech_start_sample + total_samples) * 1000 / self.sample_rate)

                    info = change_info or {}
                    change_det_ms = info.get("current_time_ms", 0)
                    first_drift = info.get("first_drift")
                    valid_count = info.get("valid_count", 0)
                    d_val = info.get("d", 0)
                    mean_d = info.get("mean_d", 0)
                    pair_max = info.get("pair_max", 0)
                    threshold = info.get("threshold", 0)
                    emb_distances = info.get("emb_distances", [])
                    distances = info.get("distances", [])
                    timestamps_cp = info.get("timestamps_ms_arr", [])

                    change_offset_from_start = change_det_ms - start_ms

                    print(f"[VAD-DBG] >>> 强制分段详情 <<<")
                    print(f"[VAD-DBG]   Buffer: {len(self.speech_buffer)}块/{total_samples}样本/{total_samples/self.sample_rate:.2f}s, ASR: [{start_ms}-{end_ms}ms]")
                    print(f"[VAD-DBG]   首个显著偏离: {first_drift}")
                    if emb_distances:
                        print(f"[VAD-DBG]   各embedding距离: {[(f'{t}ms:{d:.3f}') for t, d in emb_distances]}")
                    print(f"[VAD-DBG]   检测参数: d={d_val:.3f}, mean={mean_d:.3f}, pair_max={pair_max:.3f}, thr={threshold:.3f}")

                    _raw_start_ms = start_ms
                    _raw_end_ms = end_ms
                    _trimmed = False
                    audio_data_trimmed = audio_data

                    if CHANGE_DETECTOR_CONFIG.offline_changepoint and len(distances) >= 4 and len(timestamps_cp) == len(distances):
                        ts_arr = np.array(timestamps_cp, dtype=np.int64)
                        d_arr = np.array(distances, dtype=np.float64)
                        trim_start_ms, trim_end_ms = self._find_changepoint_offline(
                            ts_arr, d_arr, start_ms, end_ms
                        )
                        if trim_start_ms > start_ms:
                            trim_sample = int((trim_start_ms - start_ms) / 1000 * self.sample_rate)
                            audio_data_trimmed = audio_data[trim_sample:]
                            trim_ms = trim_start_ms - start_ms
                            _trimmed = True
                            start_ms = trim_start_ms
                            overlap_samples = int(SEGMENT_OVERLAP_TAIL_MS * self.sample_rate / 1000)
                            overlap_tail = audio_data_trimmed[-overlap_samples:] if len(audio_data_trimmed) >= overlap_samples else audio_data_trimmed
                            self._prev_segment_tail = overlap_tail.copy()
                            print(f"[VAD-CP] ✅ 后处理变点修正: trim前=[{_raw_start_ms}-{_raw_end_ms}ms] → trim后=[{start_ms}-{end_ms}ms] (丢弃{trim_ms:.0f}ms/{trim_sample}样本)")
                        else:
                            overlap_samples = int(SEGMENT_OVERLAP_TAIL_MS * self.sample_rate / 1000)
                            overlap_tail = audio_data[-overlap_samples:] if total_samples >= overlap_samples else audio_data
                            self._prev_segment_tail = overlap_tail.copy()
                            print(f"[VAD-CP] ⏭️  无更优变化点，保持原始分段")
                    else:
                        overlap_samples = int(SEGMENT_OVERLAP_TAIL_MS * self.sample_rate / 1000)
                        overlap_tail = audio_data[-overlap_samples:] if total_samples >= overlap_samples else audio_data
                        self._prev_segment_tail = overlap_tail.copy()

                    first_emb_ts = emb_distances[0][0] if emb_distances else change_det_ms
                    first_drift_d = first_drift[1] if first_drift else 0.0
                    buffer_total_ms = total_samples / self.sample_rate * 1000
                    pollution_ms = end_ms - change_det_ms
                    pollution_pct = pollution_ms / buffer_total_ms * 100 if buffer_total_ms > 0 else 0
                    _confirm = info.get("confirm_count", 0)
                    if _trimmed:
                        print(f"[VAD-POLLUTION] ⚠️ 分段=[{start_ms}-{end_ms}ms](已修正) | "
                              f"Buffer原始={_raw_start_ms}-{_raw_end_ms}ms | "
                              f"实时检测={change_det_ms}ms | "
                              f"混入新说话人≈{pollution_ms:.0f}ms({pollution_pct:.0f}%)")
                    else:
                        print(f"[VAD-POLLUTION] ⚠️ 分段=[{start_ms}-{end_ms}ms] | "
                              f"Buffer时长={buffer_total_ms:.0f}ms | "
                              f"VAD检测={change_det_ms}ms(偏{change_offset_from_start:.0f}ms) | "
                              f"混入新说话人≈{pollution_ms:.0f}ms({pollution_pct:.0f}%) | "
                              f"首个embedding@{first_emb_ts}ms,d={first_drift_d:.3f} | "
                              f"embedding确认需{_confirm}帧≈{_confirm * 320}ms滞后")

                    print(f"[VAD] 检测到说话人变化，强制分段 ({start_ms}-{end_ms}ms, 原因=声纹变化)")
                    self.speech_buffer = []
                    self.speech_start_sample = self._total_samples_processed
                    self.silence_samples = 0
                    self._embedding_buffer.clear()
                    self._embedding_timestamps_ms.clear()

                    # 激活冷却期：接下来 3 秒内不允许再次强制分段
                    self._speaker_change_cooldown_until_ms = current_time_ms + self._speaker_change_cooldown_ms
                    print(f"[VAD-COOL] 🔒 激活冷却期: {self._speaker_change_cooldown_until_ms}ms (持续 {self._speaker_change_cooldown_ms}ms)")

                    return SpeechSegment(
                        audio_data=audio_data_trimmed,
                        start_ms=start_ms,
                        end_ms=end_ms,
                        segment_reason="voice_change",
                        overlap_tail=self._prev_segment_tail,
                    )

            if is_speech:
                self.silence_samples = 0
            else:
                self.silence_samples += VAD_WINDOW_SIZE
                if self.silence_samples >= self.min_silence_samples:
                    total_samples = sum(len(x) for x in self.speech_buffer)
                    if total_samples >= self.min_speech_samples:
                        audio_data = np.concatenate(self.speech_buffer)
                        start_ms = int(self.speech_start_sample * 1000 / self.sample_rate)
                        end_ms = int((self.speech_start_sample + total_samples) * 1000 / self.sample_rate)
                        silence_dur = int(self.silence_samples * 1000 / self.sample_rate)
                        overlap_samples = int(SEGMENT_OVERLAP_TAIL_MS * self.sample_rate / 1000)
                        overlap_tail = audio_data[-overlap_samples:] if total_samples >= overlap_samples else audio_data
                        self._prev_segment_tail = overlap_tail.copy()

                        print(f"[VAD] 静音超时结束 ({start_ms}-{end_ms}ms, 静音={silence_dur}ms)")
                        self.state = "idle"
                        self.speech_buffer = []
                        self.speech_start_sample = 0
                        self.silence_samples = 0
                        self._embedding_buffer.clear()
                        self._embedding_timestamps_ms.clear()
                        return SpeechSegment(
                            audio_data=audio_data,
                            start_ms=start_ms,
                            end_ms=end_ms,
                            segment_reason="silence_timeout",
                            overlap_tail=self._prev_segment_tail,
                        )
                    else:
                        print(f"[VAD] 语音太短，忽略 ({total_samples} samples)")
                        self.state = "idle"
                        self.speech_buffer = []
                        self.speech_start_sample = 0
                        self.silence_samples = 0
                        self._embedding_buffer.clear()
                        self._embedding_timestamps_ms.clear()

        return None


# ============== 流式 ASR（对齐 InsightEye StreamingASR）==============

class StreamingASR:
    def __init__(self, asr_model):
        self.asr_model = asr_model

    def recognize(
        self,
        audio_data: np.ndarray,
        on_delta: Callable[["TranscriptDelta"], None],
        language: str = "zh",
    ):
        try:
            result = self.asr_model.generate(
                input=audio_data,
                batch_size_s=300,
                is_streaming=True,
                language=language,
            )

            for item in result:
                is_final = True
                text = None
                if isinstance(item, dict):
                    text = item.get("text", "")
                elif isinstance(item, str):
                    text = item.strip()

                if text:
                    print(f"[ASR] 转录增量: {text}")
                    on_delta(TranscriptDelta(
                        text=text,
                        is_final=is_final,
                        start_ms=0,
                        end_ms=0,
                    ))

        except Exception as e:
            print(f"[ASR] 识别失败: {e}")
            import traceback
            traceback.print_exc()


# ============== 流式声纹识别（对齐 InsightEye StreamingSpeakerRecognition）==============

class StreamingSpeakerRecognition:
    def __init__(self, camp_model, device="cuda"):
        self.camp_model = camp_model
        self.extractor = SpeakerEmbeddingExtractor(camp_model, device=device) if camp_model else None

    def extract_and_compare(
        self,
        audio_data: np.ndarray,
        registered_embeddings: dict,
        threshold: float = 0.5,
    ) -> Optional[Tuple[str, float, Dict[str, float]]]:
        if not registered_embeddings:
            print("[声纹] 没有已注册的说话人，无法进行声纹识别")
            return None
        if self.extractor is None:
            return None

        try:
            n_reg = len(registered_embeddings)
            print(f"[声纹] 开始识别，音频长度={len(audio_data)/16000:.2f}秒，已注册: {n_reg}人")

            embedding = self.extractor.extract(audio_data)
            emb_norm = np.linalg.norm(embedding)

            if emb_norm < 1e-6:
                print(f"[声纹] 警告: 检测到零向量 embedding (norm={emb_norm:.2e})，识别结果不可靠，返回空匹配")
                return ("__unknown__", 0.0, {})

            print(f"[声纹] 声纹特征: L2={emb_norm:.4f}, 维度={len(embedding)}")

            best_match = None
            best_score = 0
            all_sims = {}

            for speaker_id, registered_emb in registered_embeddings.items():
                cos_sim = np.dot(embedding, registered_emb) / (
                    np.linalg.norm(embedding) * np.linalg.norm(registered_emb)
                )
                score = (cos_sim + 1.0) / 2.0
                all_sims[speaker_id] = float(score)

                if n_reg <= 10 or score >= threshold:
                    print(f"[声纹] 与 {speaker_id} 相似度: cos={cos_sim:.3f}, score={score:.3f}")

                if score > best_score:
                    best_score = score
                    best_match = speaker_id

            if best_match:
                print(f"[声纹] 识别结果: {best_match} (score={best_score:.3f})")
                return (best_match, float(best_score), all_sims)
            return None

        except Exception as e:
            print(f"[声纹] 声纹识别异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def extract_topk_and_compare(
        self,
        audio_data: np.ndarray,
        registered_embeddings: dict,
        top_k: int = 3,
        threshold: float = 0.60,
    ) -> Optional[Tuple[List[Tuple[str, float]], Dict[str, float]]]:
        try:
            if not registered_embeddings:
                print("[声纹-topk] 没有已注册的说话人")
                return None

            embedding = self.extractor.extract(audio_data)
            emb_norm = np.linalg.norm(embedding)
            if emb_norm < 1e-6:
                print(f"[声纹-topk] 警告: 零向量 embedding，topk 结果不可靠，返回空")
                return ([], {})

            all_sims = {}
            for speaker_id, registered_emb in registered_embeddings.items():
                cos_sim = np.dot(embedding, registered_emb) / (
                    np.linalg.norm(embedding) * np.linalg.norm(registered_emb)
                )
                score = (cos_sim + 1.0) / 2.0
                all_sims[speaker_id] = float(score)

            sorted_speakers = sorted(all_sims.items(), key=lambda x: x[1], reverse=True)
            topk = [(sid, score) for sid, score in sorted_speakers[:top_k]
                    if score >= threshold]

            print(f"[声纹-topk] 音频长度={len(audio_data)/16000:.2f}秒，top-{len(topk)}: {topk}")
            return (topk, all_sims)

        except Exception as e:
            print(f"[声纹-topk] 识别异常: {e}")
            import traceback
            traceback.print_exc()
            return None

    def extract_multi_window(
        self,
        audio_data: np.ndarray,
        n_windows: int = 3,
        window_step_ratio: float = 0.25,
    ) -> list[tuple[np.ndarray, float, bool]]:
        if self.extractor is None:
            return []
        return self.extractor.extract_multi_window(audio_data, n_windows, window_step_ratio)

    def extract_fused(
        self,
        audio_data: np.ndarray,
        n_windows: int = 3,
        window_step_ratio: float = 0.25,
        fusion_method: str = "mean",
    ) -> tuple[np.ndarray, dict]:
        if self.extractor is None:
            raise RuntimeError("extractor 未初始化")
        return self.extractor.extract_fused(audio_data, n_windows, window_step_ratio, fusion_method)


# ============== 流式管道（对齐 InsightEye StreamingPipeline）==============

class StreamingPipeline:
    _corrector = None

    def __init__(
        self,
        vad_model,
        asr_model,
        punc_model=None,
        camp_model=None,
        language: str = "zh",
        device: str = "cuda",
        use_correction: bool = False,
    ):
        self.vad = StreamingVAD(vad_model)
        self.asr = StreamingASR(asr_model)
        self.punc_model = punc_model
        self.speaker = StreamingSpeakerRecognition(camp_model, device=device) if camp_model else None
        self.language = language
        self.device = device
        self.use_correction = use_correction

        self._denoiser_initialized = False
        self._denoiser = None
        self._corrector_initialized = False

        if camp_model is not None:
            self._embedding_extractor = SpeakerEmbeddingExtractor(camp_model, device=device)
            self.vad.set_embedding_extractor(self._embedding_extractor)
        else:
            self._embedding_extractor = None

        self._embedding_thread_running = False
        self._embedding_thread: Optional[threading.Thread] = None

        self.on_transcript: Optional[Callable] = None
        self.on_speaker: Optional[Callable] = None
        self.on_speech_segment: Optional[Callable] = None

        self._registered_speakers: Dict[str, np.ndarray] = {}
        self._use_enhanced_engine = False
        self._enhanced_registry = None

        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._transcript_queue: queue.Queue = queue.Queue(maxsize=100)
        self._process_task: Optional[asyncio.Task] = None
        self._asr_lock = asyncio.Lock()

        self._speaker_counter = 0

        self._multi_window_enabled = False
        self._multi_window_n: int = 3
        self._multi_window_step: float = 0.25
        self._multi_window_vote: str = "score_weighted"

        self._pending_segments: List[SpeechSegment] = []
        self._pending_lock = threading.Lock()
        self._merge_enabled: bool = True
        self._merge_max_wait_ms: float = 10000.0
        self._merge_min_same_label: int = 2
        self._merge_min_duration_ms: float = 500.0
        self._last_speaker_label: Optional[str] = None

        self._track_counter = 0
        self._track_lock = threading.Lock()

        self._inactivity_timeout_ms: float = 4000.0
        self._last_audio_time: float = time.time()
        self._inactivity_task: Optional[asyncio.Task] = None
        self._inactivity_running: bool = False

        self._segment_stats = {
            "silence_timeout": 0,
            "voice_change": 0,
            "merged_submit": 0,
            "timeout_submit": 0,
            "total_segments": 0,
            "total_merged": 0,
        }

        print("[管道] 说话人标签合并逻辑已启用")

    def _init_denoiser(self):
        if not DENOISE_ENABLED:
            return
        if self._denoiser_initialized:
            return

        try:
            from app.asr.audio_denoiser import get_denoiser
            self._denoiser = get_denoiser(backend=DENOISE_BACKEND)
            self._denoiser_initialized = True
            if self._denoiser.is_available():
                print(f"[去噪] 去噪器已初始化，后端: {DENOISE_BACKEND}")
            else:
                print("[去噪] 去噪器不可用，跳过去噪处理")
                self._denoiser = None
        except Exception as e:
            print(f"[去噪] 去噪器初始化失败: {e}")
            self._denoiser_initialized = True
            self._denoiser = None

    def _apply_denoise(self, audio: np.ndarray, sample_rate: int = 16000) -> np.ndarray:
        if not DENOISE_ENABLED or self._denoiser is None:
            return audio
        if not self._denoiser_initialized:
            self._init_denoiser()
        if self._denoiser is None:
            return audio
        try:
            return self._denoiser.denoise(audio, sample_rate)
        except Exception as e:
            print(f"[去噪] 去噪处理失败: {e}")
            return audio

    def _init_corrector(self):
        if not self.use_correction:
            return
        if self._corrector_initialized:
            return
        try:
            from app.text_corrector import get_corrector
            self._corrector = get_corrector()
            self._corrector_initialized = True
            print("[MacBERT纠错] 纠错器已初始化")
        except Exception as e:
            print(f"[MacBERT纠错] 纠错器初始化失败: {e}")
            self._corrector_initialized = True
            self._corrector = None

    def _apply_macbert_correction(self, delta: TranscriptDelta) -> TranscriptDelta:
        if not self.use_correction or not delta.text or not delta.text.strip():
            return delta
        if not self._corrector_initialized:
            self._init_corrector()
        if self._corrector is None:
            return delta
        try:
            original_text = delta.text
            result = self._corrector.correct(original_text)
            if result.get("corrected", False):
                delta.corrected_text = result.get("target", original_text)
                delta.was_corrected = True
                delta.correction_errors = result.get("errors", [])
                errors = result.get("errors", [])
                if errors:
                    error_str = ", ".join([f"'{e[0]}'→'{e[1]}'" for e in errors[:3]])
                    print(f"[MacBERT纠错] '{original_text[:40]}...' → '{delta.corrected_text[:40]}...' | 纠错: {error_str}")
                else:
                    print(f"[MacBERT纠错] '{original_text[:40]}...' → '{delta.corrected_text[:40]}...'")
        except Exception as e:
            print(f"[MacBERT纠错] 纠错失败: {e}")
        return delta

    def register_speaker(self, speaker_id: str, embedding: np.ndarray,
                         name: Optional[str] = None, role: Optional[str] = None):
        self._registered_speakers[speaker_id] = embedding
        print(f"[管道] 已注册说话人: {speaker_id}({name or 'unknown'}), "
              f"当前共 {len(self._registered_speakers)} 位: {list(self._registered_speakers.keys())}")

        # 同时注册到增强引擎（这样 EnhancedIdentificationResult 会携带 name）
        if self._use_enhanced_engine and self._enhanced_registry:
            self._enhanced_registry.register_embedding(speaker_id, embedding, name=name, role=role)

    def unregister_speaker(self, speaker_id: str) -> bool:
        if speaker_id in self._registered_speakers:
            del self._registered_speakers[speaker_id]
            print(f"[管道] 已注销说话人: {speaker_id}, 剩余 {len(self._registered_speakers)} 位")
            return True
        return False

    def set_enhanced_registry(self, registry):
        if not hasattr(self, '_enhanced_registry') or self._enhanced_registry is None:
            self._use_enhanced_engine = True
            print("[管道] 已接入增强版声纹引擎（cascade matching + dynamic threshold）")
        self._enhanced_registry = registry

    def set_multi_window(self, enabled: bool = True, n_windows: int = 3,
                         window_step_ratio: float = 0.25, vote_method: str = "score_weighted"):
        self._multi_window_enabled = enabled
        self._multi_window_n = n_windows
        self._multi_window_step = window_step_ratio
        self._multi_window_vote = vote_method
        mode_str = "enabled" if enabled else "disabled"
        print(f"[管道] 多窗口投票: {mode_str}, n={n_windows}, step_ratio={window_step_ratio}, method={vote_method}")

    def set_merge_strategy(self, enabled: bool = True, max_wait_ms: float = 5000.0,
                           min_same_label: int = 2, min_duration_ms: float = 500.0):
        self._merge_enabled = enabled
        self._merge_max_wait_ms = max_wait_ms
        self._merge_min_same_label = min_same_label
        self._merge_min_duration_ms = min_duration_ms
        print(f"[管道] 合并策略: enabled={enabled}, max_wait={max_wait_ms}ms")

    _SPEAKER_EMB_SIM_THRESHOLD: float = 0.65

    def _check_speaker_similarity_for_merge(
        self, segment: SpeechSegment, last_pending_label: str
    ) -> bool:
        last_segment = self._pending_segments[-1] if self._pending_segments else None
        if not last_segment or last_segment.embedding is None:
            print(f"[管道-合并] 🔍 声纹相似度回退检查: 缓冲区无 embedding，无法比较，判定为换人")
            return False

        if segment.embedding is None:
            print(f"[管道-合并] 🔍 声纹相似度回退检查: 新片段无 embedding，无法比较，判定为换人")
            return False

        emb_a = segment.embedding
        emb_b = last_segment.embedding
        norm_a = np.linalg.norm(emb_a)
        norm_b = np.linalg.norm(emb_b)
        if norm_a < 1e-6 or norm_b < 1e-6:
            print(f"[管道-合并] 🔍 声纹相似度回退检查: embedding 范数过小，无法比较，判定为换人")
            return False

        cos_sim = float(np.dot(emb_a, emb_b) / (norm_a * norm_b))
        print(f"[管道-合并] 🔍 声纹相似度回退检查: 片段 embedding 直接比较 ({last_pending_label} vs {segment.speaker_label}), 余弦相似度={cos_sim:.4f}, 阈值={self._SPEAKER_EMB_SIM_THRESHOLD}")

        if cos_sim >= self._SPEAKER_EMB_SIM_THRESHOLD:
            print(f"[管道-合并] 🔍 判定为同一人（相似度 {cos_sim:.4f} >= {self._SPEAKER_EMB_SIM_THRESHOLD}），继续合并")
            return True
        else:
            print(f"[管道-合并] 🔍 判定为换人（相似度 {cos_sim:.4f} < {self._SPEAKER_EMB_SIM_THRESHOLD}）")
            return False

    def _merge_segments(self, segments: List[SpeechSegment], speaker_label: Optional[str] = None) -> SpeechSegment:
        if not segments:
            raise ValueError("无法合并空片段列表")

        if len(segments) == 1:
            seg = segments[0]
            seg.speaker_label = speaker_label
            return seg

        merged_audio = np.concatenate([s.audio_data for s in segments])
        start_ms = segments[0].start_ms
        end_ms = segments[-1].end_ms
        overlap_tail = segments[-1].overlap_tail

        reasons = set(s.segment_reason for s in segments)
        merged_reason = "/".join(sorted(reasons))
        final_label = speaker_label or segments[-1].speaker_label or segments[-1].speaker_id
        speaker_id = segments[-1].speaker_id

        merged_segment = SpeechSegment(
            audio_data=merged_audio,
            start_ms=start_ms,
            end_ms=end_ms,
            segment_reason=merged_reason,
            overlap_tail=overlap_tail,
            speaker_id=speaker_id,
            speaker_label=final_label,
            speaker_name=segments[-1].speaker_name,
        )
        merged_segment._track_id = getattr(segments[-1], '_track_id', None)
        merged_segment.speaker_sims = segments[-1].speaker_sims
        merged_segment.embedding = segments[-1].embedding

        print(f"[管道-合并] 合并 {len(segments)} 个片段: "
              f"[{start_ms}-{end_ms}ms], 时长={(end_ms - start_ms)/1000:.2f}s, "
              f"标签={final_label}, 原因={merged_reason}")

        return merged_segment

    def _should_commit_segments(self, current_label: Optional[str]) -> Tuple[bool, List[SpeechSegment]]:
        if not self._pending_segments:
            return False, []

        if self._pending_segments:
            first_seg = self._pending_segments[0]
            last_seg = self._pending_segments[-1]
            wait_duration_ms = last_seg.end_ms - first_seg.start_ms
            if wait_duration_ms >= self._merge_max_wait_ms:
                print(f"[管道-合并] 等待超时 ({wait_duration_ms:.0f}ms >= {self._merge_max_wait_ms}ms)，强制提交")
                return True, list(self._pending_segments)

        last_pending_label = self._pending_segments[-1].speaker_label if self._pending_segments else None
        if current_label is not None and last_pending_label is not None and current_label != last_pending_label:
            print(f"[管道-合并] 标签变化: {last_pending_label} → {current_label}，提交之前的片段")
            return True, list(self._pending_segments)

        if len(self._pending_segments) >= self._merge_min_same_label:
            labels = [s.speaker_label for s in self._pending_segments if s.speaker_label is not None]
            if labels and all(l == labels[0] for l in labels):
                print(f"[管道-合并] 连续 {len(labels)} 个相同标签片段达标，提交")
                return True, list(self._pending_segments)

        return False, []

    def _add_segment_to_buffer(self, segment: SpeechSegment) -> Optional[List[SpeechSegment]]:
        """
        合并决策（使用 top3 同人规则替代 uncertain）。

        决策树：
        1. 缓冲区为空 → 头部碎片检查（片段太短则等待）
        2. 标签变化时 → 用 top3 同人规则判断是否同人
           - 新片段 top-3 包含缓冲区任意说话人 → 视为同人，继续合并
           - 不包含 → 进行短片段二次确认
        3. 短片段二次确认：如果新片段很短(<3s)，检查与缓冲区最后片段的声纹相似度
           - 相似度高 → 认为是尾部碎片，合并
           - 相似度低 → 确认换人
        4. 尾部残留检测：极短片段(<1s)直接丢弃
        5. 等待超时 → 强制提交缓冲区
        """
        # ── 阈值配置 ────────────────────────────────────────────────
        HEAD_FRAGMENT_THRESHOLD_MS = 1000   # 头部碎片：<1秒认为可能是碎片
        HEAD_MIN_BUFFER_MS = 3000          # 头部碎片：缓冲区达到3秒才提交首个片段
        SHORT_SEGMENT_THRESHOLD_MS = 3000   # 短片段二次确认阈值
        TAIL_RESIDUE_THRESHOLD_MS = 500    # 尾部残留：<0.5秒直接丢弃
        BUFFER_CONTENT_THRESHOLD_MS = 5000  # 短片段合并：缓冲区至少5秒

        with self._pending_lock:
            _is_timeout_submit = False
            _is_short_tail_submit = False
            last_pending_label = self._pending_segments[-1].speaker_label if self._pending_segments else None

            # 计算片段时长
            segment_duration_ms = segment.end_ms - segment.start_ms if segment.start_ms and segment.end_ms else len(segment.audio_data) / 16.0

            track_id = getattr(segment, '_track_id', 0)
            print(f"[📦合并][#{track_id:04d}] ═══ 合并决策 ═══")
            print(f"[📦合并][#{track_id:04d}] 新片段: {segment.speaker_label} | 时长: {segment_duration_ms:.0f}ms")

            # ── 尾部残留检测：极短片段直接丢弃 ──────────────────────────
            if segment_duration_ms < TAIL_RESIDUE_THRESHOLD_MS:
                print(f"[📦合并][#{track_id:04d}] 🗑️ 尾部残留检测: {segment_duration_ms:.0f}ms < {TAIL_RESIDUE_THRESHOLD_MS}ms，丢弃")
                return None

            # ── 缓冲区为空：头部碎片检查 ────────────────────────────────
            if not self._pending_segments:
                if segment_duration_ms < HEAD_FRAGMENT_THRESHOLD_MS:
                    # 片段太短，可能是头部碎片，先等待更多音频
                    self._pending_segments.append(segment)
                    self._last_speaker_label = segment.speaker_label
                    print(f"[📦合并][#{track_id:04d}] 🧊 头部碎片检查: {segment_duration_ms:.0f}ms < {HEAD_FRAGMENT_THRESHOLD_MS}ms，"
                          f"等待更多音频后再判断")
                    return None
                else:
                    # 片段足够长，直接加入并等待确认
                    self._pending_segments.append(segment)
                    self._last_speaker_label = segment.speaker_label
                    print(f"[📦合并][#{track_id:04d}] ✅ 片段足够长({segment_duration_ms:.0f}ms >= {HEAD_FRAGMENT_THRESHOLD_MS}ms)，加入缓冲区等待确认")
                    return None

            should_commit = False

            pending_ids = [f"#{getattr(s, '_track_id', '?'):04d}" for s in self._pending_segments]
            buffered_labels = [s.speaker_label for s in self._pending_segments if s.speaker_label]
            buffered_duration_ms = self._pending_segments[-1].end_ms - self._pending_segments[0].start_ms
            print(f"[📦合并][#{track_id:04d}] 缓冲区: {len(self._pending_segments)} 个 {pending_ids} "
                  f"| 缓冲说话人: {buffered_labels} | 缓冲时长: {buffered_duration_ms:.0f}ms")

            # ── 头部碎片提交检查：缓冲区首个片段太短且现在有了足够的缓冲内容 ──
            if len(self._pending_segments) == 1:
                first_seg = self._pending_segments[0]
                first_duration_ms = first_seg.end_ms - first_seg.start_ms if first_seg.start_ms and first_seg.end_ms else 0
                if first_duration_ms < HEAD_FRAGMENT_THRESHOLD_MS and buffered_duration_ms >= HEAD_MIN_BUFFER_MS:
                    # 首个片段是头部碎片，现在缓冲区足够长了，应该提交它并处理新片段
                    print(f"[📦合并][#{track_id:04d}] 🧊 头部碎片提交: 首个片段{first_duration_ms:.0f}ms < {HEAD_FRAGMENT_THRESHOLD_MS}ms，"
                          f"缓冲已达{buffered_duration_ms:.0f}ms >= {HEAD_MIN_BUFFER_MS}ms，提交头部碎片")
                    # 先提交头部碎片
                    segments_to_commit = [s for s in self._pending_segments if s._pending]
                    for s in segments_to_commit:
                        s._pending = False
                    first_seg._pending = False
                    self._pending_segments.clear()
                    self._segment_stats["head_fragment_submit"] = self._segment_stats.get("head_fragment_submit", 0) + 1
                    print(f"[📦合并][#{track_id:04d}] 🧊 提交头部碎片: #{getattr(first_seg, '_track_id', '?')} → speaker={first_seg.speaker_label}")
                    # 不返回，继续处理新片段

            # ── 检查是否超时 ──────────────────────────────────────
            first_seg = self._pending_segments[0]
            last_seg = self._pending_segments[-1]
            wait_duration_ms = last_seg.end_ms - first_seg.start_ms
            if self._pending_segments and wait_duration_ms >= self._merge_max_wait_ms:
                _is_timeout_submit = True
                print(f"[📦合并][#{track_id:04d}] ⏰ 超时提交 ({wait_duration_ms:.0f}ms >= {self._merge_max_wait_ms}ms)")
                should_commit = True

            # ── 标签变化 → 用 top3 同人规则 ──────────────────────────
            elif segment.speaker_label != last_pending_label:
                print(f"[📦合并][#{track_id:04d}] 🔄 标签变化: {last_pending_label} → {segment.speaker_label}")

                # 用新片段的 top-3 候选判断是否与缓冲区中任意说话人同人
                is_same, matched_label, _ = self._check_same_person_by_top3(
                    segment, buffered_labels
                )

                if is_same:
                    print(f"[📦合并][#{track_id:04d}]    ← top3 同人规则通过，继续合并（不提交）")
                    self._pending_segments.append(segment)
                    self._last_speaker_label = segment.speaker_label
                    return None
                else:
                    # top3 不匹配，进行短片段二次确认
                    # 如果新片段很短，且缓冲区已有一定时长，认为可能是尾部碎片
                    is_short_segment = segment_duration_ms < SHORT_SEGMENT_THRESHOLD_MS
                    has_buffer_content = buffered_duration_ms > BUFFER_CONTENT_THRESHOLD_MS

                    if is_short_segment and has_buffer_content:
                        print(f"[📦合并][#{track_id:04d}]    🔍 短片段二次确认: 新片段{segment_duration_ms:.0f}ms < {SHORT_SEGMENT_THRESHOLD_MS}ms，"
                              f"检查是否为尾部碎片...")

                        # 检查新片段与缓冲区最后片段的声纹相似度
                        should_merge_as_tail = self._check_speaker_similarity_for_merge(
                            segment, last_pending_label
                        )

                        if should_merge_as_tail:
                            print(f"[📦合并][#{track_id:04d}]    ← 短片段二次确认通过(相似度{self._SPEAKER_EMB_SIM_THRESHOLD})，"
                                  f"判定为尾部碎片，合并到缓冲区")
                            self._pending_segments.append(segment)
                            self._last_speaker_label = segment.speaker_label
                            return None
                        else:
                            print(f"[📦合并][#{track_id:04d}]    ← 短片段二次确认失败，声纹不相似，确认换人")
                            should_commit = True
                    else:
                        # 片段不够短或缓冲区内容不足，直接检查声纹相似度
                        should_continue = self._check_speaker_similarity_for_merge(
                            segment, last_pending_label
                        )
                        if should_continue:
                            print(f"[📦合并][#{track_id:04d}]    ← 声纹相似度回退检查通过，继续合并")
                            self._pending_segments.append(segment)
                            self._last_speaker_label = segment.speaker_label
                            return None
                        else:
                            print(f"[📦合并][#{track_id:04d}]    ← top3 不匹配 + 声纹回退失败，确认换人")
                            should_commit = True

            # ── 提交 ────────────────────────────────────────────────
            if should_commit:
                segments_to_commit = [s for s in self._pending_segments if s._pending]
                for s in segments_to_commit:
                    s._pending = False
                committed_ids = [f"#{getattr(s, '_track_id', '?'):04d}" for s in segments_to_commit]

                if _is_timeout_submit:
                    self._segment_stats["timeout_submit"] += 1
                elif _is_short_tail_submit:
                    self._segment_stats["merged_submit"] += 1
                else:
                    self._segment_stats["merged_submit"] += 1
                self._segment_stats["total_merged"] += 1

                self._pending_segments.clear()
                self._pending_segments.append(segment)
                self._last_speaker_label = segment.speaker_label
                final_label = segments_to_commit[0].speaker_label if segments_to_commit else 'unknown'
                commit_reason = "超时" if _is_timeout_submit else ("短片段尾部合并" if _is_short_tail_submit else "换人")
                print(f"[📦合并][#{track_id:04d}] ✅ 提交({commit_reason}): {committed_ids} → speaker={final_label}")
                print(f"{'='*70}\n")
                return segments_to_commit
            else:
                self._pending_segments.append(segment)
                self._last_speaker_label = segment.speaker_label
                print(f"[📦合并][#{track_id:04d}] ← 继续等待 (缓冲区: {len(self._pending_segments)} 个)")
                return None

    def _flush_pending_segments(self) -> List[SpeechSegment]:
        with self._pending_lock:
            if not self._pending_segments:
                return []

            # 管道结束时，检查是否是头部碎片
            HEAD_FRAGMENT_THRESHOLD_MS = 1000
            segments = [s for s in self._pending_segments if s._pending]

            # 如果只有一个片段且时长很短，可能是头部碎片，丢弃
            if len(segments) == 1:
                first_seg = segments[0]
                first_duration_ms = first_seg.end_ms - first_seg.start_ms if first_seg.start_ms and first_seg.end_ms else 0
                if first_duration_ms < HEAD_FRAGMENT_THRESHOLD_MS:
                    print(f"[管道-合并] ⚠️ 管道结束时发现头部碎片({first_duration_ms:.0f}ms < {HEAD_FRAGMENT_THRESHOLD_MS}ms)，丢弃")
                    self._pending_segments.clear()
                    self._segment_stats["head_fragment_dropped"] = self._segment_stats.get("head_fragment_dropped", 0) + 1
                    return []

            for s in segments:
                s._pending = False
            self._pending_segments.clear()
            self._segment_stats["timeout_submit"] += 1
            self._segment_stats["total_merged"] += 1
            print(f"[管道-合并] 管道结束时提交 {len(segments)} 个待处理片段")
            return segments

    def _recognize_speaker_label(self, segment: SpeechSegment) -> Tuple[Optional[str], Dict[str, float]]:
        audio_len = len(segment.audio_data) if segment.audio_data is not None else 0
        if audio_len == 0:
            return None, {}

        with self._track_lock:
            self._track_counter += 1
            track_id = self._track_counter
        segment._track_id = track_id
        audio_ms = audio_len / 16.0

        print(f"\n[🎯#{track_id:04d}] 音频{audio_ms:.0f}ms ═══")

        try:
            if self._use_enhanced_engine and self._enhanced_registry:
                result = self._enhanced_registry.identify(segment.audio_data, track_id=track_id)
                if result and result.matches:
                    top = result.matches[0]
                    speaker_sims = {}
                    for m in result.matches:
                        speaker_sims[m.speaker_id] = m.final_score
                    segment.speaker_sims = speaker_sims
                    segment.speaker_name = top.name
                    if hasattr(self._enhanced_registry, 'extractor') and self._enhanced_registry.extractor:
                        try:
                            emb = self._enhanced_registry.extractor.extract(segment.audio_data)
                            if emb is not None and np.linalg.norm(emb) > 1e-6:
                                segment.embedding = emb
                        except Exception:
                            pass

                    self._check_speaker_uncertainty(segment, speaker_sims)

                    print(f"[🎯#{track_id:04d}] 识别结果: speaker={top.speaker_id}, cos={top.cosine_score:.4f}")
                    return top.speaker_id, speaker_sims
                print(f"[🎯#{track_id:04d}] 无匹配结果")
                return None, {}

            elif self._registered_speakers and self.speaker:
                result = self.speaker.extract_and_compare(
                    segment.audio_data,
                    self._registered_speakers,
                    threshold=SPEAKER_SIMILARITY_THRESHOLD
                )
                if result:
                    speaker_id, score, all_sims = result
                    if speaker_id != "__unknown__":
                        segment.speaker_sims = all_sims
                        if self.speaker and hasattr(self.speaker, 'extractor') and self.speaker.extractor:
                            try:
                                emb = self.speaker.extractor.extract(segment.audio_data)
                                if emb is not None and np.linalg.norm(emb) > 1e-6:
                                    segment.embedding = emb
                            except Exception:
                                pass

                        self._check_speaker_uncertainty(segment, all_sims)

                        print(f"[🎯#{track_id:04d}] 识别结果: speaker={speaker_id}, score={score:.3f}")
                        return speaker_id, all_sims
                print(f"[🎯#{track_id:04d}] 无匹配结果")
                return None, {}
            else:
                self._speaker_counter += 1
                default_label = f"speaker_{self._speaker_counter}"
                print(f"[🎯#{track_id:04d}] 无已注册说话人，使用: {default_label}")
                return default_label, {}

        except Exception as e:
            print(f"[🎯#{track_id:04d}] 识别异常: {e}")
            import traceback
            traceback.print_exc()
            return None, {}

    def _check_same_person_by_top3(
        self,
        new_segment: SpeechSegment,
        buffered_labels: List[str],
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        """
        新的同人判断规则（替代 uncertain 机制）。

        规则：如果新片段的 top-3 候选中包含缓冲区中任意一个片段的说话人，
        则视为同一人，继续合并。

        返回: (is_same_person, matched_label, unmatched_labels)
        - is_same_person: True 表示新片段和缓冲区是同一人
        - matched_label: 匹配的说话人 label
        - unmatched_labels: 缓冲区中未被匹配的 label 列表
        """
        if not new_segment.speaker_sims or not buffered_labels:
            return False, None, buffered_labels

        # 从 speaker_sims 中提取 top-3
        sorted_candidates = sorted(
            new_segment.speaker_sims.items(),
            key=lambda x: x[1],
            reverse=True
        )[:3]
        top3_ids = {sid for sid, _ in sorted_candidates}

        matched = None
        unmatched = []
        for buffered_label in buffered_labels:
            if buffered_label in top3_ids:
                matched = buffered_label
            else:
                unmatched.append(buffered_label)

        if matched is not None:
            print(f"[管道-同人] ✅ 新片段 top-3={{{', '.join(top3_ids)}}} 与缓冲区说话人 '{matched}' 匹配，视为同一人")
            return True, matched, unmatched
        else:
            print(f"[管道-同人] ❌ 新片段 top-3={{{', '.join(top3_ids)}}} 与缓冲区说话人 {buffered_labels} 无交集，需换人")
            return False, None, buffered_labels

    def _check_speaker_uncertainty(self, segment: SpeechSegment, speaker_sims: Dict[str, float]) -> None:
        """
        不再标记 uncertain，改为保留 top-3 信息供 _check_same_person_by_top3 使用。
        保留此方法以兼容外部调用，但不再设置 speaker_uncertain。
        """
        # 仅记录 top-3 分数到 segment
        if speaker_sims:
            sorted_speakers = sorted(speaker_sims.items(), key=lambda x: x[1], reverse=True)
            if len(sorted_speakers) >= 2:
                gap = sorted_speakers[0][1] - sorted_speakers[1][1]
                if gap < 0.1:
                    print(f"[管道-声纹] top1({sorted_speakers[0][0]}:{sorted_speakers[0][1]:.3f}) "
                          f"vs top2({sorted_speakers[1][0]}:{sorted_speakers[1][1]:.3f}) gap={gap:.3f}")

    def _embedding_extraction_loop(self) -> None:
        import time
        interval_s = CHANGE_DETECTOR_CONFIG.embedding_interval_ms / 1000.0
        while self._embedding_thread_running:
            try:
                self.vad.extract_pending_embeddings()
                time.sleep(interval_s)
            except Exception as e:
                print(f"[管道] 后台 embedding 提取异常: {e}")

    async def start(self):
        if self._process_task is None or self._process_task.done():
            self._loop = asyncio.get_event_loop()
            self._process_task = asyncio.create_task(self._process_loop())
            print("[管道] 流式管道已启动")

        # 启动 4 秒静止超时定时器
        if not self._inactivity_running:
            self._inactivity_running = True
            self._last_audio_time = time.time()
            self._inactivity_task = asyncio.create_task(self._inactivity_check_loop())
            print(f"[管道] 静止超时定时器已启动 (阈值={self._inactivity_timeout_ms}ms)")

    async def _process_loop(self) -> None:
        while True:
            try:
                loop = asyncio.get_event_loop()
                item = await loop.run_in_executor(None, self._transcript_queue.get)

                if item is None:
                    break

                item_type, data = item

                if item_type == "delta":
                    delta = data
                    if delta.text.strip():
                        loop = asyncio.get_event_loop()
                        delta = await loop.run_in_executor(
                            None, self._apply_punctuation_sync, delta
                        )
                        delta = await loop.run_in_executor(
                            None, self._apply_macbert_correction, delta
                        )

                        committed_ids = getattr(delta, '_committed_ids', [])
                        final_label = getattr(delta, 'speaker_id', 'unknown')
                        text_preview = delta.text[:30] + "..." if len(delta.text) > 30 else delta.text

                        if delta.was_corrected and delta.corrected_text:
                            corrected_preview = delta.corrected_text[:30] + "..." if len(delta.corrected_text) > 30 else delta.corrected_text
                            print(f"[📤WS] ← ASR结果 (片段{committed_ids}, speaker={final_label})")
                            print(f"       纠前: '{text_preview}'")
                            print(f"       纠后: '{corrected_preview}'")
                        else:
                            print(f"[📤WS] ← ASR结果 (片段{committed_ids}, speaker={final_label}): '{text_preview}'")

                        if self.on_transcript:
                            result = self.on_transcript(delta)
                            if asyncio.iscoroutine(result):
                                await result

                elif item_type == "speaker_result":
                    if isinstance(data, tuple) and len(data) == 4:
                        speaker_id, confidence, interviewer_sim, candidate_sim = data
                    elif isinstance(data, tuple) and len(data) == 2:
                        speaker_id, confidence = data
                        interviewer_sim = candidate_sim = 0.0
                    else:
                        print(f"[管道] speaker_result 数据格式异常: {type(data)}，跳过")
                        continue
                    if self.on_speaker:
                        result = self.on_speaker(speaker_id, confidence)
                        if asyncio.iscoroutine(result):
                            await result

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[管道] 处理循环错误: {e}")
                import traceback
                traceback.print_exc()

    def _apply_punctuation_sync(self, delta: TranscriptDelta) -> TranscriptDelta:
        if not self.punc_model or not delta.text or not delta.text.strip():
            return delta
        try:
            result = self.punc_model.generate(input=delta.text)
            if result and len(result) > 0:
                punc_text = result[0].get("text", delta.text) if isinstance(result[0], dict) else str(result[0])
                if punc_text and punc_text != delta.text:
                    print(f"[ASR] 标点后处理: '{delta.text}' → '{punc_text}'")
                    delta.text = punc_text
        except Exception as e:
            print(f"[ASR] 标点后处理失败（使用原文）: {e}")
        return delta

    async def feed_audio(self, audio_data: np.ndarray):
        # 更新最后音频时间（用于静止超时检测）
        self._last_audio_time = time.time()

        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = asyncio.get_event_loop()

        if self._process_task is None:
            self._process_task = asyncio.create_task(self._process_loop())

        if self._embedding_extractor is not None and not self._embedding_thread_running:
            self._embedding_thread_running = True
            self._embedding_thread = threading.Thread(target=self._embedding_extraction_loop, daemon=True)
            self._embedding_thread.start()

        if DENOISE_ENABLED:
            audio_data = self._apply_denoise(audio_data, 16000)

        try:
            segment = self.vad.feed(audio_data)
        except Exception as e:
            print(f"[管道] VAD 检测异常: {e}")
            import traceback
            traceback.print_exc()
            return

        if segment:
            self._segment_stats["total_segments"] += 1
            if segment.segment_reason == "silence_timeout":
                self._segment_stats["silence_timeout"] += 1
            elif segment.segment_reason == "voice_change":
                self._segment_stats["voice_change"] += 1

            duration_s = len(segment.audio_data) / 16000.0
            print(f"[管道] 检测到语音片段，样本数={len(segment.audio_data)}, 时长={duration_s:.2f}s, 分段原因={segment.segment_reason}")

            MIN_SEGMENT_DURATION_S = 0.5
            if duration_s < MIN_SEGMENT_DURATION_S:
                print(f"[管道] 跳过过短片段 ({duration_s:.2f}s < {MIN_SEGMENT_DURATION_S}s)")
                return

            if self.on_speech_segment:
                result = self.on_speech_segment(segment)
                if asyncio.iscoroutine(result):
                    await result

            if self._merge_enabled:
                speaker_label, speaker_sims = self._recognize_speaker_label(segment)
                segment.speaker_label = speaker_label
                segment.speaker_id = speaker_label
                segment.speaker_sims = speaker_sims

                track_id = getattr(segment, '_track_id', 0)

                segments_to_commit = self._add_segment_to_buffer(segment)

                if segments_to_commit:
                    committed_ids = [f"#{getattr(s, '_track_id', '?'):04d}" for s in segments_to_commit]
                    committed_labels = [s.speaker_label for s in segments_to_commit]
                    final_label = committed_labels[0] if committed_labels else 'unknown'

                    confirmed_label = final_label
                    merged_segment = self._merge_segments(segments_to_commit, confirmed_label)

                    merged_segment._committed_ids = committed_ids
                    merged_segment._committed_labels = committed_labels

                    print(f"[📤WS] → 提交片段{committed_ids}，speaker={final_label}")
                    asyncio.create_task(
                        self._run_streaming_asr_locked(
                            merged_segment.audio_data,
                            merged_segment.segment_reason,
                            merged_segment.overlap_tail,
                            merged_segment.start_ms,
                            merged_segment.end_ms,
                            merged_segment.speaker_label,
                            merged_segment,
                        )
                    )
                else:
                    print(f"[⏳缓冲][#{track_id:04d}] 片段等待确认: speaker={segment.speaker_label}, 缓冲区还有 {len(self._pending_segments)} 个")
            else:
                track_id = getattr(segment, '_track_id', 0)
                print(f"[📤WS][#{track_id:04d}] 提交非合并 ASR 任务 (start_ms={segment.start_ms}, speaker={segment.speaker_label})")
                asyncio.create_task(
                    self._run_streaming_asr_locked(
                        segment.audio_data,
                        segment.segment_reason,
                        segment.overlap_tail,
                        segment.start_ms,
                        segment.end_ms,
                        segment.speaker_label,
                        segment,
                    )
                )

    async def _run_streaming_asr_locked(
        self,
        audio_data: np.ndarray,
        segment_reason: str = None,
        overlap_tail: np.ndarray = None,
        segment_start_ms: int = 0,
        segment_end_ms: int = 0,
        speaker_label: str = None,
        merged_segment=None,
    ):
        loop = asyncio.get_event_loop()
        speaker_result_holder = {}

        if overlap_tail is not None and len(overlap_tail) > 0:
            prepend_samples = len(overlap_tail)
            prepend_ms = int(prepend_samples / self.vad.sample_rate * 1000)
            audio_data_original_samples = len(audio_data)
            audio_data_original_ms = int(audio_data_original_samples / self.vad.sample_rate * 1000)
            audio_data = np.concatenate([overlap_tail, audio_data])
            orig_start_ms = segment_start_ms
            orig_end_ms = segment_end_ms
            actual_start_ms = segment_start_ms - prepend_ms
            actual_end_ms = segment_end_ms
            print(f"[ASR-DBG] ASR 接收详情:")
            print(f"[ASR-DBG]   分段原因: {segment_reason}")
            print(f"[ASR-DBG]   prepend: {prepend_samples}样本={prepend_ms}ms")
            print(f"[ASR-DBG]   原始: [{orig_start_ms}-{orig_end_ms}ms], {audio_data_original_samples}样本, {audio_data_original_ms}ms")
            print(f"[ASR-DBG]   prepend后: [{actual_start_ms}-{actual_end_ms}ms], {len(audio_data)}样本, {len(audio_data)/self.vad.sample_rate*1000:.0f}ms")
            segment_start_ms = actual_start_ms
        else:
            print(f"[ASR-DBG] ASR 接收: [{segment_start_ms}-{segment_end_ms}ms], "
                  f"{len(audio_data)}样本, {len(audio_data)/self.vad.sample_rate*1000:.0f}ms, "
                  f"reason={segment_reason}")

        def _do_speaker_recognition() -> None:
            if not self._registered_speakers and not self._use_enhanced_engine:
                return
            track_id = getattr(merged_segment, '_track_id', None)
            try:
                if self._use_enhanced_engine and self._enhanced_registry:
                    if self._multi_window_enabled:
                        # 注意：测试证明多窗口融合会降低 18% 准确率，仅当明确启用时使用
                        result, stats = self._enhanced_registry.identify_with_voting(
                            audio_data,
                            n_windows=self._multi_window_n,
                            window_step_ratio=self._multi_window_step,
                            vote_method=self._multi_window_vote,
                            use_multi_window=True,
                            track_id=track_id,
                        )
                        if result and result.matches:
                            top = result.matches[0]
                            all_sims = {m.speaker_id: m.final_score for m in result.matches}
                            speaker_result_holder["data"] = ("enhanced_vote", ({
                                "matches": [
                                    (m.speaker_id, m.name or m.speaker_id, m.final_score)
                                    for m in result.matches
                                ],
                                "all_sims": all_sims,
                                "stats": stats,
                            }, all_sims))
                        else:
                            speaker_result_holder["data"] = ("enhanced_vote", ([], {}))
                    else:
                        # 使用改进后的融合算法（center_subtract + Mahalanobis）
                        result = self._enhanced_registry.identify(audio_data, track_id=track_id)
                        if result and result.matches:
                            top = result.matches[0]
                            all_sims = {m.speaker_id: m.final_score for m in result.matches}
                            speaker_result_holder["data"] = ("enhanced", ([
                                (m.speaker_id, m.name or m.speaker_id, m.final_score)
                                for m in result.matches
                            ], all_sims))
                        else:
                            speaker_result_holder["data"] = ("enhanced", ([], {}))
                else:
                    if self._multi_window_enabled and self.speaker is not None:
                        window_results = self.speaker.extract_multi_window(
                            audio_data,
                            n_windows=self._multi_window_n,
                            window_step_ratio=self._multi_window_step,
                        )
                        valid = [(emb, ts, ok) for emb, ts, ok in window_results if ok]
                        if valid:
                            fused_emb, fuse_stats = self.speaker.extractor.extract_fused(
                                audio_data, self._multi_window_n,
                                self._multi_window_step, fusion_method="mean"
                            )
                            result = self.speaker.extract_and_compare(
                                fused_emb, self._registered_speakers
                            )
                            if result:
                                sid, confidence, all_sims = result
                                speaker_result_holder["data"] = ("legacy_vote", (
                                    sid, confidence, all_sims,
                                    {"n_valid": len(valid), "stats": fuse_stats}
                                ))
                        else:
                            speaker_result_holder["data"] = ("legacy_vote", (None, 0.0, {}, {}))
                    else:
                        result = self.speaker.extract_and_compare(
                            audio_data, self._registered_speakers
                        )
                        if result:
                            sid, confidence, all_sims = result
                            speaker_result_holder["data"] = ("legacy", (sid, confidence, all_sims))
            except Exception as e:
                print(f"[管道] 声纹识别异常: {e}")
                import traceback
                traceback.print_exc()

        def on_delta(delta: TranscriptDelta):
            track_id = getattr(merged_segment, '_track_id', 0) if merged_segment else 0
            delta._track_id = track_id
            delta._committed_ids = getattr(merged_segment, '_committed_ids', []) if merged_segment else []
            delta._committed_labels = getattr(merged_segment, '_committed_labels', []) if merged_segment else []
            # 补上片段时间戳（StreamingASR.recognize 内部返回0，这里用 segment 的实际时间）
            if merged_segment:
                delta.start_ms = getattr(merged_segment, 'start_ms', 0) or 0
                delta.end_ms = getattr(merged_segment, 'end_ms', 0) or 0
            # 如果 merged_segment 为 None（传入的是原始 segment），尝试从函数参数获取
            if delta.start_ms == 0 and segment_start_ms:
                delta.start_ms = segment_start_ms
                delta.end_ms = segment_end_ms

            if speaker_label:
                delta.speaker_id = speaker_label
                delta.speaker_confidence = 1.0
                delta.speaker_candidates = [(speaker_label, None, 1.0)]
                # 传递说话人姓名（来自增强引擎注册时设置的 name）
                if merged_segment and getattr(merged_segment, 'speaker_name', None):
                    delta.speaker_name = merged_segment.speaker_name
            else:
                speaker_info = speaker_result_holder.get("data")
                if speaker_info:
                    info_type, info_data = speaker_info
                    if info_type in ("enhanced", "enhanced_vote"):
                        if info_type == "enhanced_vote" and isinstance(info_data, tuple):
                            vote_data, all_sims = info_data
                            if isinstance(vote_data, dict):
                                top_candidates = vote_data.get("matches", [])
                                delta.uncertain_speaker = vote_data.get("uncertain", False)
                                delta.speaker_uncertain_reason = vote_data.get("uncertain_reason", "")
                                delta.speaker_multi_window_stats = vote_data.get("stats", {})
                            else:
                                top_candidates = []
                                all_sims = {}
                        else:
                            top_candidates, all_sims = info_data
                        if top_candidates:
                            top_sid, top_name, top_score = top_candidates[0]
                            delta.speaker_id = top_sid
                            # 保留说话人姓名（来自注册时的 name）
                            delta.speaker_name = top_name if top_name else None
                            delta.speaker_confidence = top_score
                            delta.speaker_candidates = top_candidates
                            delta.registered_speaker_sims = all_sims
                        else:
                            delta.speaker_id = "unknown"
                            delta.speaker_confidence = 0.0
                            delta.registered_speaker_sims = all_sims
                    else:
                        if info_type == "legacy_vote":
                            sid, confidence, all_sims, vote_stats = info_data
                        else:
                            sid, confidence, all_sims = info_data
                            vote_stats = {}
                        if sid == "__unknown__" or sid is None:
                            delta.speaker_id = "unknown"
                            delta.speaker_confidence = 0.0
                            delta.registered_speaker_sims = {}
                        else:
                            delta.speaker_id = sid
                            delta.speaker_confidence = confidence
                            delta.registered_speaker_sims = all_sims
                            if "interviewer" in all_sims and "candidate" in all_sims:
                                delta.recognized_role = (
                                    "candidate" if all_sims.get("candidate", 0) > all_sims.get("interviewer", 0)
                                    else "interviewer"
                                )
                            if vote_stats:
                                delta.speaker_multi_window_stats = vote_stats
                else:
                    delta.speaker_id = "unknown"
                    delta.speaker_confidence = 0.0

            if segment_reason:
                delta.segment_reason = segment_reason

            try:
                self._transcript_queue.put_nowait(("delta", delta))
            except Exception as e:
                print(f"[ASR-Thread] 放入队列失败: {e}")

        def _run():
            if not speaker_label:
                _do_speaker_recognition()
            self.asr.recognize(audio_data, on_delta, self.language)

        async with self._asr_lock:
            await self._loop.run_in_executor(None, _run)

    async def stop(self) -> None:
        # 停止静止超时定时器
        self._inactivity_running = False
        if self._inactivity_task:
            self._inactivity_task.cancel()
            try:
                await self._inactivity_task
            except asyncio.CancelledError:
                pass
            self._inactivity_task = None

        if self._merge_enabled:
            pending_segments = self._flush_pending_segments()
            if pending_segments:
                confirmed_label = pending_segments[0].speaker_label if pending_segments else None
                merged_segment = self._merge_segments(pending_segments, confirmed_label)
                track_id = getattr(merged_segment, '_track_id', 0)
                print(f"[WS][#{track_id:04d}] 停止前提交最后一批合并片段")
                asyncio.create_task(
                    self._run_streaming_asr_locked(
                        merged_segment.audio_data,
                        merged_segment.segment_reason,
                        merged_segment.overlap_tail,
                        merged_segment.start_ms,
                        merged_segment.end_ms,
                        merged_segment.speaker_label,
                        merged_segment,
                    )
                )

        if self._process_task:
            self._transcript_queue.put_nowait(None)
            self._process_task.cancel()
            try:
                await self._process_task
            except asyncio.CancelledError:
                pass
            self._process_task = None
            print("[管道] 流式管道已停止")
            self.print_segment_stats()

    def get_segment_stats(self) -> dict:
        return self._segment_stats.copy()

    def print_segment_stats(self) -> None:
        stats = self._segment_stats
        total = stats["total_segments"]
        silence = stats["silence_timeout"]
        voice = stats["voice_change"]
        merged = stats["merged_submit"]
        timeout = stats["timeout_submit"]
        total_merged = stats["total_merged"]

        print("\n" + "=" * 60)
        print("【分割统计汇总】")
        print("-" * 60)
        print(f"  总VAD片段数:        {total}")
        print(f"  静音超时分割:       {silence}" + (f" ({silence/total*100:.1f}%)" if total else "") )
        print(f"  声纹突变分割:       {voice}" + (f" ({voice/total*100:.1f}%)" if total else "") )
        print("-" * 60)
        print(f"  合并提交数:         {merged}")
        print(f"  超时强制提交数:     {timeout}")
        print(f"  总合并提交数:       {total_merged}")
        print("=" * 60 + "\n")

    def reset(self):
        self.vad.reset()
        self._last_audio_time = time.time()
        self._inactivity_running = False
        if self._inactivity_task:
            self._inactivity_task.cancel()
            self._inactivity_task = None

    # ============== 4 秒静止超时提交（防止最后一句话卡在缓冲区）==============

    def set_inactivity_timeout(self, timeout_ms: float):
        """设置静止超时阈值（毫秒），默认 4000ms"""
        self._inactivity_timeout_ms = timeout_ms
        print(f"[管道] 静止超时已设置为 {timeout_ms}ms")

    async def _inactivity_check_loop(self):
        """后台定时器：每 1 秒检查一次静止时长，若缓冲有内容且静止 >= 超时阈值则强制提交"""
        check_interval = 1.0  # 每秒检查一次
        while self._inactivity_running:
            await asyncio.sleep(check_interval)

            with self._pending_lock:
                if not self._pending_segments:
                    continue

                elapsed = (time.time() - self._last_audio_time) * 1000
                if elapsed < self._inactivity_timeout_ms:
                    continue

                # 强制提交缓冲区所有片段
                segments_to_commit = [s for s in self._pending_segments if s._pending]
                for s in segments_to_commit:
                    s._pending = False
                self._pending_segments.clear()
                self._segment_stats["timeout_submit"] += 1
                self._segment_stats["total_merged"] += 1

            if segments_to_commit:
                committed_ids = [f"#{getattr(s, '_track_id', '?'):04d}" for s in segments_to_commit]
                print(f"[管道-静止超时] ⏰ 静止 {elapsed:.0f}ms >= {self._inactivity_timeout_ms}ms，"
                      f"强制提交 {len(segments_to_commit)} 个片段: {committed_ids}")

                confirmed_label = segments_to_commit[0].speaker_label if segments_to_commit else 'unknown'
                merged_segment = self._merge_segments(segments_to_commit, confirmed_label)
                merged_segment._committed_ids = committed_ids

                asyncio.create_task(
                    self._run_streaming_asr_locked(
                        merged_segment.audio_data,
                        merged_segment.segment_reason,
                        merged_segment.overlap_tail,
                        merged_segment.start_ms,
                        merged_segment.end_ms,
                        merged_segment.speaker_label,
                        merged_segment,
                    )
                )
            segments_to_commit = []


# ============== 便捷创建函数（对齐 InsightEye）==============

def create_streaming_pipeline(
    model_manager,
    language: str = "zh",
    use_correction: bool = False,
) -> StreamingPipeline:
    return StreamingPipeline(
        vad_model=model_manager.get_vad_model(),
        asr_model=model_manager.get_asr_model(),
        punc_model=model_manager.get_punc_model(),
        camp_model=model_manager.get_camp_model(),
        language=language,
        device=model_manager.device,
        use_correction=use_correction,
    )
