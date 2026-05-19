"""
模型管理器 - 统一管理 FunASR、CAM++ 和 Silero VAD 模型的加载
从 InsightEye 移植，适配 smart-meeting-ai 配置
"""

import os
import asyncio
from typing import Optional
from dataclasses import dataclass
import numpy as np

import torch


@dataclass
class ModelPaths:
    funasr_model: str
    campplus_model: str
    campplus_en_model: str
    device: str
    local_model_dir: str = ""


def _resolve_model_path(path: str) -> str:
    """将相对路径解析为绝对路径（相对于项目根目录 backend/../../）

    模型文件在 ./models/ 下，相对路径如 "./models/funasr" 需要从项目根目录解析。
    """
    if not path:
        return ""
    # 如果是绝对路径直接返回
    if os.path.isabs(path):
        return path
    # 相对路径：从 backend/app/ 的父目录（即项目根）解析
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # backend/app/
    project_root = os.path.dirname(backend_dir)  # 项目根
    resolved = os.path.normpath(os.path.join(project_root, path))
    return resolved


def _get_default_model_paths() -> ModelPaths:
    """从环境变量和配置获取默认模型路径（自动解析相对路径）"""
    from app.config import settings

    funasr_dir = os.getenv("FUNASR_MODEL_DIR", settings.FUNASR_MODEL_DIR)
    camp_dir = os.getenv("CAMPPLUS_MODEL_DIR", settings.CAMPPLUS_MODEL_DIR)
    camp_en_dir = os.getenv("CAMPPLUS_EN_MODEL_DIR", settings.CAMPPLUS_EN_MODEL_DIR)
    local_dir = os.getenv("LOCAL_MODEL_DIR", settings.LOCAL_MODEL_DIR)
    device = os.getenv("LOCAL_DEVICE", settings.LOCAL_DEVICE or "cuda")

    return ModelPaths(
        funasr_model=_resolve_model_path(funasr_dir),
        campplus_model=_resolve_model_path(camp_dir),
        campplus_en_model=_resolve_model_path(camp_en_dir),
        device=device,
        local_model_dir=_resolve_model_path(local_dir),
    )


def _get_local_model_dir() -> str:
    """获取本地模型目录（自动解析相对路径）"""
    from app.config import settings
    path = settings.LOCAL_MODEL_DIR
    if path:
        resolved = _resolve_model_path(path)
        if os.path.isabs(resolved):
            return resolved
    # 回退：从项目根目录解析 ./models
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    project_root = os.path.dirname(backend_dir)
    return os.path.join(project_root, "models")


class ModelManager:
    """
    模型管理器 - 单例模式，全局共享模型实例

    使用方式:
        manager = ModelManager.get_instance()
        await manager.initialize()
        asr_model = manager.get_asr_model()
        camp_model = manager.get_camp_model()
    """

    _instance: Optional["ModelManager"] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self.funasr_model = None
        self.punc_model = None
        self.camp_model = None
        self.camp_en_model = None
        self.vad_model = None
        self.vad_model_samplerate = 16000
        self._initialized = False
        self._paths = _get_default_model_paths()

    @classmethod
    def get_instance(cls) -> "ModelManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def initialize(self) -> None:
        """初始化所有模型（在后台线程中加载）"""
        if self._initialized:
            return

        async with self._lock:
            if self._initialized:
                return

            print("[ModelManager] 开始加载模型...", flush=True)
            loop = asyncio.get_event_loop()

            try:
                await loop.run_in_executor(None, self._load_funasr)
                print("[ModelManager] FunASR (含内置 VAD) 加载完成", flush=True)
            except Exception as e:
                print(f"[ModelManager] FunASR 加载失败: {e}", flush=True)

            try:
                await loop.run_in_executor(None, self._load_vad)
                print("[ModelManager] Silero VAD 模型加载完成", flush=True)
            except Exception as e:
                print(f"[ModelManager] Silero VAD 加载失败: {e}", flush=True)

            try:
                await loop.run_in_executor(None, self._load_campplus)
                print("[ModelManager] CAM++ 中文声纹模型加载完成", flush=True)
            except Exception as e:
                print(f"[ModelManager] CAM++ 中文声纹模型加载失败: {e}", flush=True)

            try:
                await loop.run_in_executor(None, self._load_campplus_en)
                print("[ModelManager] CAM++ 英文声纹模型加载完成", flush=True)
            except Exception as e:
                print(f"[ModelManager] CAM++ 英文声纹模型加载失败: {e}", flush=True)

            self._initialized = True
            vad_status = "[PASS] 已加载" if self.vad_model is not None else "[FAIL] 未加载（无 VAD 模型）"
            funasr_status = "[PASS] 已加载" if self.funasr_model is not None else "[FAIL] 未加载"
            camp_status = "[PASS] 已加载" if self.camp_model is not None else "[FAIL] 未加载"
            camp_en_status = "[PASS] 已加载" if self.camp_en_model is not None else "[SKIP] 英文模型未配置"
            print("[ModelManager] ============================================", flush=True)
            print(f"[ModelManager] 模型加载结果汇总：", flush=True)
            print(f"[ModelManager]   VAD (Silero):        {vad_status}", flush=True)
            print(f"[ModelManager]   ASR (FunASR):        {funasr_status}", flush=True)
            print(f"[ModelManager]   声纹 (CAM++ zh):     {camp_status}", flush=True)
            print(f"[ModelManager]   声纹 (CAM++ en):     {camp_en_status}", flush=True)
            print("[ModelManager] ============================================", flush=True)
            if self.vad_model is None:
                print("[ModelManager] [!] 警告: VAD 模型未加载，实时转录将使用能量检测模式", flush=True)
            print("[ModelManager] 模型初始化流程结束", flush=True)

    def _load_funasr(self) -> None:
        """加载 FunASR 模型"""
        try:
            model_path = self._paths.funasr_model
            print(f"[ModelManager] 加载 FunASR 模型 from: {model_path}")

            if not os.path.exists(model_path):
                print(f"[ModelManager] FunASR 模型目录不存在: {model_path}，跳过")
                return

            from funasr import AutoModel

            # 方式1: 直接使用本地模型路径
            try:
                self.funasr_model = AutoModel(
                    model=model_path,
                    punc_model="ct-punc",
                    punc_model_revision="v2.0.4",
                    device=self._paths.device,
                    disable_update=True,
                    ncpu=4,
                )
            except Exception as e1:
                print(f"[ModelManager] 方式1失败，尝试方式2: {e1}")
                # 方式2: 使用 model_id 格式但指定本地目录
                model_id = "iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch"
                self.funasr_model = AutoModel(
                    model=model_id,
                    model_revision="v2.0.4",
                    punc_model="ct-punc",
                    punc_model_revision="v2.0.4",
                    cache_dir=self._paths.local_model_dir,
                    device=self._paths.device,
                    disable_update=True,
                    ncpu=4,
                )
            print(f"[ModelManager] FunASR 模型加载成功，使用设备: {self._paths.device}")
            self._load_punc_model()

        except Exception as e:
            print(f"[ModelManager] FunASR 模型加载失败: {e}")

    def _load_punc_model(self) -> None:
        """加载标点恢复模型（ct-punc）"""
        try:
            from funasr import AutoModel
            self.punc_model = AutoModel(
                model="ct-punc",
                model_revision="v2.0.4",
                device=self._paths.device,
                disable_update=True,
                ncpu=2,
            )
            print("[ModelManager] 标点恢复模型(ct-punc)加载成功")
        except Exception as e:
            print(f"[ModelManager] 标点恢复模型加载失败: {e}")
            self.punc_model = None

    def _load_campplus(self) -> None:
        """加载 CAM++ 中文声纹模型"""
        try:
            model_dir = self._paths.campplus_model
            print(f"[ModelManager] 加载 CAM++ 中文声纹模型 from: {model_dir}")

            if not os.path.exists(model_dir):
                print(f"[ModelManager] CAM++ 中文模型目录不存在: {model_dir}，跳过")
                return

            import yaml
            from funasr.models.campplus.model import CAMPPlus

            config_path = os.path.join(model_dir, "config.yaml")
            if not os.path.exists(config_path):
                print(f"[ModelManager] CAM++ config.yaml 不存在，跳过")
                return

            with open(config_path, "r", encoding="utf-8") as f:
                model_conf = yaml.safe_load(f)

            camp_config = model_conf.get("model_conf", {})
            self.camp_model = CAMPPlus(
                feat_dim=camp_config.get("feat_dim", 80),
                embedding_size=camp_config.get("embedding_size", 192),
                growth_rate=camp_config.get("growth_rate", 32),
                bn_size=camp_config.get("bn_size", 4),
                init_channels=camp_config.get("init_channels", 128),
                config_str=camp_config.get("config_str", "batchnorm-relu"),
                memory_efficient=camp_config.get("memory_efficient", True),
                output_level=camp_config.get("output_level", "segment"),
            )

            model_file = os.path.join(model_dir, model_conf.get("model_file") or "campplus_cn_common.bin")
            print(f"[ModelManager] CAM++ 中文模型权重文件: {model_file}")

            try:
                state_dict = torch.load(model_file, map_location=self._paths.device)
            except (RuntimeError, AssertionError):
                print(f"[ModelManager] CUDA 加载失败，尝试 CPU 模式...")
                state_dict = torch.load(model_file, map_location=torch.device("cpu"))

            self.camp_model.load_state_dict(state_dict, strict=False)
            try:
                self.camp_model.to(self._paths.device)
            except AssertionError:
                print(f"[ModelManager] CUDA 不可用，使用 CPU...")
                self.camp_model.to(torch.device("cpu"))
            self.camp_model.eval()
            print(f"[ModelManager] CAM++ 中文声纹模型加载成功")

        except Exception as e:
            print(f"[ModelManager] CAM++ 中文声纹模型加载失败: {e}")

    def _load_campplus_en(self) -> None:
        """加载 CAM++ 英文声纹模型（VoxCeleb）"""
        try:
            model_dir = self._paths.campplus_en_model
            print(f"[ModelManager] 加载 CAM++ 英文声纹模型 from: {model_dir}")

            if not os.path.exists(model_dir):
                print(f"[ModelManager] CAM++ 英文模型目录不存在: {model_dir}，跳过加载")
                return

            import json
            from funasr.models.campplus.model import CAMPPlus

            config_path = os.path.join(model_dir, "configuration.json")
            with open(config_path, "r", encoding="utf-8") as f:
                model_conf = json.load(f)

            camp_config = model_conf.get("model", {}).get("model_config", {})
            self.camp_en_model = CAMPPlus(
                feat_dim=camp_config.get("fbank_dim", 80),
                embedding_size=camp_config.get("emb_size", 512),
                growth_rate=32,
                bn_size=4,
                init_channels=128,
                config_str="batchnorm-relu",
                memory_efficient=True,
                output_level="segment",
            )

            model_file = os.path.join(model_dir, model_conf.get("model", {}).get("pretrained_model", "campplus_voxceleb.bin"))
            print(f"[ModelManager] CAM++ 英文模型权重文件: {model_file}")

            try:
                state_dict = torch.load(model_file, map_location=self._paths.device)
            except (RuntimeError, AssertionError):
                print(f"[ModelManager] CUDA 加载失败，尝试 CPU 模式...")
                state_dict = torch.load(model_file, map_location=torch.device("cpu"))

            self.camp_en_model.load_state_dict(state_dict, strict=False)
            try:
                self.camp_en_model.to(self._paths.device)
            except AssertionError:
                print(f"[ModelManager] CUDA 不可用，使用 CPU...")
                self.camp_en_model.to(torch.device("cpu"))
            self.camp_en_model.eval()

            print(f"[ModelManager] CAM++ 英文声纹模型加载成功")

        except Exception as e:
            print(f"[ModelManager] CAM++ 英文声纹模型加载失败: {e}")

    def _load_vad(self) -> None:
        """加载 Silero VAD 模型（优先项目 ./models，其次 D:/InsightEye/models，最后联网下载）"""
        try:
            torch.set_num_threads(1)

            # 优先级1: 项目根目录的 ./models（通过 LOCAL_MODEL_DIR 配置）
            local_master_dir = os.path.join(
                _get_local_model_dir(),
                "silero-vad", "snakers4_silero-vad_master"
            )
            local_v1 = os.path.join(local_master_dir, "files", "silero-vad", "silero_vad.jit")
            local_v2 = os.path.join(local_master_dir, "src", "silero_vad", "data", "silero_vad.jit")

            # 优先级2: D:/InsightEye/models（兼容旧路径）
            insighteye_vad = os.path.join(
                os.getenv("INSIGHTEYE_MODELS", "D:/InsightEye/models"),
                "silero-vad", "snakers4_silero-vad_master"
            )
            insighteye_jit = os.path.join(
                insighteye_vad, "src", "silero_vad", "data", "silero_vad.jit"
            )

            model, utils = None, None

            # 尝试加载顺序：项目 models > InsightEye models > 联网下载
            if os.path.exists(local_v1):
                print(f"[ModelManager] 发现项目本地 Silero VAD: {local_v1}", flush=True)
                os.environ["TORCH_HUB_DIR"] = os.path.join(_get_local_model_dir(), "silero-vad")
                # 对齐 InsightEye：用与 FunASR/CAM++ 相同的设备
                model, utils = torch.hub.load(repo_or_dir=local_master_dir, model='silero_vad', trust_repo=True, map_location=self._paths.device)
            elif os.path.exists(local_v2):
                print(f"[ModelManager] 发现项目本地 Silero VAD: {local_v2}", flush=True)
                model, utils = self._load_silero_from_local(os.path.join(local_master_dir, "src", "silero_vad"))
            elif os.path.exists(insighteye_jit):
                print(f"[ModelManager] 发现 InsightEye Silero VAD: {insighteye_jit}", flush=True)
                model, utils = self._load_silero_from_local(os.path.join(insighteye_vad, "src", "silero_vad"))
            else:
                print(f"[ModelManager] 未找到本地 Silero VAD，尝试联网下载...", flush=True)
                torch_hub_dir = os.path.join(_get_local_model_dir(), "silero-vad")
                os.makedirs(torch_hub_dir, exist_ok=True)
                os.environ["TORCH_HUB_DIR"] = torch_hub_dir
                model, utils = torch.hub.load(repo_or_dir='snakers4/silero-vad', model='silero_vad', trust_repo=True, map_location=self._paths.device)

            if model is None:
                raise RuntimeError("VAD 模型加载返回了 None")

            (get_speech_timestamps, _, read_audio, _, _) = utils
            self.vad_model = model
            self.vad_get_speech_timestamps = get_speech_timestamps
            self.vad_read_audio = read_audio
            print("[ModelManager] Silero VAD 模型加载成功", flush=True)

        except Exception as e:
            print(f"[ModelManager] Silero VAD 模型加载失败: {e}", flush=True)

    def _load_silero_from_local(self, src_dir: str):
        """直接从本地 .jit 文件加载 Silero VAD（设备与 CAM++ 一致）"""
        jit_file = os.path.join(src_dir, "data", "silero_vad.jit")
        if not os.path.exists(jit_file):
            raise FileNotFoundError(f"找不到 Silero VAD .jit 文件: {jit_file}")

        print(f"[ModelManager] 直接加载本地 .jit 模型: {jit_file}", flush=True)

        # 对齐 InsightEye：与 FunASR/CAM++ 用同一设备
        target_device = self._paths.device
        try:
            model = torch.jit.load(jit_file, map_location=target_device)
        except (RuntimeError, AssertionError):
            print(f"[ModelManager] {target_device} .jit 加载失败，尝试 CPU 模式...")
            model = torch.jit.load(jit_file, map_location=torch.device("cpu"))

        model.eval()

        def read_audio(path):
            import torchaudio
            waveform, sr = torchaudio.load(path)
            if sr != 16000:
                waveform = torchaudio.functional.resample(waveform, sr, 16000)
            return waveform.squeeze(0).numpy()

        utils = (lambda *a, **kw: [], lambda: None, read_audio, lambda: None, lambda: None)
        return model, utils

    def get_asr_model(self):
        return self.funasr_model

    def get_punc_model(self):
        return self.punc_model

    def get_camp_model(self):
        return self.camp_model

    def get_camp_en_model(self):
        return self.camp_en_model

    def get_vad_model(self):
        return self.vad_model

    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def device(self) -> str:
        return self._paths.device


class SpeakerEmbeddingExtractor:
    """说话人声纹特征提取器，使用 CAM++ 模型"""

    def __init__(self, camp_model, device="cuda"):
        self.camp_model = camp_model
        self.device = device
        self.sample_rate = 16000

    def extract(self, audio_data: np.ndarray) -> np.ndarray:
        """
        从音频数据中提取声纹特征向量（192维）
        Args:
            audio_data: numpy 数组，16kHz 采样率，float32 格式，范围 [-1, 1]
        Returns:
            192 维声纹向量
        """
        if self.camp_model is None:
            raise RuntimeError("CAM++ 模型未加载")

        try:
            import torch
            from funasr.models.campplus.utils import extract_feature
            from funasr.utils.load_utils import load_audio_text_image_video

            audio_tensor = torch.from_numpy(audio_data).float()
            audio_list = [audio_tensor]

            features_padded, feature_lengths, feature_times = extract_feature(audio_list)
            try:
                features_padded = features_padded.to(device=self.device)
            except AssertionError:
                self.device = "cpu"
                features_padded = features_padded.to(device="cpu")

            embedding = self.camp_model.forward(features_padded)

            if len(embedding.shape) > 2:
                embedding = embedding.squeeze(0)

            embedding_np = embedding.cpu().detach().numpy()

            if embedding_np.ndim > 1:
                embedding_np = embedding_np[0] if embedding_np.shape[0] == 1 else embedding_np.mean(axis=0)

            if np.any(np.isnan(embedding_np)):
                n_nan = np.sum(np.isnan(embedding_np))
                pct = n_nan * 100.0 / embedding_np.size
                print(f"[SpeakerEmbedding] 警告: embedding 包含 {n_nan}/{embedding_np.size} 个 NaN ({pct:.1f}%)")
                if pct < 10.0:
                    mean_val = np.nanmean(embedding_np)
                    embedding_np = np.nan_to_num(embedding_np, nan=mean_val)
                else:
                    raise ValueError(f"Embedding 失效率过高 ({pct:.1f}%)")

            return embedding_np

        except Exception as e:
            print(f"[SpeakerEmbedding] 声纹提取失败: {e}")
            raise

    def extract_from_bytes(self, audio_bytes: bytes) -> np.ndarray:
        """从字节数据中提取声纹特征"""
        audio_int16 = np.frombuffer(audio_bytes, dtype=np.int16)
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        return self.extract(audio_float32)

    def compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """计算两个声纹向量的余弦相似度"""
        emb1_norm = emb1 / (np.linalg.norm(emb1) + 1e-8)
        emb2_norm = emb2 / (np.linalg.norm(emb2) + 1e-8)
        similarity = float(np.dot(emb1_norm, emb2_norm))
        return (similarity + 1.0) / 2.0

    def _detect_speech_ranges(self, audio_data: np.ndarray) -> list[tuple[int, int]]:
        """用能量阈值检测音频中的语音区间"""
        win_size = int(self.sample_rate * 0.025)
        win_step = int(win_size * 0.5)
        n_samples = len(audio_data)
        energy_threshold = 0.01
        speech_ranges = []
        in_speech = False
        speech_start = 0
        i = 0
        while i * win_step < n_samples:
            start = i * win_step
            end = min(start + win_size, n_samples)
            frame = audio_data[start:end]
            energy = float(np.sqrt(np.mean(frame ** 2)))
            if energy > energy_threshold:
                if not in_speech:
                    speech_start = start
                    in_speech = True
            else:
                if in_speech:
                    speech_ranges.append((speech_start, end))
                    in_speech = False
            i += 1
        if in_speech:
            speech_ranges.append((speech_start, n_samples))
        if not speech_ranges:
            return []
        merged = [speech_ranges[0]]
        for start, end in speech_ranges[1:]:
            if start - merged[-1][1] < int(self.sample_rate * 0.1):
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        return merged

    def extract_multi_window(
        self, audio_data: np.ndarray, n_windows: int = 3, window_step_ratio: float = 0.25,
    ) -> list[tuple[np.ndarray, float, bool]]:
        """从音频中提取多个滑动窗口的声纹向量"""
        n_samples = len(audio_data)
        min_samples = int(self.sample_rate * 0.3)
        window_len = n_samples
        if n_samples < min_samples * n_windows:
            try:
                emb = self.extract(audio_data)
                return [(emb, 0.0, True)]
            except Exception:
                return []
        speech_ranges = self._detect_speech_ranges(audio_data)
        if speech_ranges and len(speech_ranges) >= 2:
            last_start, last_end = speech_ranges[-1]
            speech_len = last_end - last_start
            window_len = max(min(speech_len, n_samples // 2), int(self.sample_rate * 0.5))
            win_start = max(0, last_end - window_len)
            step = int(window_len * window_step_ratio)
        else:
            window_len = n_samples // n_windows
            step = int(window_len * window_step_ratio)
        results = []
        for i in range(n_windows):
            start = i * step
            end = min(start + window_len, n_samples)
            if end - start < min_samples:
                break
            window_audio = audio_data[start:end]
            energy = float(np.sqrt(np.mean(window_audio ** 2)))
            if energy < 1e-4:
                continue
            try:
                emb = self.extract(window_audio)
                if float(np.linalg.norm(emb)) < 1e-6:
                    continue
                results.append((emb, start / self.sample_rate, True))
            except Exception:
                results.append((np.array([]), start / self.sample_rate, False))
        return results

    def extract_fused(
        self, audio_data: np.ndarray, n_windows: int = 3,
        window_step_ratio: float = 0.25, fusion_method: str = "mean",
    ) -> tuple[np.ndarray, dict]:
        """提取多窗口声纹并融合为单一向量"""
        windows = self.extract_multi_window(audio_data, n_windows, window_step_ratio)
        valid = [(emb, ts) for emb, ts, ok in windows if ok]
        if not valid:
            raise ValueError("所有窗口均提取失败")
        embeddings = [emb for emb, _ in valid]
        timestamps = [ts for _, ts in valid]
        if fusion_method == "median":
            stacked = np.array(embeddings)
            fused = np.median(stacked, axis=0)
        else:
            fused = np.mean(embeddings, axis=0)
        fused = fused / (np.linalg.norm(fused) + 1e-8)
        window_scores = {}
        for idx, (emb, ts) in enumerate(valid):
            sim = float(np.dot(emb, fused))
            window_scores[f"window_{idx}_t{ts:.2f}s"] = round(sim, 4)
        mean_sim = float(np.mean(list(window_scores.values()))) if window_scores else 0.0
        std_sim = float(np.std(list(window_scores.values()))) if len(window_scores) > 1 else 0.0
        outlier_count = sum(
            1 for v in window_scores.values() if abs(v - mean_sim) > 2.5 * std_sim
        ) if std_sim > 0 else 0
        stats = {
            "n_windows": n_windows, "n_valid": len(valid),
            "window_scores": window_scores,
            "mean_sim": round(mean_sim, 4),
            "std_sim": round(std_sim, 4),
            "outlier_count": outlier_count,
            "timestamps": timestamps,
        }
        return fused, stats


def get_model_manager() -> ModelManager:
    """获取模型管理器实例的快捷函数"""
    return ModelManager.get_instance()
