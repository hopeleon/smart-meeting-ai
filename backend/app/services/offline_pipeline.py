"""
离线音频处理管线 — VibeVoice-ASR（端到端 ASR）+ CAM++（声纹对比）。

核心流程：
  音频（最长60分钟一次性输入）
    ↓
  VibeVoice-ASR 单次推理
    → 输出: [{Start, End, Speaker_ID, Content}, ...]
    Speaker_ID 是 VibeVoice 内部的 0/1/2... 序号，不代表真实说话人
    ↓
  对每个片段单独提取音频 → CAM++ embedding
    → 与声纹数据库逐一余弦打分
    → 映射到注册说话人ID
    → 每个句子片段独立识别，不共享分数
    ↓
  TurnSegment[]
    ↓
  摘要

优势：
  - 端到端，无需 VAD、无需流式状态机
  - 60分钟一次性处理，全局说话人追踪
  - CAM++ 是专为此任务训练的说话人验证模型
"""

import asyncio
import gc
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Callable, Any, Tuple

import numpy as np
import torch

from app.services.speaker_db_service import get_speaker_db, bytes_to_ndarray


# ─── ASR 后置过滤：无意义语气词 ─────────────────────────────────
_MEANINGLESS_PATTERNS: set = {
    '嗯', '啊', '呃', '哦', '噢', '呀', '哈', '嗯嗯', '啊啊',
    '哦哦', '噢噢', '嗯嗯嗯', '啊啊啊啊', '嗯啊', '啊嗯', '嗯嗯嗯嗯',
}


# ─── 数据结构 ───────────────────────────────────────────────────

@dataclass
class TurnSegment:
    speaker_id: Optional[str] = None
    speaker_name: Optional[str] = None
    text: str = ""
    words: List[Any] = field(default_factory=list)
    start_ms: int = 0
    end_ms: int = 0
    confidence: float = 0.0
    top3_ids: List[str] = field(default_factory=list)
    merged_from: int = 1


@dataclass
class ProcessingResult:
    meeting_id: str
    turns: List[TurnSegment]
    speakers: Dict[str, Dict]
    summary: Optional[Dict]
    processing_time_ms: float


# ─── 声纹引擎（CAM++） ─────────────────────────────────────────

class Speaker3DEngine:
    """
    加载 CAM++ 声纹模型，192维 embedding。
    与注册接口 speakers.py 使用相同的 embedding 提取逻辑，保证 embedding 空间一致。
    """

    def __init__(self):
        self.model = None
        self._device = "cpu"

    def load(self):
        if self.model is not None:
            return

        print("[CAM++] 加载 CAM++ 声纹模型...", flush=True)

        import os
        import torch
        from funasr.models.campplus.model import CAMPPlus

        self._device = "cuda" if torch.cuda.is_available() else "cpu"
        print(f"[CAM++] 使用设备: {self._device}", flush=True)

        backend_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        model_dir = os.path.join(backend_dir, "models", "iic", "speech_campplus_sv_zh-cn_16k-common")
        ckpt_path = os.path.join(model_dir, "campplus_cn_common.bin")

        self.model = CAMPPlus(
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
        self.model.load_state_dict(state_dict, strict=False)
        self.model.to(self._device)
        self.model.eval()

        print(f"[CAM++] CAM++ 加载完成: {model_dir}", flush=True)

    def extract(self, audio_data: np.ndarray) -> np.ndarray:
        """从音频提取 192维 embedding"""
        if self.model is None:
            self.load()

        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)

        try:
            import torch
            from funasr.models.campplus.utils import extract_feature

            audio_tensor = torch.from_numpy(audio_data).float()
            features, _, _ = extract_feature([audio_tensor])
            features = features.to(self._device)

            with torch.no_grad():
                embedding = self.model(features)

            if embedding.dim() > 2:
                embedding = embedding.squeeze(0)
            emb = embedding.cpu().numpy()

            if emb.ndim > 1:
                emb = emb[0] if emb.shape[0] == 1 else emb.mean(axis=0)

            if np.any(np.isnan(emb)):
                emb = np.nan_to_num(emb, nan=np.nanmean(emb))

            norm = np.linalg.norm(emb)
            if norm > 1e-8:
                emb = emb / norm
            return emb.astype(np.float32)

        except Exception as e:
            print(f"[CAM++] 声纹提取失败: {e}", flush=True)
            return np.zeros(192, dtype=np.float32)

    def compute_similarity(self, emb1: np.ndarray, emb2: np.ndarray) -> float:
        """余弦相似度"""
        n1 = emb1 / (np.linalg.norm(emb1) + 1e-8)
        n2 = emb2 / (np.linalg.norm(emb2) + 1e-8)
        return float(np.dot(n1, n2))

    def unload(self):
        """卸载 CAM++ 模型，释放显存"""
        import gc, torch
        if self.model is not None:
            del self.model
            self.model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        print("[CAM++] CAM++ 模型已卸载", flush=True)


# ─── 核心类 ─────────────────────────────────────────────────────

class OfflinePipeline:
    """
    离线音频处理：VibeVoice-ASR 端到端 + CAM++ 声纹对比。
    """

    # 声纹对比阈值
    SIM_THRESHOLD = 0.20  # 余弦相似度 >= 0.20 视为同一人

    def __init__(
        self,
        meeting_id: str,
        sample_rate: int = 16000,
        language: str = "zh",
        on_progress: Optional[Callable[[str, float], None]] = None,
    ):
        self.meeting_id = meeting_id
        self.sample_rate = sample_rate
        self.language = language
        self._report_progress = on_progress or (lambda s, p: None)
        # 设备检测：优先用 CUDA
        _use_cuda = torch.cuda.is_available()
        self._vibevoice_device = "cuda" if _use_cuda else "cpu"
        print(f"[Pipeline3] 设备检测: {'CUDA' if _use_cuda else 'CPU'}", flush=True)

        self._speaker3d = Speaker3DEngine()

        self._vibevoice_processor = None
        self._vibevoice_model = None

        self._speaker_names: Dict[str, str] = {}
        self._registered_embeddings: Dict[str, np.ndarray] = {}

    # ─── 公共 API ───────────────────────────────────────────────

    async def process(self, audio_data: np.ndarray) -> ProcessingResult:
        total_start = time.time()
        stage_start = total_start

        print(f"[Pipeline3] ═══════════════════════════════════════════════", flush=True)
        print(f"[Pipeline3] 启动 (VibeVoice-ASR + CAM++)", flush=True)
        print(f"[Pipeline3] 音频时长: {len(audio_data) / self.sample_rate:.1f}s", flush=True)
        print(f"[Pipeline3] ═══════════════════════════════════════════════", flush=True)

        self._report_progress("初始化", 0.0)
        init_start = time.time()
        await self._initialize()
        init_time = time.time() - init_start
        print(f"[Pipeline3-计时] 模型加载耗时: {init_time:.1f}s", flush=True)

        # Step 1: VibeVoice-ASR 端到端推理
        self._report_progress("VibeVoice推理", 0.0)
        asr_start = time.time()
        vibevoice_turns = self._vibevoice_transcribe(audio_data)
        asr_time = time.time() - asr_start
        print(f"[Pipeline3] VibeVoice 推理完成: {len(vibevoice_turns)} 个片段, 耗时: {asr_time:.1f}s", flush=True)

        # ── 卸载 VibeVoice，保留 CAM++ 供声纹对比使用 ───────────────
        self._unload_vibevoice()

        # Step 2: 声纹对比（CAM++）
        self._report_progress("声纹对比", 0.0)
        spk_start = time.time()
        mapped = self._map_speakers_by_voiceprint(vibevoice_turns, audio_data)
        spk_time = time.time() - spk_start
        print(f"[Pipeline3] 声纹对比完成: {len(mapped)} 个片段, 耗时: {spk_time:.1f}s", flush=True)

        # ── 卸载 CAM++，释放显存给 Ollama ─────────────────────────
        self._speaker3d.unload()
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        free = torch.cuda.mem_get_info()[0] / 1024**3 if torch.cuda.is_available() else 0
        print(f"[Pipeline3] CAM++ 已卸载, GPU free: {free:.1f} GB", flush=True)

        # Step 3: 组装 turns + 摘要
        turns = self._build_turns(mapped)
        speakers = self._collect_speakers(turns)

        self._report_progress("生成摘要", 0.0)
        sum_start = time.time()
        summary = await self._generate_summary(turns)
        sum_time = time.time() - sum_start
        self._report_progress("生成摘要", 1.0)

        total_time = time.time() - total_start
        total_time_ms = total_time * 1000

        print(f"[Pipeline3] ═══════════════════════════════════════════════", flush=True)
        print(f"[Pipeline3] 完成！各阶段耗时：", flush=True)
        print(f"[Pipeline3]   模型加载: {init_time:.1f}s", flush=True)
        print(f"[Pipeline3]   VibeVoice转写: {asr_time:.1f}s", flush=True)
        print(f"[Pipeline3]   声纹识别: {spk_time:.1f}s", flush=True)
        print(f"[Pipeline3]   摘要生成: {sum_time:.1f}s", flush=True)
        print(f"[Pipeline3]   总耗时: {total_time:.1f}s ({total_time_ms:.0f}ms)", flush=True)
        print(f"[Pipeline3]   转写片段: {len(turns)}, 说话人数: {len(speakers)}", flush=True)
        print(f"[Pipeline3] ═══════════════════════════════════════════════", flush=True)

        self._report_progress("完成", 1.0)

        return ProcessingResult(
            meeting_id=self.meeting_id,
            turns=turns,
            speakers=speakers,
            summary=summary,
            processing_time_ms=total_time_ms,
        )

    # ─── 初始化 ────────────────────────────────────────────────

    async def _initialize(self):
        """加载模型 + 声纹库"""
        print("[Pipeline3] ═══ 模型加载阶段 ═══", flush=True)
        print("[Pipeline3] Step 1/4: 加载 VibeVoice Processor...", flush=True)
        self._report_progress("加载模型", 0.0)

        # 加载 VibeVoice-ASR
        try:
            from transformers import AutoProcessor, VibeVoiceAsrForConditionalGeneration
        except ImportError as e:
            raise RuntimeError(f"transformers 导入失败: {e}")

        model_id = "microsoft/VibeVoice-ASR-HF"
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "models", "vibevoice"
        )
        # 用原始全精度模型（16GB），避免 vibevoice-int8 的 INT8 量化 cuBLAS 兼容问题
        snapshot_dir = os.path.join(
            cache_dir, "models--microsoft--VibeVoice-ASR-HF", "snapshots",
            "f22241c2062b3b25272bf117397e03d73381037a"
        )

        print(f"[Pipeline3]   路径: {snapshot_dir}", flush=True)
        self._vibevoice_processor = AutoProcessor.from_pretrained(
            snapshot_dir, local_files_only=True
        )
        print("[Pipeline3]   ✓ Processor 加载成功", flush=True)
        self._report_progress("加载模型", 0.2)

        load_kwargs: Dict[str, Any] = {
            "trust_remote_code": True,
            "local_files_only": True,
        }
        if self._vibevoice_device == "cuda":
            load_kwargs["device_map"] = "cuda"
            load_kwargs["torch_dtype"] = torch.float16
        else:
            load_kwargs["device_map"] = "cpu"
            load_kwargs["torch_dtype"] = torch.float32

        print(f"[Pipeline3] Step 2/4: 加载 VibeVoice 模型 (device={self._vibevoice_device}, dtype={load_kwargs['torch_dtype']})...", flush=True)
        try:
            self._vibevoice_model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
                snapshot_dir, **load_kwargs
            )
        except (RuntimeError, OSError) as e:
            if "out of memory" in str(e).lower() or "cuda" in str(e).lower():
                print(f"[Pipeline3] ⚠ CUDA OOM 或驱动问题，切换到 CPU 模式...", flush=True)
                torch.cuda.empty_cache()
                torch.cuda.synchronize()
                load_kwargs["device_map"] = "cpu"
                load_kwargs["torch_dtype"] = torch.float32
                self._vibevoice_device = "cpu"
                self._vibevoice_model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
                    snapshot_dir, **load_kwargs
                )
            else:
                raise
        print(f"[Pipeline3]   ✓ VibeVoice-ASR 模型加载完成", flush=True)
        print(f"[Pipeline3]   模型参数量: {sum(p.numel() for p in self._vibevoice_model.parameters()) / 1e9:.2f}B", flush=True)
        self._report_progress("加载模型", 0.4)

        print("[Pipeline3] Step 3/4: 加载 CAM++ 声纹模型...", flush=True)
        self._speaker3d.load()
        print("[Pipeline3]   ✓ CAM++ 加载完成", flush=True)
        self._report_progress("加载模型", 0.6)

        # 加载声纹数据库
        print("[Pipeline3] Step 4/4: 加载声纹数据库...", flush=True)
        speaker_db = get_speaker_db()
        speakers = speaker_db.get_all_speakers()
        self._registered_embeddings = {}
        for sp in speakers:
            sid = sp.get("speaker_id")
            name = sp.get("name")
            embedding_bytes = sp.get("embedding")
            if sid and embedding_bytes is not None:
                emb = bytes_to_ndarray(embedding_bytes)
                if emb is not None:
                    self._registered_embeddings[sid] = emb
                    self._speaker_names[sid] = name or sid

        print(f"[Pipeline3]   ✓ 声纹数据库: {len(self._registered_embeddings)} 位注册说话人", flush=True)
        if self._registered_embeddings:
            names = list(self._registered_embeddings.keys())[:5]
            print(f"[Pipeline3]   示例: {names}{'...' if len(self._registered_embeddings) > 5 else ''}", flush=True)
        self._report_progress("加载模型", 1.0)
        print("[Pipeline3] ═══ 模型加载完成 ═══", flush=True)

    # ─── Step 1: VibeVoice-ASR 推理 ─────────────────────────────

    # VibeVoice 模型期望 24kHz 音频
    _VIBEVOICE_SAMPLE_RATE = 24000

    def _vibevoice_transcribe(
        self, audio_data: np.ndarray
    ) -> List[Dict]:
        """
        调用 VibeVoice-ASR 端到端推理。
        VibeVoice 支持最长 60 分钟音频单次处理（内部自动分 chunk + 状态缓存）。
        返回: [{"Speaker": int, "Start": float, "End": float, "Content": str}, ...]
        """
        # 转为 float32 [-1, 1]
        if audio_data.dtype != np.float32:
            audio_data = audio_data.astype(np.float32)
        if np.abs(audio_data).max() > 1.0:
            audio_data = audio_data / 32768.0

        # 重采样到 24kHz（VibeVoice 模型期望的采样率）
        if self.sample_rate != self._VIBEVOICE_SAMPLE_RATE:
            audio_data = self._resample(audio_data, self.sample_rate, self._VIBEVOICE_SAMPLE_RATE)
            print(f"[Pipeline3] 重采样: {self.sample_rate}Hz → {self._VIBEVOICE_SAMPLE_RATE}Hz", flush=True)

        inputs = self._vibevoice_processor.apply_transcription_request(
            audio=audio_data,
        )
        print(f"[Pipeline3-转写] 输入 shape: {inputs.get('input_values', 'N/A').shape if hasattr(inputs.get('input_values'), 'shape') else 'N/A'}", flush=True)

        if hasattr(self._vibevoice_model, "device"):
            device = next(self._vibevoice_model.parameters()).device
            model_dtype = next(self._vibevoice_model.parameters()).dtype
            inputs = {
                k: (v.to(device=device, dtype=model_dtype) if k == "input_values"
                    else v.to(device=device))
                if hasattr(v, "to") else v
                for k, v in inputs.items()
            }

        gen_kwargs = {}
        audio_min = len(audio_data) / self._VIBEVOICE_SAMPLE_RATE / 60
        gen_kwargs["max_new_tokens"] = max(65536, int(audio_min * 3000))  # 提高到每分钟 3000 tokens，确保长音频不被截断
        print(f"[Pipeline3-转写] 音频时长: {len(audio_data) / self._VIBEVOICE_SAMPLE_RATE / 60:.1f} 分钟, max_new_tokens={gen_kwargs['max_new_tokens']}", flush=True)
        print(f"[Pipeline3-转写] 开始 VibeVoice ASR 推理...", flush=True)
        import time as time_module
        t0 = time_module.time()

        with torch.no_grad():
            output_ids = self._vibevoice_model.generate(**inputs, **gen_kwargs)

        elapsed = time_module.time() - t0
        print(f"[Pipeline3-转写] ✓ 推理完成，耗时 {elapsed:.1f}s", flush=True)

        generated_ids = output_ids[:, inputs["input_ids"].shape[1]:]
        print(f"[Pipeline3-转写] 生成 tokens 数: {generated_ids.shape[1]}", flush=True)

        raw_text = self._vibevoice_processor.batch_decode(generated_ids)[0]
        print(f"[Pipeline3-转写] 原始输出长度: {len(raw_text)} 字符", flush=True)
        if len(raw_text) < 10:
            return []

        print(f"[Pipeline3-转写] 开始解码...", flush=True)
        try:
            transcription = self._vibevoice_processor.decode(
                generated_ids, return_format="parsed"
            )[0]
        except Exception as e:
            print(f"[Pipeline3-转写] decode 失败，尝试手动解析: {e}", flush=True)
            transcription = self._parse_partial_json(raw_text)

        if transcription:
            print(f"[Pipeline3-转写] ✓ 转写完成: {len(transcription)} 个片段", flush=True)
        else:
            print(f"[Pipeline3-转写] ✗ 无转写结果", flush=True)

        return transcription

    def _unload_vibevoice(self):
        """卸载 VibeVoice 模型，释放显存"""
        import gc, torch
        if self._vibevoice_model is not None:
            del self._vibevoice_model
            self._vibevoice_model = None
        if self._vibevoice_processor is not None:
            del self._vibevoice_processor
            self._vibevoice_processor = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
        free = torch.cuda.mem_get_info()[0] / 1024**3 if torch.cuda.is_available() else 0
        print(f"[Pipeline3] VibeVoice 已卸载, GPU free: {free:.1f} GB", flush=True)

    def _resample(
        self, audio: np.ndarray, orig_sr: int, target_sr: int
    ) -> np.ndarray:
        """简单的线性插值重采样。"""
        if orig_sr == target_sr:
            return audio
        duration = len(audio) / orig_sr
        target_length = int(duration * target_sr)
        indices = np.linspace(0, len(audio) - 1, target_length)
        return np.interp(indices, np.arange(len(audio)), audio).astype(audio.dtype)

    def _parse_partial_json(self, raw_text: str) -> List[Dict]:
        """手动从原始输出中提取 JSON 片段（用于 decode 失败时降级）。"""
        import json, re

        # 去掉 chat template 前缀
        text = re.sub(r'^<\|im_start\|>.*?\n', '', raw_text, count=1)
        text = text.replace('<|im_end|>\n', '').replace('<|endoftext|>', '').strip()

        results: List[Dict] = []
        # 提取所有 {..."Content": "...", ...} 对象
        # 匹配 {"Start":..., "End":..., "Speaker":..., "Content": "..."} 结构
        pattern = r'\{[^{}]*"Start"\s*:\s*[\d.]+[^{}]*"End"\s*:\s*[\d.]+[^{}]*"Speaker"\s*:\s*\d+[^{}]*"Content"\s*:\s*"([^"]*)"[^{}]*\}'
        for m in re.finditer(pattern, text):
            try:
                # 找到这个对象的完整 JSON
                start, end = m.span()
                # 往前找到 {
                json_start = text.rfind('{', 0, start)
                if json_start == -1:
                    continue
                # 往后找到 }
                depth = 0
                json_end = json_start
                for i, ch in enumerate(text[json_start:], json_start):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            json_end = i + 1
                            break
                chunk = text[json_start:json_end]
                obj = json.loads(chunk)
                if isinstance(obj, dict) and "Content" in obj:
                    results.append(obj)
            except (json.JSONDecodeError, ValueError):
                continue

        if results:
            print(f"[Pipeline3] 手动解析出 {len(results)} 个片段", flush=True)
        return results

    # ─── Step 2: 声纹对比映射 ─────────────────────────────────

    def _map_speakers_by_voiceprint(
        self,
        vibevoice_turns: List[Dict],
        audio_data: np.ndarray,
    ) -> List[TurnSegment]:
        """
        对每个 VibeVoice Speaker 片段：
          1. 提取该时间段的音频
          2. 用 CAM++ 提取 embedding
          3. 与声纹数据库逐一余弦打分
          4. 取最高分者作为 speaker_id（若 < SIM_THRESHOLD 则标记 unknown）
        """
        print("[Pipeline3] ═══ 声纹识别阶段 ═══", flush=True)
        self._report_progress("声纹识别", 0.0)
        total = len(vibevoice_turns)
        results: List[TurnSegment] = []

        # 遍历每个片段，单独提取 embedding、单独比对
        print(f"[Pipeline3-声纹] 开始对每个片段单独提取 embedding（使用 CAM++）...", flush=True)
        valid_turns: List[Dict] = []   # 有有效 embedding 的片段
        skip_count = 0
        for i, item in enumerate(vibevoice_turns):
            if "Speaker" not in item:
                continue
            dur = item["End"] - item["Start"]
            if dur < 0.5:
                skip_count += 1
                continue
            start_s = int(item["Start"] * self.sample_rate)
            end_s = int(item["End"] * self.sample_rate)
            end_s = min(end_s, len(audio_data))
            chunk = audio_data[start_s:end_s]
            emb = self._speaker3d.extract(chunk)
            item_with_emb = {**item, "_emb": emb}
            valid_turns.append(item_with_emb)
            if (i + 1) % 100 == 0:
                print(f"[Pipeline3-声纹]   已处理 {i+1}/{len(vibevoice_turns)} 个片段...", flush=True)
        print(f"[Pipeline3-声纹] ✓ embedding 提取完成: {len(valid_turns)} 个有效, {skip_count} 个过短跳过", flush=True)
        self._report_progress("声纹识别", 0.3)

        # 每个片段单独与数据库比对
        print(f"[Pipeline3-声纹] 开始与 {len(self._registered_embeddings)} 位注册说话人逐一比对（阈值: {self.SIM_THRESHOLD}）...", flush=True)
        num_reg = len(self._registered_embeddings)
        # 批量比对：所有 query emb 堆叠，一次矩阵乘法
        if valid_turns:
            query_embs = np.stack([t["_emb"] for t in valid_turns])  # (N, 192)
            reg_ids = list(self._registered_embeddings.keys())
            reg_embs = np.stack([self._registered_embeddings[sid] for sid in reg_ids])  # (M, 192)
            # 余弦相似度：已归一化，直接 dot
            sim_matrix = np.dot(query_embs, reg_embs.T)  # (N, M)
            # 每行排序
            sorted_idx = np.argsort(-sim_matrix, axis=1)
        else:
            sim_matrix = np.zeros((0, 0))
            sorted_idx = np.zeros((0, 0), dtype=int)

        print(f"[Pipeline3-声纹] ✓ 比对完成: {len(valid_turns)} 片段 × {num_reg} 注册人 = {len(valid_turns) * num_reg} 次比较", flush=True)
        self._report_progress("声纹识别", 0.6)

        # 打印转写预览（每个片段独立显示 Top-3）
        print(f"[Pipeline3-声纹] ═══ 转写预览（每个片段独立 Top-3）══════════════════════", flush=True)
        # 建立 vibevoice_turns index → sim_matrix row 的映射
        valid_idx = 0
        vi_to_sim_row: Dict[int, int] = {}
        for vi_idx, item in enumerate(vibevoice_turns):
            if "Speaker" not in item:
                continue
            dur = item["End"] - item["Start"]
            if dur < 0.5:
                continue
            vi_to_sim_row[vi_idx] = valid_idx
            valid_idx += 1
        for vi_idx, item in enumerate(vibevoice_turns):
            if "Speaker" not in item:
                continue
            dur = item["End"] - item["Start"]
            if dur < 0.5:
                continue
            row = vi_to_sim_row[vi_idx]
            top3 = [f"{self._speaker_names.get(reg_ids[sorted_idx[row][r]], reg_ids[sorted_idx[row][r]])}({sim_matrix[row][sorted_idx[row][r]]:.3f})" for r in range(min(3, num_reg))]
            best_sim = sim_matrix[row][sorted_idx[row][0]]
            best_name = self._speaker_names.get(reg_ids[sorted_idx[row][0]], reg_ids[sorted_idx[row][0]])
            status = "✓" if best_sim >= self.SIM_THRESHOLD else "✗"
            print(f"[Pipeline3-声纹]   [{status}] {best_name}: \"{item['Content'][:60]}...\"", flush=True)
            print(f"[Pipeline3-声纹]      Top-3: {' | '.join(top3)}", flush=True)
        if len(valid_turns) == 0:
            print(f"[Pipeline3-声纹]   (无有效片段)", flush=True)

        # 构建 TurnSegment
        for vi_idx, item in enumerate(vibevoice_turns):
            if "Speaker" not in item:
                continue
            dur = item["End"] - item["Start"]
            if dur < 0.5:
                continue
            # 空声纹数据库：直接标记 unknown
            if not self._registered_embeddings:
                results.append(TurnSegment(
                    speaker_id="unknown",
                    speaker_name=None,
                    text=item["Content"].strip(),
                    start_ms=int(item["Start"] * 1000),
                    end_ms=int(item["End"] * 1000),
                    confidence=0.0,
                    top3_ids=[],
                    merged_from=1,
                ))
                continue

            row = vi_to_sim_row[vi_idx]
            best_sim = float(sim_matrix[row][sorted_idx[row][0]])
            if best_sim >= self.SIM_THRESHOLD:
                speaker_id = reg_ids[sorted_idx[row][0]]
                confidence = best_sim
                top3_ids = [f"{reg_ids[sorted_idx[row][r]]}({sim_matrix[row][sorted_idx[row][r]]:.3f})" for r in range(min(5, num_reg))]
            else:
                speaker_id = "unknown"
                confidence = best_sim
                top3_ids = [f"{reg_ids[sorted_idx[row][r]]}({sim_matrix[row][sorted_idx[row][r]]:.3f})" for r in range(min(5, num_reg))]

            text = item["Content"].strip()
            # ASR 后置过滤：跳过纯语气词/无意义内容
            if text:
                chinese = ''.join(c for c in text if '\u4e00' <= c <= '\u9fff')
                is_meaningless = (
                    chinese in _MEANINGLESS_PATTERNS or
                    (len(chinese) == 0 and len(text) < 3)
                )
                if is_meaningless:
                    continue

            results.append(TurnSegment(
                speaker_id=speaker_id,
                speaker_name=self._speaker_names.get(speaker_id),
                text=text,
                start_ms=int(item["Start"] * 1000),
                end_ms=int(item["End"] * 1000),
                confidence=confidence,
                top3_ids=top3_ids[:3],
                merged_from=1,
            ))

        print(f"[Pipeline3-声纹] ✓ 声纹识别完成: {len(results)} 个片段", flush=True)
        self._report_progress("声纹识别", 1.0)
        return results

    # ─── Turns 组装 ─────────────────────────────────────────────

    def _build_turns(self, segments: List[TurnSegment]) -> List[TurnSegment]:
        if not segments:
            return []
        segments = sorted(segments, key=lambda s: s.start_ms)
        merged: List[TurnSegment] = []
        cur = TurnSegment(
            speaker_id=segments[0].speaker_id,
            speaker_name=segments[0].speaker_name,
            text=segments[0].text,
            words=list(segments[0].words),
            start_ms=segments[0].start_ms,
            end_ms=segments[0].end_ms,
            confidence=segments[0].confidence,
            top3_ids=list(segments[0].top3_ids),
            merged_from=segments[0].merged_from,
        )
        for i in range(1, len(segments)):
            nxt = segments[i]
            gap = nxt.start_ms - cur.end_ms
            if cur.speaker_id == nxt.speaker_id and gap < 5000:
                cur.text = cur.text + " " + nxt.text
                cur.end_ms = nxt.end_ms
                cur.confidence = max(cur.confidence, nxt.confidence)
                cur.merged_from += nxt.merged_from
            else:
                merged.append(cur)
                cur = TurnSegment(
                    speaker_id=nxt.speaker_id,
                    speaker_name=nxt.speaker_name,
                    text=nxt.text,
                    words=list(nxt.words),
                    start_ms=nxt.start_ms,
                    end_ms=nxt.end_ms,
                    confidence=nxt.confidence,
                    top3_ids=list(nxt.top3_ids),
                    merged_from=nxt.merged_from,
                )
        merged.append(cur)
        return merged

    def _collect_speakers(self, turns: List[TurnSegment]) -> Dict[str, Dict]:
        stats: Dict[str, Dict] = {}
        for t in turns:
            sid = t.speaker_id or "unknown"
            if sid not in stats:
                stats[sid] = {
                    "name": t.speaker_name,
                    "count": 0,
                    "total_duration_ms": 0,
                }
            stats[sid]["count"] += 1
            stats[sid]["total_duration_ms"] += t.end_ms - t.start_ms
        return stats

    # ─── 摘要 ──────────────────────────────────────────────────

    async def _generate_summary(self, turns: List[TurnSegment]) -> Dict:
        if not turns:
            return {}
        transcript_lines = []
        for t in turns:
            label = t.speaker_name if t.speaker_name else (t.speaker_id or "unknown")
            entry = {"speaker": label, "text": t.text}
            if t.top3_ids:
                entry["top3"] = t.top3_ids[:3]
            transcript_lines.append(entry)
        try:
            from app.services.summary_service import SummaryService
            service = SummaryService()

            def _call():
                return service._call_meetingsummary_from_lines(
                    transcript_lines,
                    prefix=f"final3_{self.meeting_id[:8]}",
                    subdir="final",
                )
            result = await asyncio.to_thread(_call)
            if result:
                return {
                    "overview": (result.get("bullet_points", ["摘要生成失败"])[0]
                                 if result.get("bullet_points") else "无法生成摘要"),
                    "key_decisions": result.get("key_decisions", []),
                    "action_items": result.get("action_items", []),
                }
        except Exception as e:
            print(f"[Pipeline3] 摘要生成失败: {e}", flush=True)
        return {}


# ─── API 入口函数（供 meetings.py 调用） ─────────────────────────

async def process_audio(
    meeting_id: str,
    audio_data: np.ndarray,
    sample_rate: int = 16000,
    language: str = "zh",
    on_progress: Optional[Callable[[str, float], None]] = None,
) -> ProcessingResult:
    """
    音频离线处理入口：VibeVoice-ASR + CAM++。
    供 FastAPI 后台任务调用。
    """
    pipeline = OfflinePipeline(
        meeting_id=meeting_id,
        sample_rate=sample_rate,
        language=language,
        on_progress=on_progress,
    )
    return await pipeline.process(audio_data)
