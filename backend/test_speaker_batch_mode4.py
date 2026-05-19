"""
模式四：批量声纹识别算法测试脚本（适配 smart-meeting-ai）

功能：
1. 从测试数据集加载注册音频，提取声纹并注册
2. 对测试集中的每个音频片段进行识别
3. 支持多种识别算法对比，输出准确率统计

用法：
  python test_speaker_batch_mode4.py                          # 运行全量对比（所有算法）
  python test_speaker_batch_mode4.py --compare               # 同上
  python test_speaker_batch_mode4.py -m baseline_cosine     # 单算法测试
  python test_speaker_batch_mode4.py -n 200                 # 指定样本数
  python test_speaker_batch_mode4.py --detail 10            # 展示10个样本的详细得分对比
  python test_speaker_batch_mode4.py -s                      # 保存结果到JSON
  python test_speaker_batch_mode4.py --compare -n 200 -s    # 对比+保存
"""

import argparse
import copy
import json
import os
import random
import sys
import time
import wave
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# ─────────────────────────────────────────────────────────────
# 路径设置（适配 smart-meeting-ai 项目结构）
# ─────────────────────────────────────────────────────────────
BACKEND_DIR = Path(__file__).parent
PROJECT_ROOT = BACKEND_DIR.parent
TEST_DATA_DIR = BACKEND_DIR / "data" / "test_audio" / "test_run_20260516_191245"
REGISTRATION_FILE = TEST_DATA_DIR / "test_run_20260516_191245_registration.json"
GROUND_TRUTH_FILE = TEST_DATA_DIR / "test_run_20260516_191245_ground_truth.json"
OUTPUT_DIR = BACKEND_DIR / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

# 注册音频目录（和 ground_truth.json 中的 audio_file 相对路径一致）
AUDIO_DIR = TEST_DATA_DIR


class RecognitionMode(Enum):
    """支持的识别算法模式"""

    # ===== 基础对比基准 =====
    BASELINE_COSINE = "baseline_cosine"
    BASELINE_L2 = "baseline_l2"

    # ===== Embedding 预处理 =====
    CENTER_SUBTRACT = "center_subtract"
    NORMALIZE_BEFORE = "normalize_before"
    DIMENSION_WEIGHTED = "dimension_weighted"
    PCA_WHITENING = "pca_whitening"

    # ===== 相似度度量 =====
    COSINE_PLUS_L2 = "cosine_plus_l2"
    ANGLE_MAGNITUDE = "angle_magnitude"
    MAHALANOBIS = "mahalanobis"
    SOFTMAX_SCORE = "softmax_score"

    # ===== 匹配策略 =====
    TOPK_VERIFY = "topk_verify"
    DIFF_THRESHOLD = "diff_threshold"
    CONFIDENCE_CALIBRATE = "confidence_calibrate"
    BAYESIAN_POSTERIOR = "bayesian_posterior"

    # ===== 融合方法 =====
    MEDIAN_FUSION = "median_fusion"
    WEIGHTED_WINDOW = "weighted_window"
    TRIMMED_MEAN = "trimmed_mean"
    GEOMETRIC_MEAN = "geometric_mean"

    # ===== 多算法集成 =====
    ENSEMBLE_VOTE = "ensemble_vote"
    ENSEMBLE_WEIGHTED = "ensemble_weighted"


# 系统级真随机（避免种子的可预测性）
_system_random = random.SystemRandom()


# ─────────────────────────────────────────────────────────────
# 数据结构
# ─────────────────────────────────────────────────────────────

@dataclass
class TestResult:
    segment_id: int
    audio_path: str
    ground_truth_speaker_id: str
    recognized_speaker_id: Optional[str]
    recognized_speaker_name: Optional[str]
    recognized_score: float
    is_correct: bool
    top3_includes_correct: bool
    duration_sec: float
    algorithm_used: str = ""
    all_scores: Dict[str, float] = field(default_factory=dict)
    uncertainty_flag: bool = False


@dataclass
class TestSummary:
    total_segments: int
    top1_correct: int
    top1_accuracy: float
    top3_correct: int
    top3_accuracy: float
    uncertain_count: int
    results: List[TestResult]
    algorithm_name: str = ""


@dataclass
class GlobalStats:
    embeddings: List[np.ndarray] = field(default_factory=list)
    mean_embedding: Optional[np.ndarray] = None
    dim_variance: Optional[np.ndarray] = None
    dim_weights: Optional[np.ndarray] = None
    pca_matrix: Optional[np.ndarray] = None
    pca_scaler: Optional[object] = None
    pca_fitted: bool = False
    speaker_centroids: Dict[str, np.ndarray] = field(default_factory=dict)


# ─────────────────────────────────────────────────────────────
# 声纹识别测试器
# ─────────────────────────────────────────────────────────────

class SpeakerRecognitionTester:
    def __init__(self):
        self.extractor = None
        self.device = "cuda"
        self.registration_data: Dict = {}
        self.ground_truth_data: Dict = {}

        # 存储已注册的声纹：speaker_id -> list of embeddings
        self.registered_embeddings: Dict[str, List[np.ndarray]] = {}

        # 存储 speaker metadata
        self.speaker_info: Dict[str, dict] = {}

        # 全局统计（用于 PCA、中心化等）
        self.global_stats = GlobalStats()

        self._pca_fitted = False

    # ─────────────────────────────────────────────────────────
    # 资源加载
    # ─────────────────────────────────────────────────────────

    def load_resources(self):
        print("=" * 60)
        print("模式四：批量声纹识别算法测试（smart-meeting-ai）")
        print("=" * 60)

        # 加载注册配置
        print("\n[1/4] 加载注册配置...")
        with open(REGISTRATION_FILE, "r", encoding="utf-8") as f:
            self.registration_data = json.load(f)
        print(f"  注册配置: {self.registration_data['num_speakers']} 位说话人")

        # 加载 ground truth
        print("\n[2/4] 加载测试集...")
        with open(GROUND_TRUTH_FILE, "r", encoding="utf-8") as f:
            self.ground_truth_data = json.load(f)
        meta = self.ground_truth_data["_meta"]
        print(f"  测试集: {meta['num_segments']} 个片段, "
              f"{meta['total_duration_sec']:.1f}s, {meta['num_speakers']} 位说话人")

        # 构建 speaker info 映射
        for sp in meta["speakers"]:
            self.speaker_info[sp["speaker_id"]] = sp

        # 初始化 CAM++ 模型
        print("\n[3/4] 初始化 CAM++ 声纹模型...")
        sys.path.insert(0, str(BACKEND_DIR))
        from app.asr.model_manager import get_model_manager

        model_mgr = get_model_manager()
        if not model_mgr.is_initialized():
            print("  模型未加载，初始化中...")
            import asyncio
            asyncio.run(model_mgr.initialize(load_funasr=False, load_vad=False))

        camp_model = model_mgr.get_camp_model()
        if camp_model is None:
            raise RuntimeError("CAM++ 模型加载失败，请检查模型路径")

        from app.asr.model_manager import SpeakerEmbeddingExtractor
        self.extractor = SpeakerEmbeddingExtractor(
            camp_model=camp_model,
            device=model_mgr.device,
        )
        self.device = model_mgr.device
        print(f"  模型加载完成，设备: {self.device}")

        # 提取注册音频的声纹
        print("\n[4/4] 提取注册音频声纹...")
        self._extract_registration_embeddings()

        # 预计算全局统计
        self._compute_global_stats()

        print(f"\n✅ 资源加载完成：{len(self.registered_embeddings)} 位说话人已注册")

    def _load_wav(self, filepath: str) -> Optional[Tuple[np.ndarray, int]]:
        """加载 WAV 音频，返回 (audio_data, sample_rate)"""
        try:
            with wave.open(filepath, "rb") as wf:
                n_channels = wf.getnchannels()
                sample_width = wf.getsampwidth()
                framerate = wf.getframerate()
                n_frames = wf.getnframes()
                frames = wf.readframes(n_frames)

                if sample_width == 2:
                    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                else:
                    audio = np.frombuffer(frames, dtype=np.float32)

                if n_channels > 1:
                    audio = audio.reshape(-1, n_channels).mean(axis=1)

                if framerate != 16000:
                    import scipy.signal as signal
                    num_samples = int(len(audio) * 16000 / framerate)
                    audio = signal.resample(audio, num_samples)

                return audio.astype(np.float32), 16000
        except Exception as e:
            print(f"    ❌ 加载失败 {filepath}: {e}")
            return None

    def _extract_registration_embeddings(self):
        """从注册音频中提取声纹"""
        speakers = self.registration_data["speakers"]

        # 先扫描所有音频文件
        all_wav_files = list(AUDIO_DIR.glob("*.wav"))
        file_map = {f.stem: str(f) for f in all_wav_files}

        for sp in speakers:
            speaker_id = sp["speaker_id"]
            name = sp["name"]
            num_segs = sp["num_segments"]

            # 找该说话人的注册音频（音频名包含 speaker_id）
            emb_list = []
            for wav_file in all_wav_files:
                # ground_truth 中的 audio_file 形如 "619c5c1f-a5e7-4e99-bf22-51b6c6cab465.wav"
                # registration 里没有直接列出文件名，我们通过遍历 ground_truth 中该说话人的片段来查找
                pass

            # 从 ground_truth 中收集该说话人的所有音频
            segments = [s for s in self.ground_truth_data["segments"]
                        if s["speaker_id"] == speaker_id]

            # 取前 num_segs 个作为注册音频
            reg_segs = segments[:num_segs]

            for seg in reg_segs:
                audio_file = seg["audio_file"]
                wav_path = AUDIO_DIR / audio_file
                if not wav_path.exists():
                    continue

                audio_data = self._load_wav(str(wav_path))
                if audio_data is None:
                    continue
                audio, _ = audio_data

                if len(audio) < 16000:  # 至少 1 秒
                    continue

                try:
                    emb = self.extractor.extract(audio)
                    if emb is not None and np.linalg.norm(emb) > 1e-6:
                        emb_list.append(emb)
                except Exception as e:
                    print(f"    提取声纹失败 {audio_file}: {e}")

            if emb_list:
                self.registered_embeddings[speaker_id] = emb_list
                print(f"  注册: {name}({speaker_id}) - {len(emb_list)} 个声纹样本")

        # 计算每个说话人的质心
        for speaker_id, emb_list in self.registered_embeddings.items():
            stacked = np.array(emb_list)
            centroid = np.mean(stacked, axis=0)
            centroid = centroid / (np.linalg.norm(centroid) + 1e-8)
            self.global_stats.speaker_centroids[speaker_id] = centroid

    def _compute_global_stats(self):
        """收集全局 embedding 用于 PCA、中心化等"""
        all_embs = []
        for emb_list in self.registered_embeddings.values():
            all_embs.extend(emb_list)

        if not all_embs:
            return

        stacked = np.array(all_embs)
        self.global_stats.embeddings = all_embs
        self.global_stats.mean_embedding = np.mean(stacked, axis=0)
        self.global_stats.dim_variance = np.var(stacked, axis=0)

        max_var = np.max(self.global_stats.dim_variance) + 1e-8
        self.global_stats.dim_weights = self.global_stats.dim_variance / max_var
        print(f"  全局统计: {len(all_embs)} 个 embedding, 维度={len(self.global_stats.mean_embedding)}")

    def _fit_pca(self, n_components: int = 64):
        """拟合 PCA 变换矩阵"""
        if self.global_stats.pca_fitted:
            return

        from sklearn.decomposition import PCA
        from sklearn.preprocessing import StandardScaler

        all_embs = self.global_stats.embeddings
        if len(all_embs) < 10:
            print("    样本不足，跳过 PCA")
            return

        stacked = np.array(all_embs)
        scaler = StandardScaler()
        scaled = scaler.fit_transform(stacked)

        n_comp = min(n_components, len(all_embs) - 1, stacked.shape[1])
        pca = PCA(n_components=n_comp)
        transformed = pca.fit_transform(scaled)

        self.global_stats.pca_matrix = pca.components_.T
        self.global_stats.pca_scaler = scaler
        self.global_stats.pca_fitted = True
        print(f"  PCA 拟合: 保留 {n_comp} 个主成分，解释方差: {sum(pca.explained_variance_ratio_) * 100:.1f}%")

    # ─────────────────────────────────────────────────────────
    # 核心识别算法
    # ─────────────────────────────────────────────────────────

    @staticmethod
    def _cosine(a: np.ndarray, b: np.ndarray) -> float:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a < 1e-8 or norm_b < 1e-8:
            return 0.5
        return float(np.dot(a, b) / (norm_a * norm_b))

    @staticmethod
    def _l2(a: np.ndarray, b: np.ndarray) -> float:
        n1 = a / (np.linalg.norm(a) + 1e-8)
        n2 = b / (np.linalg.norm(b) + 1e-8)
        dist = np.linalg.norm(n1 - n2)
        return 1.0 / (1.0 + dist)

    def _get_centroid(self, speaker_id: str) -> Optional[np.ndarray]:
        return self.global_stats.speaker_centroids.get(speaker_id)

    # ---- 基线 ----

    def _score_baseline_cosine(self, emb: np.ndarray) -> Dict[str, float]:
        scores = {}
        for sid, centroid in self.global_stats.speaker_centroids.items():
            scores[sid] = self._cosine(emb, centroid)
        return scores

    def _score_baseline_l2(self, emb: np.ndarray) -> Dict[str, float]:
        scores = {}
        for sid, centroid in self.global_stats.speaker_centroids.items():
            scores[sid] = self._l2(emb, centroid)
        return scores

    # ---- Embedding 预处理 ----

    def _score_center_subtract(self, emb: np.ndarray) -> Dict[str, float]:
        if self.global_stats.mean_embedding is None:
            return self._score_baseline_cosine(emb)
        centered = emb - self.global_stats.mean_embedding
        centered = centered / (np.linalg.norm(centered) + 1e-8)
        scores = {}
        for sid, centroid in self.global_stats.speaker_centroids.items():
            reg_c = centroid - self.global_stats.mean_embedding
            reg_c = reg_c / (np.linalg.norm(reg_c) + 1e-8)
            scores[sid] = self._cosine(centered, reg_c)
        return scores

    def _score_normalize_before(self, emb: np.ndarray) -> Dict[str, float]:
        norm_emb = emb / (np.linalg.norm(emb) + 1e-8)
        scores = {}
        for sid, centroid in self.global_stats.speaker_centroids.items():
            reg_n = centroid / (np.linalg.norm(centroid) + 1e-8)
            scores[sid] = self._cosine(norm_emb, reg_n)
        return scores

    def _score_dimension_weighted(self, emb: np.ndarray) -> Dict[str, float]:
        if self.global_stats.dim_weights is None:
            return self._score_baseline_cosine(emb)
        weights = self.global_stats.dim_weights
        w_emb = emb * weights
        w_emb = w_emb / (np.linalg.norm(w_emb) + 1e-8)
        scores = {}
        for sid, centroid in self.global_stats.speaker_centroids.items():
            w_reg = centroid * weights
            w_reg = w_reg / (np.linalg.norm(w_reg) + 1e-8)
            scores[sid] = self._cosine(w_emb, w_reg)
        return scores

    def _score_pca_whitening(self, emb: np.ndarray) -> Dict[str, float]:
        self._fit_pca()
        if not self.global_stats.pca_fitted:
            return self._score_baseline_cosine(emb)
        try:
            scaled = self.global_stats.pca_scaler.transform(emb.reshape(1, -1))
            transformed = np.dot(scaled, self.global_stats.pca_matrix)
            scores = {}
            for sid, centroid in self.global_stats.speaker_centroids.items():
                reg_s = self.global_stats.pca_scaler.transform(centroid.reshape(1, -1))
                reg_t = np.dot(reg_s, self.global_stats.pca_matrix)
                scores[sid] = self._cosine(transformed[0], reg_t[0])
            return scores
        except Exception:
            return self._score_baseline_cosine(emb)

    # ---- 相似度度量 ----

    def _score_cosine_plus_l2(self, emb: np.ndarray) -> Dict[str, float]:
        cos_scores = self._score_baseline_cosine(emb)
        l2_scores = self._score_baseline_l2(emb)
        return {sid: 0.6 * cos_scores[sid] + 0.4 * l2_scores[sid]
                for sid in cos_scores}

    def _score_angle_magnitude(self, emb: np.ndarray) -> Dict[str, float]:
        norm_emb = emb / (np.linalg.norm(emb) + 1e-8)
        mag_emb = np.linalg.norm(emb)
        scores = {}
        for sid, centroid in self.global_stats.speaker_centroids.items():
            reg_n = centroid / (np.linalg.norm(centroid) + 1e-8)
            reg_m = np.linalg.norm(centroid)
            angle_sim = self._cosine(norm_emb, reg_n)
            mag_diff = abs(mag_emb - reg_m)
            mag_sim = float(np.exp(-mag_diff ** 2 / 0.1))
            scores[sid] = 0.85 * angle_sim + 0.15 * mag_sim
        return scores

    def _score_mahalanobis(self, emb: np.ndarray) -> Dict[str, float]:
        if self.global_stats.dim_variance is None:
            return self._score_baseline_cosine(emb)
        var = self.global_stats.dim_variance + 1e-6
        scores = {}
        for sid, centroid in self.global_stats.speaker_centroids.items():
            diff = emb - centroid
            mahal_sq = float(np.sum(diff ** 2 / var))
            scores[sid] = 1.0 / (1.0 + mahal_sq ** 0.5)
        return scores

    def _score_softmax(self, emb: np.ndarray, temperature: float = 0.05) -> Dict[str, float]:
        raw = self._score_baseline_cosine(emb)
        if not raw:
            return {}
        speakers = list(raw.keys())
        values = np.array(list(raw.values()))
        exp_v = np.exp((values - np.max(values)) / temperature)
        softmax_v = exp_v / np.sum(exp_v)
        return {sp: float(sv) for sp, sv in zip(speakers, softmax_v)}

    # ---- 匹配策略 ----

    def _score_topk_verify(self, emb: np.ndarray, top_k: int = 5) -> Tuple[Dict[str, float], bool]:
        raw = self._score_baseline_cosine(emb)
        if not raw:
            return {}, False
        sorted_speakers = sorted(raw.items(), key=lambda x: x[1], reverse=True)
        top_candidates = [s[0] for s in sorted_speakers[:top_k]]
        return {s: raw[s] for s in top_candidates}, False

    def _score_diff_threshold(self, emb: np.ndarray, threshold: float = 0.05) -> Tuple[Dict[str, float], bool]:
        raw = self._score_baseline_cosine(emb)
        if not raw:
            return {}, False
        sorted_vals = sorted(raw.values(), reverse=True)
        uncertain = len(sorted_vals) >= 2 and (sorted_vals[0] - sorted_vals[1]) < threshold
        return raw, uncertain

    def _score_confidence_calibrate(self, emb: np.ndarray) -> Dict[str, float]:
        raw = self._score_baseline_cosine(emb)
        if not raw:
            return {}
        values = np.array(list(raw.values()))
        v_max = np.max(values)
        v_min = np.min(values)
        scale = 10.0
        centered = values - (v_max + v_min) / 2
        calibrated = 1.0 / (1.0 + np.exp(-centered * scale))
        speakers = list(raw.keys())
        return {sp: float(cv) for sp, cv in zip(speakers, calibrated)}

    def _score_bayesian(self, emb: np.ndarray, alpha: float = 0.1) -> Dict[str, float]:
        likelihood = self._score_baseline_cosine(emb)
        if not likelihood:
            return {}
        speakers = list(likelihood.keys())
        prior = {sp: 1.0 / len(speakers) for sp in speakers}
        posterior = {sp: likelihood[sp] * (prior[sp] ** alpha) for sp in speakers}
        total = sum(posterior.values())
        if total > 0:
            posterior = {sp: p / total for sp, p in posterior.items()}
        return posterior

    # ---- 融合方法（多窗口）----

    def _score_median_fusion(self, audio: np.ndarray) -> Dict[str, float]:
        try:
            windows = self.extractor.extract_multi_window(audio, n_windows=5, window_step_ratio=0.2)
        except Exception:
            windows = []
        valid = [emb for emb, _, ok in windows if ok]

        if not valid:
            emb = self.extractor.extract(audio)
            if emb is None or np.linalg.norm(emb) < 1e-6:
                return {}
            return self._score_baseline_cosine(emb)

        stacked = np.array(valid)
        if len(valid) == 1:
            fused = valid[0]
        else:
            fused = np.median(stacked, axis=0)
        fused = fused / (np.linalg.norm(fused) + 1e-8)
        return self._score_baseline_cosine(fused)

    def _score_weighted_window(self, audio: np.ndarray) -> Dict[str, float]:
        try:
            windows = self.extractor.extract_multi_window(audio, n_windows=5, window_step_ratio=0.2)
        except Exception:
            windows = []
        valid = [emb for emb, _, ok in windows if ok]

        if not valid:
            emb = self.extractor.extract(audio)
            if emb is None or np.linalg.norm(emb) < 1e-6:
                return {}
            return self._score_baseline_cosine(emb)

        mean_emb = np.mean(valid, axis=0)
        mean_emb = mean_emb / (np.linalg.norm(mean_emb) + 1e-8)

        weights = []
        for emb in valid:
            emb_n = emb / (np.linalg.norm(emb) + 1e-8)
            sim = self._cosine(emb_n, mean_emb)
            weights.append(max(sim, 0.5))

        total_w = sum(weights)
        fused = np.zeros_like(valid[0])
        for emb, w in zip(valid, weights):
            fused += emb * (w / total_w)
        fused = fused / (np.linalg.norm(fused) + 1e-8)
        return self._score_baseline_cosine(fused)

    def _score_trimmed_mean(self, audio: np.ndarray, trim_ratio: float = 0.2) -> Dict[str, float]:
        try:
            windows = self.extractor.extract_multi_window(audio, n_windows=5, window_step_ratio=0.2)
        except Exception:
            windows = []
        valid = [emb for emb, _, ok in windows if ok]

        if not valid:
            emb = self.extractor.extract(audio)
            if emb is None or np.linalg.norm(emb) < 1e-6:
                return {}
            return self._score_baseline_cosine(emb)

        if len(valid) < 3:
            stacked = np.array(valid)
            fused = np.mean(stacked, axis=0)
            fused = fused / (np.linalg.norm(fused) + 1e-8)
            return self._score_baseline_cosine(fused)

        stacked = np.array(valid)
        n = len(valid)
        trim_count = max(1, int(n * trim_ratio))
        sorted_idx = np.argsort(stacked, axis=0)
        to_delete = np.concatenate([sorted_idx[:trim_count, :], sorted_idx[-trim_count:, :]])
        trimmed = np.delete(stacked, np.unique(to_delete), axis=0)

        if len(trimmed) == 0:
            fused = np.mean(stacked, axis=0)
        else:
            fused = np.mean(trimmed, axis=0)
        fused = fused / (np.linalg.norm(fused) + 1e-8)
        return self._score_baseline_cosine(fused)

    def _score_geometric_mean(self, audio: np.ndarray) -> Dict[str, float]:
        try:
            windows = self.extractor.extract_multi_window(audio, n_windows=5, window_step_ratio=0.2)
        except Exception:
            windows = []
        valid = [emb for emb, _, ok in windows if ok]

        if not valid:
            emb = self.extractor.extract(audio)
            if emb is None or np.linalg.norm(emb) < 1e-6:
                return {}
            return self._score_baseline_cosine(emb)

        stacked = np.array(valid)
        eps = 1e-10
        stacked_pos = np.abs(stacked) + eps
        log_mean = np.mean(np.log(stacked_pos), axis=0)
        fused = np.exp(log_mean)
        sign = np.sign(np.mean(stacked, axis=0))
        fused = fused * sign
        fused = fused / (np.linalg.norm(fused) + 1e-8)
        return self._score_baseline_cosine(fused)

    # ---- 集成方法 ----

    def _score_ensemble(self, emb: np.ndarray, mode: str = "vote") -> Dict[str, float]:
        algos = [
            self._score_baseline_cosine,
            self._score_cosine_plus_l2,
            self._score_center_subtract,
            self._score_normalize_before,
        ]
        all_scores = [f(emb) for f in algos]
        all_scores = [s for s in all_scores if s]

        if not all_scores:
            return {}

        if mode == "vote":
            votes: Dict[str, float] = {sid: 0.0 for sid in self.global_stats.speaker_centroids}
            for scores in all_scores:
                sorted_s = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                for rank, (sid, _) in enumerate(sorted_s):
                    if sid in votes:
                        votes[sid] += 1.0 / (1.0 + rank)
            total = sum(votes.values())
            if total > 0:
                votes = {sid: v / total for sid, v in votes.items()}
            return votes
        else:
            all_speakers = set()
            for scores in all_scores:
                all_speakers.update(scores.keys())
            aligned = []
            for scores in all_scores:
                aligned.append(np.array([scores.get(sp, 0.0) for sp in all_speakers]))
            avg = np.mean(aligned, axis=0)
            return {sp: float(score) for sp, score in zip(all_speakers, avg)}

    # ─────────────────────────────────────────────────────────
    # 算法调度
    # ─────────────────────────────────────────────────────────

    ALGO_MAP = {
        RecognitionMode.BASELINE_COSINE: ("baseline_cosine", False),
        RecognitionMode.BASELINE_L2: ("baseline_l2", False),
        RecognitionMode.CENTER_SUBTRACT: ("center_subtract", False),
        RecognitionMode.NORMALIZE_BEFORE: ("normalize_before", False),
        RecognitionMode.DIMENSION_WEIGHTED: ("dimension_weighted", False),
        RecognitionMode.PCA_WHITENING: ("pca_whitening", False),
        RecognitionMode.COSINE_PLUS_L2: ("cosine_plus_l2", False),
        RecognitionMode.ANGLE_MAGNITUDE: ("angle_magnitude", False),
        RecognitionMode.MAHALANOBIS: ("mahalanobis", False),
        RecognitionMode.SOFTMAX_SCORE: ("softmax_score", False),
        RecognitionMode.TOPK_VERIFY: ("topk_verify", True),
        RecognitionMode.DIFF_THRESHOLD: ("diff_threshold", True),
        RecognitionMode.CONFIDENCE_CALIBRATE: ("confidence_calibrate", False),
        RecognitionMode.BAYESIAN_POSTERIOR: ("bayesian_posterior", False),
        RecognitionMode.MEDIAN_FUSION: ("median_fusion", False),
        RecognitionMode.WEIGHTED_WINDOW: ("weighted_window", False),
        RecognitionMode.TRIMMED_MEAN: ("trimmed_mean", False),
        RecognitionMode.GEOMETRIC_MEAN: ("geometric_mean", False),
        RecognitionMode.ENSEMBLE_VOTE: ("ensemble_vote", False),
        RecognitionMode.ENSEMBLE_WEIGHTED: ("ensemble_weighted", False),
    }

    def _run_algorithm(self, audio: np.ndarray, mode: RecognitionMode) -> Tuple[Dict[str, float], bool]:
        _, has_uncertainty = self.ALGO_MAP[mode]

        # 需要音频的算法（多窗口融合）
        needs_audio = mode in [
            RecognitionMode.MEDIAN_FUSION,
            RecognitionMode.WEIGHTED_WINDOW,
            RecognitionMode.TRIMMED_MEAN,
            RecognitionMode.GEOMETRIC_MEAN,
        ]

        if needs_audio:
            if mode == RecognitionMode.MEDIAN_FUSION:
                return self._score_median_fusion(audio), False
            elif mode == RecognitionMode.WEIGHTED_WINDOW:
                return self._score_weighted_window(audio), False
            elif mode == RecognitionMode.TRIMMED_MEAN:
                return self._score_trimmed_mean(audio), False
            elif mode == RecognitionMode.GEOMETRIC_MEAN:
                return self._score_geometric_mean(audio), False

        # 提取 embedding
        emb = self.extractor.extract(audio)
        if emb is None or np.linalg.norm(emb) < 1e-6:
            return {}, False

        if mode == RecognitionMode.BASELINE_COSINE:
            return self._score_baseline_cosine(emb), False
        elif mode == RecognitionMode.BASELINE_L2:
            return self._score_baseline_l2(emb), False
        elif mode == RecognitionMode.CENTER_SUBTRACT:
            return self._score_center_subtract(emb), False
        elif mode == RecognitionMode.NORMALIZE_BEFORE:
            return self._score_normalize_before(emb), False
        elif mode == RecognitionMode.DIMENSION_WEIGHTED:
            return self._score_dimension_weighted(emb), False
        elif mode == RecognitionMode.PCA_WHITENING:
            return self._score_pca_whitening(emb), False
        elif mode == RecognitionMode.COSINE_PLUS_L2:
            return self._score_cosine_plus_l2(emb), False
        elif mode == RecognitionMode.ANGLE_MAGNITUDE:
            return self._score_angle_magnitude(emb), False
        elif mode == RecognitionMode.MAHALANOBIS:
            return self._score_mahalanobis(emb), False
        elif mode == RecognitionMode.SOFTMAX_SCORE:
            return self._score_softmax(emb), False
        elif mode == RecognitionMode.TOPK_VERIFY:
            return self._score_topk_verify(emb)
        elif mode == RecognitionMode.DIFF_THRESHOLD:
            return self._score_diff_threshold(emb)
        elif mode == RecognitionMode.CONFIDENCE_CALIBRATE:
            return self._score_confidence_calibrate(emb), False
        elif mode == RecognitionMode.BAYESIAN_POSTERIOR:
            return self._score_bayesian(emb), False
        elif mode == RecognitionMode.ENSEMBLE_VOTE:
            return self._score_ensemble(emb, "vote"), False
        elif mode == RecognitionMode.ENSEMBLE_WEIGHTED:
            return self._score_ensemble(emb, "weighted"), False

        return self._score_baseline_cosine(emb), False

    # ─────────────────────────────────────────────────────────
    # 测试执行
    # ─────────────────────────────────────────────────────────

    def _get_test_samples(self, n: int) -> List[dict]:
        segments = self.ground_truth_data["segments"]
        if len(segments) <= n:
            return segments
        return _system_random.sample(segments, n)

    def run_single(self, n_samples: int, mode: RecognitionMode) -> TestSummary:
        algoname = mode.value
        print(f"\n[测试] 算法: {algoname}, 样本数: {n_samples}")
        print("-" * 60)

        test_samples = self._get_test_samples(n_samples)
        results: List[TestResult] = []
        uncertain_count = 0

        for idx, seg in enumerate(test_samples):
            if idx % 5 == 0:
                print(f"  进度: {idx + 1}/{len(test_samples)}")

            audio_file = seg["audio_file"]
            wav_path = AUDIO_DIR / audio_file
            if not wav_path.exists():
                continue

            audio_data = self._load_wav(str(wav_path))
            if audio_data is None:
                continue
            audio, _ = audio_data
            if len(audio) < 16000:
                continue

            try:
                scores, uncertain = self._run_algorithm(audio, mode)
                if not scores:
                    continue

                sorted_m = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                top1_id, top1_score = sorted_m[0]
                top3_ids = [s[0] for s in sorted_m[:3]]

                gt_id = seg["speaker_id"]
                gt_name = seg.get("speaker_name", gt_id)
                top1_name = self.speaker_info.get(top1_id, {}).get("speaker_name", top1_id)

                results.append(TestResult(
                    segment_id=idx + 1,
                    audio_path=audio_file,
                    ground_truth_speaker_id=gt_id,
                    recognized_speaker_id=top1_id,
                    recognized_speaker_name=top1_name,
                    recognized_score=top1_score,
                    is_correct=(top1_id == gt_id),
                    top3_includes_correct=(gt_id in top3_ids),
                    duration_sec=seg["duration_ms"] / 1000.0,
                    algorithm_used=algoname,
                    all_scores=dict(sorted_m[:10]),
                    uncertainty_flag=uncertain,
                ))
                if uncertain:
                    uncertain_count += 1

            except Exception as e:
                print(f"    ❌ {audio_file}: {e}")

        total = len(results)
        top1_correct = sum(1 for r in results if r.is_correct)
        top3_correct = sum(1 for r in results if r.top3_includes_correct)

        return TestSummary(
            total_segments=total,
            top1_correct=top1_correct,
            top1_accuracy=top1_correct / total if total > 0 else 0,
            top3_correct=top3_correct,
            top3_accuracy=top3_correct / total if total > 0 else 0,
            uncertain_count=uncertain_count,
            results=results,
            algorithm_name=algoname,
        )

    def run_comparison(self, n_samples: int) -> Dict[str, TestSummary]:
        print("\n" + "=" * 60)
        print("算法对比测试")
        print("=" * 60)

        # 先预加载所有音频
        test_samples = self._get_test_samples(n_samples)
        print(f"\n预加载 {len(test_samples)} 个测试音频...")
        audio_cache = {}
        for seg in test_samples:
            wav_path = AUDIO_DIR / seg["audio_file"]
            if wav_path.exists():
                data = self._load_wav(str(wav_path))
                if data:
                    audio_cache[seg["audio_file"]] = data[0]

        results: Dict[str, TestSummary] = {}

        for mode in RecognitionMode:
            print(f"\n{'=' * 40}")
            print(f"算法: {mode.value}")
            print(f"{'=' * 40}")

            algo_results: List[TestResult] = []
            uncertain_count = 0

            for idx, seg in enumerate(test_samples):
                audio = audio_cache.get(seg["audio_file"])
                if audio is None or len(audio) < 16000:
                    continue

                try:
                    scores, uncertain = self._run_algorithm(audio, mode)
                    if not scores:
                        continue

                    sorted_m = sorted(scores.items(), key=lambda x: x[1], reverse=True)
                    top1_id, top1_score = sorted_m[0]
                    top3_ids = [s[0] for s in sorted_m[:3]]

                    gt_id = seg["speaker_id"]
                    gt_name = seg.get("speaker_name", gt_id)
                    top1_name = self.speaker_info.get(top1_id, {}).get("speaker_name", top1_id)

                    algo_results.append(TestResult(
                        segment_id=idx + 1,
                        audio_path=seg["audio_file"],
                        ground_truth_speaker_id=gt_id,
                        recognized_speaker_id=top1_id,
                        recognized_speaker_name=top1_name,
                        recognized_score=top1_score,
                        is_correct=(top1_id == gt_id),
                        top3_includes_correct=(gt_id in top3_ids),
                        duration_sec=seg["duration_ms"] / 1000.0,
                        algorithm_used=mode.value,
                        all_scores=dict(sorted_m[:10]),
                        uncertainty_flag=uncertain,
                    ))
                    if uncertain:
                        uncertain_count += 1
                except Exception as e:
                    print(f"    ❌ {seg['audio_file']}: {e}")

            total = len(algo_results)
            top1_correct = sum(1 for r in algo_results if r.is_correct)
            top3_correct = sum(1 for r in algo_results if r.top3_includes_correct)

            summary = TestSummary(
                total_segments=total,
                top1_correct=top1_correct,
                top1_accuracy=top1_correct / total if total > 0 else 0,
                top3_correct=top3_correct,
                top3_accuracy=top3_correct / total if total > 0 else 0,
                uncertain_count=uncertain_count,
                results=algo_results,
                algorithm_name=mode.value,
            )
            results[mode.value] = summary

            print(f"  Top-1: {summary.top1_accuracy * 100:.2f}% | "
                  f"Top-3: {summary.top3_accuracy * 100:.2f}% | "
                  f"不确定: {uncertain_count}")

        return results

    # ─────────────────────────────────────────────────────────
    # 输出
    # ─────────────────────────────────────────────────────────

    def print_summary(self, summary: TestSummary):
        print("\n" + "=" * 60)
        print(f"测试结果 - {summary.algorithm_name}")
        print("=" * 60)
        print(f"总样本: {summary.total_segments}")
        print(f"Top-1: {summary.top1_correct}/{summary.total_segments} "
              f"= {summary.top1_accuracy * 100:.2f}%")
        print(f"Top-3: {summary.top3_correct}/{summary.total_segments} "
              f"= {summary.top3_accuracy * 100:.2f}%")
        print(f"不确定: {summary.uncertain_count}")

        # 按音频时长分组
        print("\n--- 按音频时长统计 ---")
        for low, high, label in [(0, 5, "0-5s"), (5, 10, "5-10s"), (10, 15, "10-15s"), (15, 999, "15s+")]:
            bucket = [r for r in summary.results if low <= r.duration_sec < high]
            if bucket:
                acc = sum(1 for r in bucket if r.is_correct) / len(bucket) * 100
                print(f"  {label}: {acc:.1f}% ({len(bucket)} 个)")

        # Top-1 错误分析
        print("\n--- Top-1 错误样本 ---")
        errors = [r for r in summary.results if not r.is_correct]
        if not errors:
            print("  无错误！")
        for r in errors[:10]:
            gt_name = self.speaker_info.get(r.ground_truth_speaker_id, {}).get("speaker_name", r.ground_truth_speaker_id)
            gt_rank = self._get_rank(r.all_scores, r.ground_truth_speaker_id)
            print(f"  [{r.segment_id}] GT={gt_name}({r.ground_truth_speaker_id}) | "
                  f"识别={r.recognized_speaker_name} | 得分={r.recognized_score:.4f} | "
                  f"GT排名=#{gt_rank}")

        # 正确样本
        print("\n--- 正确样本 (前10个) ---")
        correct = [r for r in summary.results if r.is_correct][:10]
        for r in correct:
            print(f"  [{r.segment_id}] GT={r.recognized_speaker_name} | "
                  f"得分={r.recognized_score:.4f} | {r.duration_sec:.1f}s")

    def print_comparison(self, results: Dict[str, TestSummary]):
        print("\n" + "=" * 70)
        print("算法对比结果（按 Top-1 准确率排序）")
        print("=" * 70)
        print(f"{'算法':<25} {'Top-1':<12} {'Top-3':<12} {'不确定':<10} {'样本数':<8}")
        print("-" * 70)

        sorted_res = sorted(results.items(), key=lambda x: x[1].top1_accuracy, reverse=True)
        for name, s in sorted_res:
            print(f"{name:<25} {s.top1_accuracy * 100:>6.2f}%   "
                  f"{s.top3_accuracy * 100:>6.2f}%   "
                  f"{s.uncertain_count:>6}   {s.total_segments:>6}")

        print("=" * 70)
        best = sorted_res[0]
        worst = sorted_res[-1]
        print(f"🏆 最佳算法: {best[0]} (Top-1: {best[1].top1_accuracy * 100:.2f}%)")
        print(f"📉 最差算法: {worst[0]} (Top-1: {worst[1].top1_accuracy * 100:.2f}%)")
        print(f"📈 提升空间: {(best[1].top1_accuracy - worst[1].top1_accuracy) * 100:.2f}%")

    def save_summary(self, summary: TestSummary, filepath: Optional[str] = None):
        if filepath is None:
            filepath = OUTPUT_DIR / f"speaker_test_{summary.algorithm_name}.json"
        output = {
            "algorithm": summary.algorithm_name,
            "summary": {
                "total_segments": summary.total_segments,
                "top1_correct": summary.top1_correct,
                "top1_accuracy": summary.top1_accuracy,
                "top3_correct": summary.top3_correct,
                "top3_accuracy": summary.top3_accuracy,
                "uncertain_count": summary.uncertain_count,
            },
            "results": [
                {
                    "segment_id": r.segment_id,
                    "audio_file": r.audio_path,
                    "gt_id": r.ground_truth_speaker_id,
                    "gt_name": self.speaker_info.get(r.ground_truth_speaker_id, {}).get("speaker_name", ""),
                    "recognized_id": r.recognized_speaker_id,
                    "recognized_name": r.recognized_speaker_name,
                    "score": r.recognized_score,
                    "is_correct": r.is_correct,
                    "top3_includes_correct": r.top3_includes_correct,
                    "duration_sec": r.duration_sec,
                    "uncertain": r.uncertainty_flag,
                    "all_scores": r.all_scores,
                    "gt_rank": self._get_rank(r.all_scores, r.ground_truth_speaker_id),
                }
                for r in summary.results
            ],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n结果已保存: {filepath}")

    def save_comparison(self, results: Dict[str, TestSummary], filepath: Optional[str] = None):
        if filepath is None:
            filepath = OUTPUT_DIR / "speaker_algorithm_comparison.json"
        output = {
            "algorithms": {
                name: {
                    "total_segments": s.total_segments,
                    "top1_correct": s.top1_correct,
                    "top1_accuracy": s.top1_accuracy,
                    "top3_correct": s.top3_correct,
                    "top3_accuracy": s.top3_accuracy,
                    "uncertain_count": s.uncertain_count,
                }
                for name, s in results.items()
            },
            "ranking": [
                name for name, _ in sorted(results.items(), key=lambda x: x[1].top1_accuracy, reverse=True)
            ],
        }
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)
        print(f"\n对比结果已保存: {filepath}")

    @staticmethod
    def _get_rank(all_scores: Dict[str, float], gt_id: str) -> Optional[int]:
        if not all_scores or not gt_id:
            return None
        sorted_items = sorted(all_scores.items(), key=lambda x: x[1], reverse=True)
        for rank, (sid, _) in enumerate(sorted_items, 1):
            if sid == gt_id:
                return rank
        return None


# ─────────────────────────────────────────────────────────────
# 主函数
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="模式四：批量声纹识别算法测试（smart-meeting-ai）")
    all_algo_names = [a.value for a in RecognitionMode]

    parser.add_argument("-n", "--n-samples", type=int, default=26,
                        help="测试样本数量（默认全部 26 个）")
    parser.add_argument("-m", "--mode", type=str, default="compare",
                        choices=all_algo_names + ["compare"],
                        help="算法模式（默认 compare = 全量对比）")
    parser.add_argument("--compare", action="store_true",
                        help="运行多算法对比")
    parser.add_argument("-s", "--save", action="store_true",
                        help="保存结果到 JSON")
    parser.add_argument("--output", type=str, default=None,
                        help="结果保存路径")
    parser.add_argument("--seed", type=int, default=None,
                        help="随机种子")

    args = parser.parse_args()

    # 随机种子
    if args.seed is not None:
        random.seed(args.seed)
        print(f"随机种子: {args.seed}")
    else:
        seed = int(time.time() * 1000) % (2 ** 32)
        random.seed(seed)
        print(f"时间种子: {seed}")

    # 加载资源
    tester = SpeakerRecognitionTester()
    tester.load_resources()

    # 确定模式
    do_compare = args.compare or args.mode == "compare"

    if do_compare:
        results = tester.run_comparison(n_samples=args.n_samples)
        tester.print_comparison(results)
        if args.save:
            tester.save_comparison(results, args.output)
    else:
        mode = RecognitionMode(args.mode)
        summary = tester.run_single(n_samples=args.n_samples, mode=mode)
        tester.print_summary(summary)
        if args.save:
            tester.save_summary(summary, args.output)


if __name__ == "__main__":
    main()
