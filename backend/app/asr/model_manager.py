"""
CAM++ 声纹模型管理 - 用于说话人注册和声纹比对。

核心：与 Pipeline3 (offline_pipeline.py) 中 Speaker3DEngine 使用完全相同的模型和提取逻辑，
保证注册与推理的 embedding 空间一致。

同时包含 ModelManager 类，统一管理实时转录所需的所有模型：
- Silero VAD
- FunASR (paraformer-large + ct-punc)
- CAM++ zh / en
"""

import os
import asyncio
from typing import Optional
from dataclasses import dataclass
import numpy as np

import torch


def _resolve_model_path(path: str) -> str:
    """解析模型路径：
    - 绝对路径：原样返回
    - 相对路径（如 ./models/funasr, ../data）：相对于 backend/ 目录解析
    - 模型名（如 paraformer-large）：原样返回，由 FunASR AutoModel 处理
    """
    if not path:
        return ""
    if os.path.isabs(path):
        return path

    # 始终以 backend/ 目录为基准，避免依赖 CWD
    _backend_dir = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )

    # ./models/xxx 或 ../xxx → 相对于 backend/ 解析
    if path.startswith("./") or path.startswith("../"):
        return os.path.normpath(os.path.join(_backend_dir, path))

    # 直接以 models/ funasr campplus silero 开头的相对路径 → 相对于 backend/
    if any(path.startswith(p) for p in ("models/", "funasr", "campplus", "silero")):
        return os.path.normpath(os.path.join(_backend_dir, path))

    # 纯模型名/ID（funasr iic/speech_paraformer 等）→ 原样返回
    return path


def _get_local_model_dir() -> str:
    from app.config import settings
    path = settings.LOCAL_MODEL_DIR
    if path:
        resolved = _resolve_model_path(path)
        if os.path.isabs(resolved):
            return resolved
    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    return os.path.join(os.path.dirname(backend_dir), "models")


@dataclass
class ModelPaths:
    funasr_model: str
    campplus_model: str
    campplus_en_model: str
    device: str
    local_model_dir: str = ""


def _get_default_model_paths() -> ModelPaths:
    """从环境变量和配置获取默认模型路径（自动解析相对路径）"""
    from app.config import settings
    from app.asr.config import (
        FUNASR_MODEL_DIR,
        CAMPPLUS_MODEL_DIR,
        CAMPPLUS_EN_MODEL_DIR,
        LOCAL_MODEL_DIR,
        LOCAL_DEVICE,
    )

    funasr_dir = os.getenv("FUNASR_MODEL_DIR", FUNASR_MODEL_DIR)
    camp_dir = os.getenv("CAMPPLUS_MODEL_DIR", CAMPPLUS_MODEL_DIR)
    camp_en_dir = os.getenv("CAMPPLUS_EN_MODEL_DIR", CAMPPLUS_EN_MODEL_DIR)
    local_dir = os.getenv("LOCAL_MODEL_DIR", LOCAL_MODEL_DIR)
    device = os.getenv("LOCAL_DEVICE", LOCAL_DEVICE or settings.LOCAL_DEVICE or "cuda")

    return ModelPaths(
        funasr_model=_resolve_model_path(funasr_dir),
        campplus_model=_resolve_model_path(camp_dir),
        campplus_en_model=_resolve_model_path(camp_en_dir),
        device=device,
        local_model_dir=_resolve_model_path(local_dir),
    )


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

        print("", flush=True)
        print("[ModelManager] =========================================================", flush=True)
        print("[ModelManager]              实时转录模型加载", flush=True)
        print("[ModelManager] =========================================================", flush=True)
        loop = asyncio.get_event_loop()

        # ── Silero VAD ──────────────────────────────────────────────
        print("[ModelManager] [1/5] Silero VAD (语音活动检测)...", flush=True)
        _vad_ok = False
        try:
            await loop.run_in_executor(None, self._load_vad)
            if self.vad_model is not None:
                print("[ModelManager]       [PASS] Silero VAD 加载成功", flush=True)
                _vad_ok = True
            else:
                print("[ModelManager]       [FAIL] Silero VAD 加载失败（模型为空）", flush=True)
        except Exception as e:
            print(f"[ModelManager]       [FAIL] Silero VAD 加载失败: {e}", flush=True)

        # ── FunASR ASR ─────────────────────────────────────────────
        print("[ModelManager] [2/5] FunASR ASR (语音识别)...", flush=True)
        _asr_ok = False
        try:
            await loop.run_in_executor(None, self._load_funasr)
            if self.funasr_model is not None:
                print("[ModelManager]       [PASS] FunASR ASR 加载成功", flush=True)
                _asr_ok = True
            else:
                print("[ModelManager]       [FAIL] FunASR ASR 加载失败（模型为空）", flush=True)
        except Exception as e:
            print(f"[ModelManager]       [FAIL] FunASR ASR 加载失败: {e}", flush=True)

        # ── 标点恢复模型 ───────────────────────────────────────────
        print("[ModelManager] [3/5] 标点恢复模型 (ct-punc)...", flush=True)
        try:
            await loop.run_in_executor(None, self._load_punc_model)
            if self.punc_model is not None:
                print("[ModelManager]       [PASS] 标点恢复模型加载成功", flush=True)
            else:
                print("[ModelManager]       [SKIP] 标点恢复模型加载失败（可选）", flush=True)
        except Exception as e:
            print(f"[ModelManager]       [SKIP] 标点恢复模型加载失败: {e}", flush=True)

        # ── CAM++ 中文 ─────────────────────────────────────────────
        print("[ModelManager] [4/5] CAM++ 声纹 (中文)...", flush=True)
        _camp_ok = False
        try:
            await loop.run_in_executor(None, self._load_campplus)
            if self.camp_model is not None:
                print("[ModelManager]       [PASS] CAM++ 中文声纹模型加载成功", flush=True)
                _camp_ok = True
            else:
                print("[ModelManager]       [FAIL] CAM++ 中文声纹模型加载失败（模型为空）", flush=True)
        except Exception as e:
            print(f"[ModelManager]       [FAIL] CAM++ 中文声纹模型加载失败: {e}", flush=True)

        # ── CAM++ 英文 ─────────────────────────────────────────────
        print("[ModelManager] [5/5] CAM++ 声纹 (英文, 可选)...", flush=True)
        try:
            await loop.run_in_executor(None, self._load_campplus_en)
            if self.camp_en_model is not None:
                print("[ModelManager]       [PASS] CAM++ 英文声纹模型加载成功（可选）", flush=True)
            else:
                print("[ModelManager]       [SKIP] CAM++ 英文声纹模型未配置或加载失败（可选）", flush=True)
        except Exception as e:
            print(f"[ModelManager]       [SKIP] CAM++ 英文声纹模型加载失败: {e}", flush=True)

        # ── 汇总 ───────────────────────────────────────────────────
        _total = 5
        _passed = sum(1 for _f in [_vad_ok, _asr_ok, _camp_ok] if _f)
        print("", flush=True)
        print("[ModelManager] =========================================================", flush=True)
        print(f"[ModelManager]  加载结果汇总（共 {_total} 个核心模型）", flush=True)
        print("[ModelManager] ---------------------------------------------------------", flush=True)
        _vad_s = "[PASS]" if _vad_ok else "[FAIL]"
        _asr_s = "[PASS]" if _asr_ok else "[FAIL]"
        _camp_s = "[PASS]" if _camp_ok else "[FAIL]"
        print(f"[ModelManager]   1. Silero VAD (语音活动检测)     {_vad_s}", flush=True)
        print(f"[ModelManager]   2. FunASR ASR (语音识别)         {_asr_s}", flush=True)
        print(f"[ModelManager]   3. 标点恢复模型 (ct-punc)       {'[PASS]' if self.punc_model else '[SKIP]'}  (可选)", flush=True)
        print(f"[ModelManager]   4. CAM++ 中文声纹               {_camp_s}", flush=True)
        print(f"[ModelManager]   5. CAM++ 英文声纹               {'[PASS]' if self.camp_en_model else '[SKIP]'}  (可选)", flush=True)
        print("[ModelManager] ---------------------------------------------------------", flush=True)
        print(f"[ModelManager]  通过: {_passed}/{_total}   设备: {self._paths.device}", flush=True)
        print("[ModelManager] =========================================================", flush=True)

        if not _vad_ok and not _asr_ok:
            print("[ModelManager]  严重: VAD 和 ASR 均未加载，实时转录功能不可用", flush=True)
        elif not _vad_ok:
            print("[ModelManager]  警告: VAD 未加载，将使用能量检测模式", flush=True)
        else:
            print("[ModelManager]  就绪: 实时转录模型已加载完成，可以开始会议", flush=True)

        self._initialized = True

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
        """加载 Silero VAD 模型（优先项目 ./models，其次联网下载）"""
        try:
            torch.set_num_threads(1)

            # 优先级1: 项目根目录的 ./models（通过 LOCAL_MODEL_DIR 配置）
            local_master_dir = os.path.join(
                _get_local_model_dir(),
                "silero-vad", "snakers4_silero-vad_master"
            )
            local_v1 = os.path.join(local_master_dir, "files", "silero-vad", "silero_vad.jit")
            local_v2 = os.path.join(local_master_dir, "src", "silero_vad", "data", "silero_vad.jit")

            model, utils = None, None

            if os.path.exists(local_v1):
                print(f"[ModelManager] 发现项目本地 Silero VAD: {local_v1}", flush=True)
                os.environ["TORCH_HUB_DIR"] = os.path.join(_get_local_model_dir(), "silero-vad")
                model, utils = torch.hub.load(repo_or_dir=local_master_dir, model='silero_vad', trust_repo=True, map_location=self._paths.device)
            elif os.path.exists(local_v2):
                print(f"[ModelManager] 发现项目本地 Silero VAD: {local_v2}", flush=True)
                model, utils = self._load_silero_from_local(os.path.join(local_master_dir, "src", "silero_vad"))
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
        """直接从本地 .jit 文件加载 Silero VAD"""
        jit_file = os.path.join(src_dir, "data", "silero_vad.jit")
        if not os.path.exists(jit_file):
            raise FileNotFoundError(f"找不到 Silero VAD .jit 文件: {jit_file}")

        print(f"[ModelManager] 直接加载本地 .jit 模型: {jit_file}", flush=True)

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

    def __init__(self, model, device: str = "cuda"):
        self.model = model
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
        if self.model is None:
            raise RuntimeError("CAM++ 模型未加载")

        try:
            import torch
            from funasr.models.campplus.utils import extract_feature

            if audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)

            audio_tensor = torch.from_numpy(audio_data).float()
            features, _, _ = extract_feature([audio_tensor])
            try:
                features = features.to(device=self.device)
            except AssertionError:
                self.device = "cpu"
                features = features.to(device="cpu")

            embedding = self.model(features)

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

    def extract_multi_window(
        self, audio_data: np.ndarray, n_windows: int = 3, window_step_ratio: float = 0.25,
        sample_rate: int = 16000,
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
        speech_ranges = self._detect_speech_ranges(audio_data, sample_rate)
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
        sample_rate: int = 16000,
    ) -> tuple[np.ndarray, dict]:
        windows = self.extract_multi_window(audio_data, n_windows, window_step_ratio, sample_rate)
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

    def _detect_speech_ranges(self, audio_data: np.ndarray, sample_rate: int = 16000) -> list[tuple[int, int]]:
        win_size = int(sample_rate * 0.025)
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
            if start - merged[-1][1] < int(sample_rate * 0.1):
                merged[-1] = (merged[-1][0], end)
            else:
                merged.append((start, end))
        return merged


def load_campplus_model(device: Optional[str] = None) -> tuple:
    """加载 CAM++ 模型，返回 (model, device)。"""
    import torch
    from funasr.models.campplus.model import CAMPPlus

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    model_dir = os.path.join(backend_dir, "models", "iic", "speech_campplus_sv_zh-cn_16k-common")
    ckpt_path = os.path.join(model_dir, "campplus_cn_common.bin")

    model = CAMPPlus(
        feat_dim=80,
        embedding_size=192,
        growth_rate=32,
        bn_size=4,
        init_channels=128,
        config_str="batchnorm-relu",
        memory_efficient=True,
        output_level="segment",
    )
    state_dict = torch.load(ckpt_path, map_location="cpu")
    if "state_dict" in state_dict:
        state_dict = state_dict["state_dict"]
    model.load_state_dict(state_dict, strict=False)
    model.to(device)
    model.eval()
    print(f"[SpeakerEmbedding] CAM++ 加载完成，设备: {device}")
    return model, device


def get_model_manager() -> ModelManager:
    """获取模型管理器实例的快捷函数"""
    return ModelManager.get_instance()
