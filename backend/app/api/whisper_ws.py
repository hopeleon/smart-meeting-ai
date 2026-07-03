"""
WhisperLiveKit WebSocket 处理器 — 第三转录模式

WebSocket 消息格式（WhisperLiveKit 原生协议）：
  客户端 → 服务端:
    二进制音频帧（int16 PCM 16kHz 单声道）
    空字节 b"" 表示结束

  服务端 → 客户端:
    {"type": "config", "useAudioWorklet": true, "mode": "full"}
    {"type": "transcript.completed", "speaker_id": "...", "text": "...", "start_ms": ..., "end_ms": ...}
    {"type": "ready_to_stop"}
"""

import asyncio
import os
import numpy as np
import json
import time
import traceback
import threading
import uuid
from typing import Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import async_session
from app.models.transcript import TranscriptLine

router = APIRouter()

# 全局 WhisperLiveKit 引擎（延迟初始化，首次 WS 连接时加载）
_whisper_engine = None
_whisper_ready = False

# --- 声纹身份识别（仅 whisper 模式；复用注册/realtime 同一套 CAM++ 引擎）---
_SPK_COS_THRESHOLD = 0.5     # top1 余弦 >= 此值才接受身份（offline 用 0.20 偏松；融合分尺度被压缩不可用，故卡余弦；可调）
_SPK_GAP_MIN = 0.06          # top1 与 top2 余弦差 >= 此值，避免模棱两可误认
_SPK_MIN_SEC = 1.2           # 片段时长 >= 此值才尝试识别（太短声纹不可靠）
_SPK_RETRY_GAP_SEC = 2.0     # 同一未识别说话人，至少新增这么多音频才再试一次


import re as _re_ws
try:
    import opencc as _opencc
    _T2S = _opencc.OpenCC("t2s")
except Exception:
    _T2S = None
# whisper-small 对中文常输出繁体并误打方言标签 (台語)/(語)，去标签 + 繁转简
_DIALECT_TAG = _re_ws.compile(r"[（(]\s*(台語|台语|閩南語|闽南语|粵語|粤语|語|语)\s*[)）]")


def _to_simplified(text):
    if not text:
        return text
    text = _DIALECT_TAG.sub("", text)
    if _T2S is not None:
        try:
            text = _T2S.convert(text)
        except Exception:
            pass
    return text


# Whisper 常在静音/非语音段吐出的“套话”幻觉(YouTube 字幕味)，整句屏蔽，不误伤正常话
_HALLUCINATION = (
    "谢谢大家的收看", "谢谢大家收看", "谢谢观看", "感谢观看", "谢谢收看",
    "谢谢大家的观看", "下次见", "下次再见", "我们下次再见", "下集再见",
    "请不要忘记订阅", "记得订阅", "点赞订阅", "请按赞订阅", "请订阅",
    "多谢收看", "字幕由", "本字幕", "明镜与点点",
)


def _looks_like_hallucination(text):
    """整句基本就是套话幻觉时返回 True(幻觉短语需占整句一半以上，避免误杀长正常句)。"""
    t = _re_ws.sub("[^一-鿿A-Za-z]", "", text or "")
    if len(t) < 2:
        return False
    for p in _HALLUCINATION:
        if p in t and len(p) >= len(t) * 0.5:
            return True
    return False


def _build_speaker_engine():
    """构建声纹识别引擎：CAM++ 提取器 + 从声纹库加载已注册说话人。
    与注册接口 / realtime 完全相同的提取与匹配逻辑，保证 embedding 空间一致。
    任何环节失败返回 None（whisper 转写不受影响，仅退化为匿名说话人）。"""
    try:
        import numpy as np
        from app.asr.model_manager import load_campplus_model, SpeakerEmbeddingExtractor
        from app.asr.enhanced_engine import EnhancedRecognitionEngine
        from app.services.speaker_db_service import get_speaker_db, bytes_to_ndarray

        camp_model, device = load_campplus_model()
        extractor = SpeakerEmbeddingExtractor(camp_model, device=device)
        engine = EnhancedRecognitionEngine(extractor)

        loaded = 0
        for sp in get_speaker_db().load_all(active_only=True):
            raw = sp.get("embedding")
            emb = bytes_to_ndarray(raw) if raw is not None else None
            if emb is None and isinstance(raw, np.ndarray):
                emb = raw
            if emb is not None:
                engine.register_embedding(sp["speaker_id"], emb,
                                          name=sp.get("name"), role=sp.get("role"))
                loaded += 1
        if loaded:
            print("[Whisper-声纹] 声纹引擎就绪，已注册 %d 位说话人" % loaded, flush=True)
        else:
            print("[Whisper-声纹] 声纹库为空，说话人将保持匿名（需先在声纹管理里注册）", flush=True)
        return engine
    except Exception as e:
        print("[Whisper-声纹] 声纹引擎初始化失败，转写继续但不做身份识别: %s" % e, flush=True)
        return None


def _identify_pcm_bytes(speaker_engine, seg_bytes):
    """线程内执行：int16 PCM 字节 -> float32 -> CAM++ 识别，返回 (name, score) 或 None。"""
    try:
        import numpy as np
        pcm = np.frombuffer(seg_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        if pcm.size == 0:
            return None
        result = speaker_engine.identify(pcm)
        if result and result.matches:
            top = result.matches[0]
            second = float(result.matches[1].cosine_score) if len(result.matches) > 1 else 0.0
            return {"name": top.name or top.speaker_id,
                    "cos": float(top.cosine_score),
                    "fused": float(top.final_score),
                    "gap": float(top.cosine_score) - second}
        return None
    except Exception as e:
        print("[Whisper-声纹] 片段识别异常: %s" % e, flush=True)
        return None


def _parse_time_str(time_str: str) -> float:
    """解析 H:MM:SS 或 MM:SS 格式时间字符串为秒数"""
    parts = time_str.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    if len(parts) == 2:
        return int(parts[0]) * 60 + float(parts[1])
    return float(parts[0])


async def _ensure_engine():
    """延迟初始化 Faster-Whisper 引擎（线程中执行，不阻塞事件循环）"""
    global _whisper_engine, _whisper_ready
    if _whisper_engine is not None:
        return _whisper_engine

    def _try_build(diarization: bool):
        from whisperlivekit import TranscriptionEngine
        print(f"[Whisper] 初始化 Faster-Whisper 引擎（diarization={diarization}）...", flush=True)
        return TranscriptionEngine(
            model_size=os.getenv("WHISPER_MODEL_SIZE", "large-v3"),  # small->large-v3：真实会议明显更准；环境变量可回退
            lan="zh",
            min_chunk_size=float(os.getenv("WHISPER_MIN_CHUNK_SEC", "2.0")),  # 5s预算下加长chunk，更完整上下文
            diarization=diarization,
            device="cuda",
            compute_type="float16",
        )

    try:
        _whisper_engine = await asyncio.to_thread(_try_build, True)
        _whisper_ready = True
        print("[Whisper] Faster-Whisper 引擎就绪（PCM 模式）", flush=True)
        return _whisper_engine
    except (SystemExit, Exception) as e:
        # diarization 初始化失败（缺依赖=SystemExit / 模型缺失或网络错误=普通异常）一律降级
        print(f"[Whisper] diarization 初始化失败（{type(e).__name__}: {e}），回退到 diarization=False", flush=True)
        try:
            _whisper_engine = await asyncio.to_thread(_try_build, False)
            _whisper_ready = True
            print("[Whisper] Faster-Whisper 引擎就绪（PCM 模式，无 diarization）", flush=True)
            return _whisper_engine
        except Exception as e2:
            print(f"[Whisper] 无 diarization 模式也失败: {e2}", flush=True)
            raise


async def _persist_transcript(meeting_id: str, speaker_id: str, text: str,
                                start_ms: float, end_ms: float, confidence: float,
                                speaker_name: str = None):
    """将转写结果持久化到数据库"""
    try:
        from datetime import datetime as dt

        async with async_session() as db:
            db_line = TranscriptLine(
                id=str(uuid.uuid4()),
                meeting_id=meeting_id,
                speaker_id=speaker_id,
                speaker_label=speaker_name or speaker_id,
                text=text,
                start_time=start_ms / 1000.0,
                end_time=end_ms / 1000.0,
                confidence=confidence,
                created_at=dt.utcnow(),
            )
            db.add(db_line)
            await db.commit()
    except Exception as db_err:
        print(f"[Whisper] 持久化失败: {db_err}", flush=True)


class _OnlineSpeakerCluster:
    """基于 CAM++ embedding 的在线说话人聚类（无需事先注册，无说话人上限）。"""

    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold
        self.centroids = []  # [{label, emb, n}]

    def assign(self, emb):
        if emb is None or np.linalg.norm(emb) < 1e-6:
            return None
        best, best_sim = None, -1.0
        for c in self.centroids:
            sim = float(np.dot(emb, c["emb"]) /
                        (np.linalg.norm(emb) * np.linalg.norm(c["emb"]) + 1e-8))
            if sim > best_sim:
                best_sim, best = sim, c
        if best is not None and best_sim >= self.threshold:
            best["emb"] = (best["emb"] * best["n"] + emb) / (best["n"] + 1)
            best["n"] += 1
            return best["label"]
        label = "speaker_%d" % (len(self.centroids) + 1)
        self.centroids.append({"label": label, "emb": emb.copy(), "n": 1})
        return label


_pyannote_pipeline = None
_pyannote_lock = threading.Lock()


def _get_pyannote_pipeline():
    """懒加载 pyannote/speaker-diarization-4.0（本地缓存，离线可用）。失败返回 None。"""
    global _pyannote_pipeline
    if _pyannote_pipeline is not None:
        return _pyannote_pipeline
    with _pyannote_lock:
        if _pyannote_pipeline is not None:
            return _pyannote_pipeline
        import os, huggingface_hub
        from huggingface_hub import constants as _hfc
        _prev = os.environ.get("HF_HUB_OFFLINE")
        _prev_c = getattr(_hfc, "HF_HUB_OFFLINE", None)
        os.environ["HF_HUB_OFFLINE"] = "1"
        # 关键：huggingface_hub 在导入时已缓存该常量，运行时必须直接改常量才生效
        try: _hfc.HF_HUB_OFFLINE = True
        except Exception: pass
        try: huggingface_hub.constants.HF_HUB_OFFLINE = True
        except Exception: pass
        try:
            import torch
            from pyannote.audio import Pipeline
            pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-4.0")
            if torch.cuda.is_available():
                pipe.to(torch.device("cuda"))
            _pyannote_pipeline = pipe
            print("[Whisper-pyannote] pipeline 加载完成", flush=True)
        except Exception as e:
            print("[Whisper-pyannote] pipeline 加载失败: %s" % e, flush=True)
            _pyannote_pipeline = None
        finally:
            if _prev is None:
                os.environ.pop("HF_HUB_OFFLINE", None)
            else:
                os.environ["HF_HUB_OFFLINE"] = _prev
            try: _hfc.HF_HUB_OFFLINE = _prev_c
            except Exception: pass
        return _pyannote_pipeline


class _PyannoteDiarizer:
    """pyannote 滑窗近实时 diarization：后台每 STEP 秒在最近 WINDOW 秒上跑 pyannote，
    每个 turn 用 CAM++ 聚类成全局稳定 speaker_N，提供 label_for(start,end) 查询。
    （牺牲跨窗一致性换实时性，可有延迟。）"""
    WINDOW_S = 20.0
    STEP_S = 4.0

    def __init__(self, audio_buf, extractor):
        self.buf = audio_buf
        self.extractor = extractor
        self.cluster = _OnlineSpeakerCluster()
        self.timeline = []   # [(start_s, end_s, label)]
        self._task = None
        self._running = False

    def start(self):
        self._running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()

    def _diarize(self, seg_bytes, base_s):
        import numpy as _np, torch as _t
        pipe = _get_pyannote_pipeline()
        if pipe is None:
            return []
        a = _np.frombuffer(seg_bytes, dtype=_np.int16).astype(_np.float32) / 32768.0
        with _pyannote_lock:
            out = pipe({"waveform": _t.from_numpy(a).unsqueeze(0), "sample_rate": 16000})
        ann = out.speaker_diarization
        res = []
        for turn, _, _spk in ann.itertracks(yield_label=True):
            if (turn.end - turn.start) < 0.8:
                continue
            ts, te = int(turn.start * 16000), int(turn.end * 16000)
            chunk = a[ts:te]
            try:
                emb = self.extractor.extract(chunk)
            except Exception:
                emb = None
            label = self.cluster.assign(emb) or "speaker_?"
            res.append((base_s + turn.start, base_s + turn.end, label))
        return res

    def _merge_timeline(self, turns, w0, w1):
        self.timeline = [t for t in self.timeline if t[1] <= w0 or t[0] >= w1]
        self.timeline.extend(turns)
        self.timeline.sort(key=lambda x: x[0])

    async def _loop(self):
        try:
            if await asyncio.to_thread(_get_pyannote_pipeline) is None:
                print("[Whisper-pyannote] pipeline 不可用，pyannote 模式退化为无分离", flush=True)
                return
            while self._running:
                await asyncio.sleep(self.STEP_S)
                total = len(self.buf) // 2
                if total < 2 * 16000:
                    continue
                win = int(self.WINDOW_S * 16000)
                start_sample = max(0, total - win)
                seg = bytes(self.buf[start_sample * 2: total * 2])
                base_s = start_sample / 16000.0
                try:
                    turns = await asyncio.to_thread(self._diarize, seg, base_s)
                except Exception as e:
                    print("[Whisper-pyannote] diarize 异常: %s" % e, flush=True)
                    continue
                self._merge_timeline(turns, base_s, base_s + len(seg) // 2 / 16000.0)
        except asyncio.CancelledError:
            pass

    def label_for(self, s0, s1):
        mid = (s0 + s1) / 2.0
        best = None
        for st, en, lab in self.timeline:
            if st <= mid <= en:
                best = lab
        return best


async def handle_whisper_websocket(websocket: WebSocket, meeting_id: str):
    """WhisperLiveKit WebSocket 处理主函数"""
    await websocket.accept()

    # 发送 config（对齐 WhisperLiveKit 协议）
    try:
        await websocket.send_json({
            "type": "config",
            "useAudioWorklet": True,
            "mode": "full",
        })
    except Exception as e:
        print(f"[Whisper] 发送 config 失败: {e}", flush=True)
        return

    # 初始化引擎
    try:
        engine = await _ensure_engine()
    except Exception as e:
        print(f"[Whisper] 引擎初始化失败，拒绝连接: {e}", flush=True)
        try:
            await websocket.send_json({"type": "error", "message": f"引擎初始化失败: {e}"})
        except Exception:
            pass
        return

    from whisperlivekit import AudioProcessor

    audio_processor = AudioProcessor(transcription_engine=engine)
    # 跳过 FFmpeg，直接接收原始 PCM（pcm_input 是 AudioProcessor 级别参数）
    audio_processor.is_pcm_input = True
    results_generator = await audio_processor.create_tasks()

    # PCM 缓冲（int16/16k/单声道，从流起点累计）+ 声纹识别引擎（仅 whisper 模式）
    audio_buf = bytearray()
    speaker_engine = await asyncio.to_thread(_build_speaker_engine)

    # diarization 方案（?diar=sortformer|camplus|pyannote，默认 sortformer）
    diar = (websocket.query_params.get("diar") or "sortformer").lower()
    if diar not in ("sortformer", "camplus", "pyannote"):
        diar = "sortformer"
    print(f"[Whisper] diarization 方案 = {diar}", flush=True)

    # 并行任务：处理结果 + 接收音频
    results_task = asyncio.create_task(
        _handle_results(websocket, meeting_id, results_generator, audio_buf, speaker_engine, diar)
    )

    try:
        while True:
            message = await websocket.receive_bytes()
            if message == b"":
                # 空帧 = 客户端结束音频流
                break
            audio_buf += message
            await audio_processor.process_audio(message)
    except WebSocketDisconnect:
        print(f"[Whisper] 客户端断开 (meeting_id={meeting_id})", flush=True)
    except Exception as e:
        print(f"[Whisper] WS 主循环异常: {e}", flush=True)
        traceback.print_exc()
    finally:
        results_task.cancel()
        try:
            await results_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[Whisper] 清理 results_task 时异常: {e}", flush=True)

        # 清理 AudioProcessor
        try:
            await audio_processor.cleanup()
        except Exception as e:
            print(f"[Whisper] AudioProcessor 清理失败: {e}", flush=True)

        print(f"[Whisper] 会话已清理 (meeting_id={meeting_id})", flush=True)


async def _handle_results(websocket: WebSocket, meeting_id: str, results_generator,
                          audio_buf: bytearray = None, speaker_engine=None, diar="sortformer"):
    """将 WhisperLiveKit FrontData 结果标准化后发送给客户端并持久化。
    若提供 speaker_engine + audio_buf，则对每个 sortformer 说话人做一次 CAM++ 声纹身份识别。"""
    seen_speaker_lines: set[str] = set()
    # 逐句识别：每个 (speaker, start) 句子独立识别，结果只作用于该句，不跨句缓存，
    # 避免单次误识别污染同一说话人的其它句子。
    seg_identity: dict = {}          # (speaker, start_key) -> {"name":.., "score":.., "end":..}
    _inflight: set = set()           # 正在识别中的句子 key，避免并发重复

    # diarization=camplus：CAM++ 在线聚类给 speaker 标签（无上限），按行缓存保持稳定
    _extractor = getattr(speaker_engine, "extractor", None) if speaker_engine else None
    _cluster = _OnlineSpeakerCluster() if diar == "camplus" else None
    _line_spk: dict = {}             # round(start,1) -> 稳定 speaker_id

    _pyd = None
    if diar == "pyannote" and audio_buf is not None and _extractor is not None:
        _pyd = _PyannoteDiarizer(audio_buf, _extractor)
        _pyd.start()

    async def _camplus_label(speaker_int, s0, s1):
        key = round(s0, 1)
        if key in _line_spk:
            return _line_spk[key]
        if (s1 - s0) < _SPK_MIN_SEC or _extractor is None or audio_buf is None:
            return None
        a = int(s0 * 16000) * 2
        b = min(int(s1 * 16000) * 2, len(audio_buf))
        if b - a < int(_SPK_MIN_SEC * 16000) * 2:
            return None
        pcm = np.frombuffer(bytes(audio_buf[a:b]), dtype=np.int16).astype(np.float32) / 32768.0
        try:
            emb = await asyncio.to_thread(_extractor.extract, pcm)
        except Exception:
            emb = None
        lab = (_cluster.assign(emb) if _cluster else None) or ("speaker_%s" % speaker_int)
        _line_spk[key] = lab
        return lab

    def _seg_key(spk, s0):
        return (spk, round(s0, 1))

    async def _try_identify(spk, s0, s1):
        if speaker_engine is None or audio_buf is None:
            return
        key = _seg_key(spk, s0)
        if key in _inflight or (s1 - s0) < _SPK_MIN_SEC:
            return
        prev = seg_identity.get(key)
        # 同一句已识别过：仅当又新增了足够音频，才用更完整的音频重识别（提升准确率）
        if prev is not None and (s1 - prev.get("end", s0)) < _SPK_RETRY_GAP_SEC:
            return
        a = int(s0 * 16000) * 2
        b = min(int(s1 * 16000) * 2, len(audio_buf))
        if b - a < int(_SPK_MIN_SEC * 16000) * 2:
            return
        seg = bytes(audio_buf[a:b])      # 事件循环线程内切片，避免与累计并发
        _inflight.add(key)
        try:
            res = await asyncio.to_thread(_identify_pcm_bytes, speaker_engine, seg)
            if res:
                accepted = res["cos"] >= _SPK_COS_THRESHOLD and res["gap"] >= _SPK_GAP_MIN
                # 总是记录最高分候选；identified 仅表示是否够自信
                seg_identity[key] = {"name": res["name"], "score": res["cos"],
                                     "identified": accepted, "end": s1}
                print("[Whisper-声纹] 句[spk%s@%.1fs] %s 最高=%s cos=%.3f gap=%.3f" % (
                    spk, s0, ("✓认定" if accepted else "?未达阈值"),
                    res["name"], res["cos"], res["gap"]), flush=True)
            else:
                seg_identity[key] = {"name": None, "score": 0.0, "identified": False, "end": s1}
        finally:
            _inflight.discard(key)

    try:
        async for front_data in results_generator:
            # front_data 是 FrontData 对象，调用 to_dict() 获取结构化数据
            d = front_data.to_dict() if hasattr(front_data, 'to_dict') else front_data
            lines = d.get("lines", []) if isinstance(d, dict) else []
            buffer_transcription = d.get("buffer_transcription", "") if isinstance(d, dict) else ""

            for line in lines:
                speaker = line.get("speaker", 0)
                text = line.get("text", "")
                text = _to_simplified(text)   # 繁转简 + 去 (台語) 方言标签
                start_str = line.get("start", "0:00:00")
                end_str = line.get("end", "0:00:00")

                # 跳过沉默片段和空文本
                if speaker == -2 or not text:
                    continue
                if _looks_like_hallucination(text):
                    continue   # 屏蔽 whisper 静音段套话幻觉

                start_s = _parse_time_str(start_str)
                end_s = _parse_time_str(end_str)

                # 基于 (speaker, text, start) 去重
                dedup_key = f"{speaker}|{text}|{start_s:.2f}"
                if dedup_key in seen_speaker_lines:
                    continue
                seen_speaker_lines.add(dedup_key)

                # 触发本句声纹识别（异步，不阻塞转写返回；逐句独立）
                if speaker_engine is not None:
                    asyncio.create_task(_try_identify(speaker, start_s, end_s))

                # diarization 标签：camplus 用 CAM++ 聚类(无上限)，其余用 sortformer 标签
                if diar == "camplus":
                    speaker_id = await _camplus_label(speaker, start_s, end_s)
                    if speaker_id is None:
                        continue   # 音频不足，等下次增长再发
                elif diar == "pyannote":
                    _k = round(start_s, 1)
                    if _k in _line_spk:
                        speaker_id = _line_spk[_k]
                    else:
                        _lab = _pyd.label_for(start_s, end_s) if _pyd else None
                        if _lab is None:
                            continue   # 滑窗还没覆盖到这句，等下次
                        speaker_id = _lab
                        _line_spk[_k] = _lab
                else:
                    speaker_id = f"speaker_{speaker}"

                _seg = seg_identity.get(_seg_key(speaker, start_s))
                _identified = bool(_seg and _seg.get("identified"))
                _best_name = _seg.get("name") if _seg else None     # 最高分候选（即使未达阈值）
                _best_score = _seg.get("score") if _seg else None
                _disp_name = _best_name if _identified else speaker_id
                _disp_conf = _best_score if _identified else 1.0

                # 标准化字段后发送给 WebSocket 客户端
                aligned = {
                    "type": "transcript.completed",
                    "source": "whisper",
                    "speaker_id": speaker_id,
                    "text": text,
                    "start_ms": int(start_s * 1000),
                    "end_ms": int(end_s * 1000),
                    "start_time": start_s,
                    "end_time": end_s,
                    "is_final": True,
                    "speaker_confidence": _disp_conf,
                    "speaker_name": _disp_name,
                    "identified": _identified,
                    "best_guess_name": _best_name,
                    "best_guess_score": _best_score,
                }

                try:
                    await websocket.send_json(aligned)
                except Exception as send_err:
                    print(f"[Whisper] 发送 WS 消息失败: {send_err}", flush=True)
                    return

                # 异步持久化（不阻塞结果发送）
                asyncio.create_task(
                    _persist_transcript(
                        meeting_id=meeting_id,
                        speaker_id=speaker_id,
                        text=text,
                        start_ms=start_s * 1000,
                        end_ms=end_s * 1000,
                        confidence=_disp_conf,
                        speaker_name=_disp_name,
                    )
                )

        # 所有音频处理完毕，发送结束信号
        await websocket.send_json({"type": "ready_to_stop"})
        print(f"[Whisper] 结果处理完毕 (meeting_id={meeting_id})", flush=True)

    except asyncio.CancelledError:
        print(f"[Whisper] 结果处理任务被取消 (meeting_id={meeting_id})", flush=True)
        raise
    except Exception as e:
        print(f"[Whisper] 结果处理异常: {e}", flush=True)
        traceback.print_exc()
    finally:
        if _pyd is not None:
            _pyd.stop()


@router.websocket("/ws/meeting/{meeting_id}/whisper")
async def whisper_websocket_endpoint(websocket: WebSocket, meeting_id: str):
    await handle_whisper_websocket(websocket, meeting_id)
