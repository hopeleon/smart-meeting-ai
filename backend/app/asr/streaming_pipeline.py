"""
流式 VAD + ASR + 声纹识别管道
对齐 InsightEye

核心架构：
- feed_audio: 接收音频流，内部包含 VAD 检测、说话人边界检测、片段合并
- StreamingVAD: 静音超时触发分段 + embedding 滑动窗口说话人变化检测
- 合并逻辑：等相同说话人片段累积（或超时）→ 合并音频后送 ASR
- ASR 结果通过 on_transcript 回调发送
- 去噪（RNNoise）
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

MIN_SPEECH_DURATION_MS = 800
MIN_SILENCE_DURATION_MS = 800

# 质量优先模式：积累足够音频后再处理
QUALITY_MODE_MIN_ACCUMULATE_SAMPLES = 15 * VAD_SAMPLE_RATE  # 15 秒
QUALITY_MODE_CHUNK_SAMPLES = 15 * VAD_SAMPLE_RATE            # 每 15 秒处理一次
QUALITY_MODE_MERGE_MAX_WAIT_MS = 30000                       # buffer 等待 30s 才提交
MIN_SPEECH_ENERGY_THRESHOLD = 0.005

SEGMENT_OVERLAP_TAIL_MS = 200

SPEAKER_SIMILARITY_THRESHOLD = 0.5
ENHANCED_SPEAKER_COS_THRESHOLD = 0.5  # 增强引擎识别余弦门槛：低于此判未知，避免短段误认成错的人
MAX_SPEAKERS = 10

SPEAKER_TOP_GAP_THRESHOLD = 0.03
SPEAKER_SHORT_AUDIO_THRESHOLD_MS = 2000
SPEAKER_STRICT_GAP_THRESHOLD = 0.03
# 身份识别门控（治“短段在大库里撞错人”的身份闪烁）
SPEAKER_ID_GAP_MIN = 0.06          # top1 与 top2 余弦差，低于此视为模棱两可 -> 判未知，不强行认名
SPEAKER_ID_SHORT_MS = 2000         # 短于此(ms)的片段声纹不可靠，要求更高余弦(+0.10)才接受身份

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
    _pending: bool = True


# ============== 流式 VAD（对齐 InsightEye StreamingVAD）==============

class StreamingVAD:
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
        # 变点检测：0.15s 跳步 + 0.8s 重叠窗（5s 延迟预算下提升 embedding 可靠性，压测 F1 0.37→0.91）
        self._embedding_extraction_interval_samples = int(150 * sample_rate / 1000)
        self._emb_window_samples = int(800 * sample_rate / 1000)
        self._pending_audio_lock = threading.Lock()
        self._embedding_lock = threading.Lock()
        self._last_change_detected_ms = -999999
        self._pending_audio_for_embedding: List[np.ndarray] = []
        self._prev_segment_tail: Optional[np.ndarray] = None

        self._speaker_change_cooldown_until_ms: int = 0
        self._speaker_change_cooldown_ms: int = 1500
        self._last_confirmed_speaker_label: Optional[str] = None

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
        self._prev_segment_tail = None
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
        except Exception:
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
            hop = self._embedding_extraction_interval_samples   # 0.15s 跳步
            win = self._emb_window_samples                       # 0.8s 重叠窗
            while len(audio) >= win:
                chunk = audio[:win]
                audio = audio[hop:]                              # 只前进一个 hop，保留重叠

                try:
                    emb = self._embedding_extractor.extract(chunk)
                    if emb is not None and len(emb) > 0:
                        emb_norm = np.linalg.norm(emb)
                        if emb_norm < 1e-6:
                            continue
                        ts = int(self._total_samples_processed * 1000 / self.sample_rate)
                        with self._embedding_lock:
                            self._embedding_buffer.append(emb)
                            self._embedding_timestamps_ms.append(ts)
                        extracted.append(emb)
                        self._last_embedding_extract_ms = ts
                except Exception:
                    pass

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
                gain = total_cost - cost(left) - cost(right) - cfg.cp_penalty
                if gain > best_gain:
                    best_gain = gain
                    best_cp = t
            return best_gain, best_cp

        cps = []
        stack = [(0, n)]
        while stack:
            start_, stop_ = stack.pop()
            gain, cp = find_single_cp(distances, start_, stop_)
            if cp < 0:
                continue
            cps.append(cp)
            stack.append((start_, cp))
            stack.append((cp, stop_))
        cps.sort()
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

        elif self.state == "speech":
            self.speech_buffer.append(chunk)

            current_time_ms = int(self._total_samples_processed * 1000 / self.sample_rate)
            is_in_cooldown = current_time_ms < self._speaker_change_cooldown_until_ms

            voice_changed, change_info = self._detect_speaker_change_by_embedding()
            if voice_changed and len(self.speech_buffer) >= 3:
                if is_in_cooldown:
                    pass
                else:
                    total_samples = sum(len(x) for x in self.speech_buffer)
                    audio_data = np.concatenate(self.speech_buffer)
                    start_ms = int(self.speech_start_sample * 1000 / self.sample_rate)
                    end_ms = int((self.speech_start_sample + total_samples) * 1000 / self.sample_rate)

                    info = change_info or {}
                    change_det_ms = info.get("current_time_ms", 0)
                    first_drift = info.get("first_drift")
                    distances = info.get("distances", [])
                    timestamps_cp = info.get("timestamps_ms_arr", [])

                    audio_data_trimmed = audio_data
                    _trimmed = False

                    if CHANGE_DETECTOR_CONFIG.offline_changepoint and len(distances) >= 4 and len(timestamps_cp) == len(distances):
                        ts_arr = np.array(timestamps_cp, dtype=np.int64)
                        d_arr = np.array(distances, dtype=np.float64)
                        trim_start_ms, trim_end_ms = self._find_changepoint_offline(
                            ts_arr, d_arr, start_ms, end_ms
                        )
                        if trim_start_ms > start_ms:
                            trim_sample = int((trim_start_ms - start_ms) / 1000 * self.sample_rate)
                            audio_data_trimmed = audio_data[trim_sample:]
                            start_ms = trim_start_ms
                            _trimmed = True

                    overlap_samples = int(SEGMENT_OVERLAP_TAIL_MS * self.sample_rate / 1000)
                    overlap_tail = audio_data_trimmed[-overlap_samples:] if len(audio_data_trimmed) >= overlap_samples else audio_data_trimmed
                    self._prev_segment_tail = overlap_tail.copy()

                    self.speech_buffer = []
                    self.speech_start_sample = self._total_samples_processed
                    self.silence_samples = 0
                    self._embedding_buffer.clear()
                    self._embedding_timestamps_ms.clear()

                    self._speaker_change_cooldown_until_ms = current_time_ms + self._speaker_change_cooldown_ms

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
                        overlap_samples = int(SEGMENT_OVERLAP_TAIL_MS * self.sample_rate / 1000)
                        overlap_tail = audio_data[-overlap_samples:] if total_samples >= overlap_samples else audio_data
                        self._prev_segment_tail = overlap_tail.copy()

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
                        self.state = "idle"
                        self.speech_buffer = []
                        self.speech_start_sample = 0
                        self.silence_samples = 0
                        self._embedding_buffer.clear()
                        self._embedding_timestamps_ms.clear()

        return None


# ============== 流式 ASR（对齐 InsightEye StreamingASR）==============

import re as _re
_PUNCT = "，。！？、；：,.!?;:"

def _clean_asr_text(t: str) -> str:
    """折叠 ASR/标点模型产生的重复标点（合并多段时常见的 '，，' '。。' 假象）。重复词不动(可能是真口语)。"""
    if not t:
        return t
    t = _re.sub(r"([，。！？、；：,.!?;:])\1+", r"\1", t)   # 连续相同标点 -> 一个
    t = _re.sub(r"，([。！？])", r"\1", t)                  # 逗号紧跟句末标点 -> 句末标点
    return t


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

                text = _clean_asr_text(text)
                if text:
                    on_delta(TranscriptDelta(
                        text=text,
                        is_final=is_final,
                        start_ms=0,
                        end_ms=0,
                    ))

        except Exception as e:
            print(f"[ASR] 识别失败: {e}")


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
            return None
        if self.extractor is None:
            return None

        try:
            embedding = self.extractor.extract(audio_data)
            emb_norm = np.linalg.norm(embedding)

            if emb_norm < 1e-6:
                return ("__unknown__", 0.0, {})

            best_match = None
            best_score = 0
            all_sims = {}

            for speaker_id, registered_emb in registered_embeddings.items():
                cos_sim = np.dot(embedding, registered_emb) / (
                    np.linalg.norm(embedding) * np.linalg.norm(registered_emb)
                )
                score = (cos_sim + 1.0) / 2.0
                all_sims[speaker_id] = float(score)

                if score > best_score:
                    best_score = score
                    best_match = speaker_id

            if best_match:
                return (best_match, float(best_score), all_sims)
            return None

        except Exception as e:
            print(f"[声纹] 声纹识别异常: {e}")
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

        # 质量优先模式
        self._quality_mode: bool = False
        self._quality_buffer: np.ndarray = np.array([], dtype=np.float32)
        self._quality_buffer_samples: int = 0   # 已积累样本数
        self._quality_accumulated_ms: int = 0  # 已积累音频时长（ms）

        self._segment_stats = {
            "silence_timeout": 0,
            "voice_change": 0,
            "merged_submit": 0,
            "timeout_submit": 0,
            "total_segments": 0,
            "total_merged": 0,
        }

    def _init_denoiser(self):
        if not DENOISE_ENABLED:
            return
        if self._denoiser_initialized:
            return

        try:
            from app.asr.audio_denoiser import get_denoiser
            self._denoiser = get_denoiser(backend=DENOISE_BACKEND)
            self._denoiser_initialized = True
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
            from app.asr.audio_denoiser import estimate_snr_db, DENOISE_SNR_THRESHOLD_DB
            if estimate_snr_db(audio, sample_rate) >= DENOISE_SNR_THRESHOLD_DB:
                return audio   # 噪声门控：音频够干净则不去噪，避免伤清晰语音（安全收尾）
            return self._denoiser.denoise(audio, sample_rate)
        except Exception:
            return audio

    def register_speaker(self, speaker_id: str, embedding: np.ndarray,
                         name: Optional[str] = None, role: Optional[str] = None):
        self._registered_speakers[speaker_id] = embedding

        if self._use_enhanced_engine and self._enhanced_registry:
            self._enhanced_registry.register_embedding(speaker_id, embedding, name=name, role=role)

    def unregister_speaker(self, speaker_id: str) -> bool:
        if speaker_id in self._registered_speakers:
            del self._registered_speakers[speaker_id]
            return True
        return False

    def set_enhanced_registry(self, registry):
        if not hasattr(self, '_enhanced_registry') or self._enhanced_registry is None:
            self._use_enhanced_engine = True
        self._enhanced_registry = registry

    def set_multi_window(self, enabled: bool = True, n_windows: int = 3,
                         window_step_ratio: float = 0.25, vote_method: str = "score_weighted"):
        self._multi_window_enabled = enabled
        self._multi_window_n = n_windows
        self._multi_window_step = window_step_ratio
        self._multi_window_vote = vote_method

    def set_merge_strategy(self, enabled: bool = True, max_wait_ms: float = 5000.0,
                           min_same_label: int = 2, min_duration_ms: float = 500.0):
        self._merge_enabled = enabled
        self._merge_max_wait_ms = max_wait_ms
        self._merge_min_same_label = min_same_label
        self._merge_min_duration_ms = min_duration_ms

    _SPEAKER_EMB_SIM_THRESHOLD: float = 0.65

    def _check_speaker_similarity_for_merge(
        self, segment: SpeechSegment, last_pending_label: str
    ) -> bool:
        last_segment = self._pending_segments[-1] if self._pending_segments else None
        if not last_segment or last_segment.embedding is None:
            return False

        if segment.embedding is None:
            return False

        emb_a = segment.embedding
        emb_b = last_segment.embedding
        norm_a = np.linalg.norm(emb_a)
        norm_b = np.linalg.norm(emb_b)
        if norm_a < 1e-6 or norm_b < 1e-6:
            return False

        cos_sim = float(np.dot(emb_a, emb_b) / (norm_a * norm_b))

        if cos_sim >= self._SPEAKER_EMB_SIM_THRESHOLD:
            return True
        else:
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

        merged_segment = SpeechSegment(
            audio_data=merged_audio,
            start_ms=start_ms,
            end_ms=end_ms,
            segment_reason=merged_reason,
            overlap_tail=overlap_tail,
            speaker_id=segments[0].speaker_id,
            speaker_label=final_label,
            speaker_name=segments[0].speaker_name,
        )
        merged_segment._track_id = getattr(segments[0], '_track_id', None)
        merged_segment.speaker_sims = segments[0].speaker_sims
        merged_segment.embedding = segments[0].embedding

        return merged_segment

    def _should_commit_segments(self, current_label: Optional[str]) -> Tuple[bool, List[SpeechSegment]]:
        if not self._pending_segments:
            return False, []

        if self._pending_segments:
            first_seg = self._pending_segments[0]
            last_seg = self._pending_segments[-1]
            wait_duration_ms = last_seg.end_ms - first_seg.start_ms
            if wait_duration_ms >= self._merge_max_wait_ms:
                return True, list(self._pending_segments)

        last_pending_label = self._pending_segments[-1].speaker_label if self._pending_segments else None
        if current_label is not None and last_pending_label is not None and current_label != last_pending_label:
            return True, list(self._pending_segments)

        if len(self._pending_segments) >= self._merge_min_same_label:
            labels = [s.speaker_label for s in self._pending_segments if s.speaker_label is not None]
            if labels and all(l == labels[0] for l in labels):
                return True, list(self._pending_segments)

        return False, []

    def _add_segment_to_buffer(self, segment: SpeechSegment) -> Optional[List[SpeechSegment]]:
        HEAD_FRAGMENT_THRESHOLD_MS = 1000
        HEAD_MIN_BUFFER_MS = 3000
        SHORT_SEGMENT_THRESHOLD_MS = 3000
        TAIL_RESIDUE_THRESHOLD_MS = 500
        BUFFER_CONTENT_THRESHOLD_MS = 5000

        with self._pending_lock:
            last_pending_label = self._pending_segments[-1].speaker_label if self._pending_segments else None
            last_pending_embedding = (self._pending_segments[-1].embedding
                                     if self._pending_segments and self._pending_segments[-1].embedding is not None
                                     else None)

            segment_duration_ms = (
                segment.end_ms - segment.start_ms
                if segment.start_ms and segment.end_ms
                else len(segment.audio_data) / 16.0
            )

            track_id = getattr(segment, '_track_id', 0)

            # ── 尾部残留：极短片段直接丢弃 ──────────────────────────
            if segment_duration_ms < TAIL_RESIDUE_THRESHOLD_MS:
                return None

            # ── 缓冲区为空：第一个片段 ─────────────────────────────
            if not self._pending_segments:
                if segment_duration_ms < HEAD_FRAGMENT_THRESHOLD_MS:
                    # 可能的头部碎片，等待更多音频后再判断
                    self._pending_segments.append(segment)
                    self._last_speaker_label = segment.speaker_label
                    return None
                # 长度足够的片段，加入缓冲区
                self._pending_segments.append(segment)
                self._last_speaker_label = segment.speaker_label
                return None

            # ── 计算当前缓冲总时长 ────────────────────────────────
            first_seg = self._pending_segments[0]
            last_seg = self._pending_segments[-1]
            buffered_duration_ms = last_seg.end_ms - first_seg.start_ms

            # ── 超时强制提交 ───────────────────────────────────────
            if buffered_duration_ms >= self._merge_max_wait_ms:
                segments_to_commit = [s for s in self._pending_segments if s._pending]
                for s in segments_to_commit:
                    s._pending = False
                self._segment_stats["merged_submit"] += 1
                self._segment_stats["total_merged"] += 1
                self._pending_segments.clear()
                self._pending_segments.append(segment)
                self._last_speaker_label = segment.speaker_label
                return segments_to_commit

            # ── 同 label → 直接 append ────────────────────────────
            if segment.speaker_label == last_pending_label:
                self._pending_segments.append(segment)
                self._last_speaker_label = segment.speaker_label
                return None

            # ── 不同 label → cosine similarity 二次确认 ───────────
            cos_sim = 0.0
            can_use_sim = (
                segment.embedding is not None
                and last_pending_embedding is not None
                and np.linalg.norm(segment.embedding) > 1e-6
                and np.linalg.norm(last_pending_embedding) > 1e-6
            )
            if can_use_sim:
                cos_sim = float(
                    np.dot(segment.embedding, last_pending_embedding)
                    / (np.linalg.norm(segment.embedding) * np.linalg.norm(last_pending_embedding))
                )

            if cos_sim >= self._SPEAKER_EMB_SIM_THRESHOLD:
                # 声纹相似 → 判定为同一人，append 并更新 buffer 的 embedding
                self._pending_segments.append(segment)
                self._last_speaker_label = segment.speaker_label
                return None

            # 声纹不相似 → 换人了
            # 短片段 + 缓冲区充足：当前片段作为尾部碎片合并（即使声纹不相似也合并）
            is_short_segment = segment_duration_ms < SHORT_SEGMENT_THRESHOLD_MS
            if is_short_segment and buffered_duration_ms >= BUFFER_CONTENT_THRESHOLD_MS:
                self._pending_segments.append(segment)
                self._last_speaker_label = segment.speaker_label
                return None

            # 强制提交缓冲片段，当前片段开始新 buffer
            segments_to_commit = [s for s in self._pending_segments if s._pending]
            for s in segments_to_commit:
                s._pending = False
            self._segment_stats["merged_submit"] += 1
            self._segment_stats["total_merged"] += 1
            self._pending_segments.clear()
            self._pending_segments.append(segment)
            self._last_speaker_label = segment.speaker_label
            return segments_to_commit

    def _flush_pending_segments(self) -> List[SpeechSegment]:
        with self._pending_lock:
            if not self._pending_segments:
                return []

            segments = [s for s in self._pending_segments if s._pending]

            if len(segments) == 1:
                first_seg = segments[0]
                first_duration_ms = first_seg.end_ms - first_seg.start_ms if first_seg.start_ms and first_seg.end_ms else 0
                if first_duration_ms < 1000:
                    self._pending_segments.clear()
                    self._segment_stats["head_fragment_dropped"] = self._segment_stats.get("head_fragment_dropped", 0) + 1
                    return []

            for s in segments:
                s._pending = False
            self._pending_segments.clear()
            self._segment_stats["timeout_submit"] += 1
            self._segment_stats["total_merged"] += 1
            return segments

    def _recognize_speaker_label(self, segment: SpeechSegment) -> Tuple[Optional[str], Dict[str, float]]:
        audio_len = len(segment.audio_data) if segment.audio_data is not None else 0
        if audio_len == 0:
            return None, {}

        with self._track_lock:
            self._track_counter += 1
            track_id = self._track_counter
        segment._track_id = track_id

        try:
            if self._use_enhanced_engine and self._enhanced_registry:
                result = self._enhanced_registry.identify(segment.audio_data, track_id=track_id)
                if result and result.matches:
                    top = result.matches[0]
                    speaker_sims = {}
                    for m in result.matches:
                        speaker_sims[m.speaker_id] = m.final_score
                    segment.speaker_sims = speaker_sims
                    # 先提 embedding（无论是否认定，都留给合并/聚类用）
                    if hasattr(self._enhanced_registry, 'extractor') and self._enhanced_registry.extractor:
                        try:
                            emb = self._enhanced_registry.extractor.extract(segment.audio_data)
                            if emb is not None and np.linalg.norm(emb) > 1e-6:
                                segment.embedding = emb
                        except Exception:
                            pass
                    # 身份门控：短段(<2s)声纹不可靠要更高余弦；并要求 top1 与 top2 有足够区分度，治身份闪烁
                    _seg_ms = ((segment.end_ms - segment.start_ms)
                               if (segment.start_ms and segment.end_ms)
                               else len(segment.audio_data) / 16.0)
                    _cos1 = getattr(top, "cosine_score", 0.0)
                    _cos2 = result.matches[1].cosine_score if len(result.matches) > 1 else 0.0
                    _cos_need = ENHANCED_SPEAKER_COS_THRESHOLD + (0.10 if _seg_ms < SPEAKER_ID_SHORT_MS else 0.0)
                    if _cos1 >= _cos_need and (_cos1 - _cos2) >= SPEAKER_ID_GAP_MIN:
                        segment.speaker_name = top.name
                        return top.speaker_id, speaker_sims
                    return None, speaker_sims
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
                        return speaker_id, all_sims
                return None, {}
            else:
                self._speaker_counter += 1
                default_label = f"speaker_{self._speaker_counter}"
                return default_label, {}

        except Exception as e:
            print(f"[🎯#{track_id}] 识别异常: {e}")
            return None, {}

    def _check_same_person_by_top3(
        self,
        new_segment: SpeechSegment,
        buffered_labels: List[str],
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        if not new_segment.speaker_sims or not buffered_labels:
            return False, None, buffered_labels

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
            return True, matched, unmatched
        else:
            return False, None, buffered_labels

    def _check_speaker_uncertainty(self, segment: SpeechSegment, speaker_sims: Dict[str, float]) -> None:
        if speaker_sims:
            sorted_speakers = sorted(speaker_sims.items(), key=lambda x: x[1], reverse=True)
            if len(sorted_speakers) >= 2:
                gap = sorted_speakers[0][1] - sorted_speakers[1][1]
                if gap < 0.1:
                    pass

    def _embedding_extraction_loop(self) -> None:
        interval_s = CHANGE_DETECTOR_CONFIG.embedding_interval_ms / 1000.0
        while self._embedding_thread_running:
            try:
                self.vad.extract_pending_embeddings()
                time.sleep(interval_s)
            except Exception:
                pass

    async def start(self):
        if self._process_task is None or self._process_task.done():
            self._loop = asyncio.get_event_loop()
            self._process_task = asyncio.create_task(self._process_loop())

        if not self._inactivity_running:
            self._inactivity_running = True
            self._last_audio_time = time.time()
            self._inactivity_task = asyncio.create_task(self._inactivity_check_loop())

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

                        committed_ids = getattr(delta, '_committed_ids', [])
                        final_label = getattr(delta, 'speaker_id', 'unknown')

                        if self.on_transcript:
                            delta.text = _clean_asr_text(delta.text)   # 最终清洗(在 punc 之后)
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
                        continue
                    if self.on_speaker:
                        result = self.on_speaker(speaker_id, confidence)
                        if asyncio.iscoroutine(result):
                            await result

            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"[管道] 处理循环错误: {e}")

    def _apply_punctuation_sync(self, delta: TranscriptDelta) -> TranscriptDelta:
        if delta.text:
            delta.text = _clean_asr_text(delta.text)   # 无条件折叠重复标点(ASR 自带 punc 产生 ，， 。。)
        if not self.punc_model or not delta.text or not delta.text.strip():
            return delta
        try:
            result = self.punc_model.generate(input=delta.text)
            if result and len(result) > 0:
                punc_text = result[0].get("text", delta.text) if isinstance(result[0], dict) else str(result[0])
                if punc_text and punc_text != delta.text:
                    delta.text = punc_text
        except Exception:
            pass
        return delta

    async def feed_audio(self, audio_data: np.ndarray):
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

        # ── 质量优先模式：积累音频，积累够 15 秒再处理 ─────────────────
        if self._quality_mode:
            self._quality_buffer = np.concatenate([self._quality_buffer, audio_data])
            self._quality_buffer_samples = len(self._quality_buffer)
            self._quality_accumulated_ms = int(self._quality_buffer_samples / VAD_SAMPLE_RATE * 1000)

            if self._quality_buffer_samples >= QUALITY_MODE_MIN_ACCUMULATE_SAMPLES:
                # 取出一段处理，剩余的留在 buffer 里继续积累
                chunk = self._quality_buffer[:QUALITY_MODE_CHUNK_SAMPLES]
                self._quality_buffer = self._quality_buffer[QUALITY_MODE_CHUNK_SAMPLES:]
                self._quality_buffer_samples = len(self._quality_buffer)

                await self._process_quality_chunk(chunk)
            return

        try:
            segment = self.vad.feed(audio_data)
        except Exception as e:
            print(f"[管道] VAD 检测异常: {e}")
            return

        if segment:
            self._segment_stats["total_segments"] += 1
            if segment.segment_reason == "silence_timeout":
                self._segment_stats["silence_timeout"] += 1
            elif segment.segment_reason == "voice_change":
                self._segment_stats["voice_change"] += 1

            duration_s = len(segment.audio_data) / 16000.0
            MIN_SEGMENT_DURATION_S = 0.5
            if duration_s < MIN_SEGMENT_DURATION_S:
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

                    merged_segment = self._merge_segments(segments_to_commit, final_label)

                    merged_segment._committed_ids = committed_ids
                    merged_segment._committed_labels = committed_labels

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
                track_id = getattr(segment, '_track_id', 0)
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

    async def _process_quality_chunk(self, chunk: np.ndarray):
        """质量优先模式：积累够音频后，用离线方式处理整个 chunk。"""
        if len(chunk) == 0:
            return
        chunk_duration_s = len(chunk) / 16000.0
        print(f"[管道-质量] 处理音频块 (样本={len(chunk)}, {chunk_duration_s:.1f}s)", flush=True)

        # 对整个 chunk 做离线 ASR（funasr 批量模式）
        try:
            result = self.asr_model.generate(
                input=chunk,
                batch_size_s=300,
                is_streaming=False,  # 离线模式，精度更高
                language=self.language,
            )
            full_text = ""
            for item in result:
                if isinstance(item, dict):
                    t = item.get("text", "")
                elif isinstance(item, str):
                    t = item.strip()
                else:
                    t = str(item)
                full_text += t

            if not full_text.strip():
                return

            # 用整个 chunk 提取一个声纹（从 15 秒音频，embedding 更稳定）
            speaker_label = None
            speaker_sims = {}
            emb_for_segment = None
            try:
                if self._embedding_extractor:
                    emb_for_segment = self._embedding_extractor.extract(chunk)
                if emb_for_segment is not None and np.linalg.norm(emb_for_segment) > 1e-6:
                    if self._use_enhanced_engine and self._enhanced_registry:
                        id_result = self._enhanced_registry.identify(chunk, track_id=0)
                        if id_result and id_result.matches:
                            top = id_result.matches[0]
                            _c1 = getattr(top, "cosine_score", 0.0)
                            _c2 = id_result.matches[1].cosine_score if len(id_result.matches) > 1 else 0.0
                            _need = ENHANCED_SPEAKER_COS_THRESHOLD + (0.10 if (len(chunk) / 16.0) < SPEAKER_ID_SHORT_MS else 0.0)
                            if _c1 >= _need and (_c1 - _c2) >= SPEAKER_ID_GAP_MIN:
                                speaker_label = top.speaker_id
                                speaker_sims = {m.speaker_id: m.final_score for m in id_result.matches}
                    elif self._registered_speakers:
                        result2 = self.speaker.extract_and_compare(
                            chunk, self._registered_speakers, threshold=SPEAKER_SIMILARITY_THRESHOLD
                        )
                        if result2:
                            speaker_label = result2[0]
                            speaker_sims = result2[2] if len(result2) > 2 else {}
            except Exception as e:
                print(f"[管道-质量] 声纹识别失败: {e}", flush=True)

            if not speaker_label:
                speaker_label = "unknown"

            # 标点恢复
            if self.punc_model and full_text.strip():
                try:
                    punc_result = self.punc_model.generate(input=full_text)
                    if punc_result and len(punc_result) > 0:
                        full_text = punc_result[0].get("text", full_text) if isinstance(punc_result[0], dict) else str(punc_result[0])
                except Exception:
                    pass

            # 构造一个假的 segment 用于TranscriptDelta
            from dataclasses import replace
            fake_segment = SpeechSegment(
                audio_data=chunk,
                start_ms=0,
                end_ms=int(chunk_duration_s * 1000),
                segment_reason="quality_mode",
                speaker_id=speaker_label,
                speaker_label=speaker_label,
                speaker_name=speaker_label,
            )
            fake_segment.embedding = emb_for_segment
            fake_segment.speaker_sims = speaker_sims

            # 通过 on_transcript 回调推送结果
            if self.on_transcript:
                delta = TranscriptDelta(
                    text=full_text,
                    is_final=True,
                    start_ms=0,
                    end_ms=int(chunk_duration_s * 1000),
                    speaker_id=speaker_label,
                    speaker_label=speaker_label,
                    speaker_name=speaker_label,
                    speaker_confidence=speaker_sims.get(speaker_label, 0.0) if speaker_sims else 0.0,
                    speaker_candidates=[(sid, speaker_sims[sid]) for sid in sorted(speaker_sims, key=lambda x: -speaker_sims[x])[:3]] if speaker_sims else None,
                    registered_speaker_sims=speaker_sims,
                )
                delta.text = _clean_asr_text(delta.text)   # 最终清洗(在 punc 之后)
                result_cb = self.on_transcript(delta)
                if asyncio.iscoroutine(result_cb):
                    await result_cb

        except Exception as e:
            print(f"[管道-质量] ASR 处理失败: {e}", flush=True)
            import traceback
            traceback.print_exc()

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

        # 关闭 overlap 拼接：VAD 按静音切句，边界已是完整词，拼上一段 tail 反而让边界词被识别两次
        overlap_tail = None
        if overlap_tail is not None and len(overlap_tail) > 0:
            prepend_samples = len(overlap_tail)
            prepend_ms = int(prepend_samples / self.vad.sample_rate * 1000)
            audio_data_original_samples = len(audio_data)
            audio_data_original_ms = int(audio_data_original_samples / self.vad.sample_rate * 1000)
            audio_data = np.concatenate([overlap_tail, audio_data])
            actual_start_ms = segment_start_ms - prepend_ms
            actual_end_ms = segment_end_ms
            segment_start_ms = actual_start_ms
        else:
            pass

        def _do_speaker_recognition() -> None:
            if not self._registered_speakers and not self._use_enhanced_engine:
                return
            track_id = getattr(merged_segment, '_track_id', None)
            try:
                if self._use_enhanced_engine and self._enhanced_registry:
                    if self._multi_window_enabled:
                        result, stats = self._enhanced_registry.identify_with_voting(
                            audio_data,
                            n_windows=self._multi_window_n,
                            window_step_ratio=self._multi_window_step,
                            vote_method=self._multi_window_vote,
                            use_multi_window=False,
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

        def on_delta(delta: TranscriptDelta):
            track_id = getattr(merged_segment, '_track_id', 0) if merged_segment else 0
            delta._track_id = track_id
            delta._committed_ids = getattr(merged_segment, '_committed_ids', []) if merged_segment else []
            delta._committed_labels = getattr(merged_segment, '_committed_labels', []) if merged_segment else []
            if merged_segment:
                delta.start_ms = getattr(merged_segment, 'start_ms', 0) or 0
                delta.end_ms = getattr(merged_segment, 'end_ms', 0) or 0
            if delta.start_ms == 0 and segment_start_ms:
                delta.start_ms = segment_start_ms
                delta.end_ms = segment_end_ms

            if speaker_label:
                delta.speaker_id = speaker_label
                delta.speaker_confidence = 1.0
                delta.speaker_candidates = [(speaker_label, None, 1.0)]
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
        self._inactivity_running = False
        final_asr_tasks = []
        if self._inactivity_task:
            self._inactivity_task.cancel()
            try:
                await self._inactivity_task
            except asyncio.CancelledError:
                pass
            self._inactivity_task = None

        # 质量模式：清空剩余积累 buffer
        if self._quality_mode and len(self._quality_buffer) > 0:
            remaining = self._quality_buffer
            self._quality_buffer = np.array([], dtype=np.float32)
            self._quality_buffer_samples = 0
            print(f"[管道-质量] 停止时清空剩余 {len(remaining) / 16000:.1f}s 音频", flush=True)
            final_asr_tasks.append(asyncio.create_task(self._process_quality_chunk(remaining)))

        if self._merge_enabled:
            pending_segments = self._flush_pending_segments()
            if pending_segments:
                confirmed_label = pending_segments[0].speaker_label if pending_segments else None
                merged_segment = self._merge_segments(pending_segments, confirmed_label)
                final_asr_tasks.append(asyncio.create_task(
                    self._run_streaming_asr_locked(
                        merged_segment.audio_data,
                        merged_segment.segment_reason,
                        merged_segment.overlap_tail,
                        merged_segment.start_ms,
                        merged_segment.end_ms,
                        merged_segment.speaker_label,
                        merged_segment,
                    )
                ))

        if final_asr_tasks:
            results = await asyncio.gather(*final_asr_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    print(f"[管道] 停止时最终 ASR 处理失败: {result}", flush=True)

        if self._process_task:
            self._transcript_queue.put_nowait(None)
            try:
                await asyncio.wait_for(self._process_task, timeout=10)
            except asyncio.CancelledError:
                pass
            except asyncio.TimeoutError:
                self._process_task.cancel()
                try:
                    await self._process_task
                except asyncio.CancelledError:
                    pass
            self._process_task = None
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

    def set_inactivity_timeout(self, timeout_ms: float):
        self._inactivity_timeout_ms = timeout_ms

    def set_quality_mode(self, enabled: bool):
        """开启/关闭质量优先模式。开启后音频先积累 15 秒再处理。"""
        self._quality_mode = enabled
        if not enabled:
            # 切回实时模式时，清空积累 buffer
            self._quality_buffer = np.array([], dtype=np.float32)
            self._quality_buffer_samples = 0
            self._quality_accumulated_ms = 0
            self._merge_max_wait_ms = 10000.0
            print(f"[管道] 质量优先模式已关闭，恢复实时模式", flush=True)
        else:
            self._merge_max_wait_ms = QUALITY_MODE_MERGE_MAX_WAIT_MS
            print(f"[管道] 质量优先模式已开启，积累 {QUALITY_MODE_CHUNK_SAMPLES // VAD_SAMPLE_RATE}s 后处理", flush=True)

    async def _inactivity_check_loop(self):
        check_interval = 1.0
        while self._inactivity_running:
            await asyncio.sleep(check_interval)

            with self._pending_lock:
                if not self._pending_segments:
                    continue

                elapsed = (time.time() - self._last_audio_time) * 1000
                if elapsed < self._inactivity_timeout_ms:
                    continue

                segments_to_commit = [s for s in self._pending_segments if s._pending]
                for s in segments_to_commit:
                    s._pending = False
                self._pending_segments.clear()
                self._segment_stats["timeout_submit"] += 1
                self._segment_stats["total_merged"] += 1

            if segments_to_commit:
                committed_ids = [f"#{getattr(s, '_track_id', '?'):04d}" for s in segments_to_commit]
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
