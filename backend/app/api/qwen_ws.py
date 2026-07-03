"""
Qwen3-ASR 近实时 WebSocket 处理器 — 第三种实时转录模式。

链路（前端表现对齐 whisper 模式，复用同一套 transcript.completed 协议）：
  浏览器 ─PCM(int16/16k/单声道)→ 本处理器
     → StreamingVAD 静音切句（复用 FunASR 模式的 VAD，不改它）
     → 每句：CAM++ 在线聚类(说话人分离) + CAM++ 身份识别（复用 whisper 模式那套引擎）
            + 调隔离环境里的 Qwen-ASR 微服务(HTTP) 做转写
     → 发送 transcript.completed

ASR 在独立隔离环境(py3.12/cu130)的微服务里跑，避免污染主 .venv；
说话人分离/识别复用主环境已加载的 CAM++ 模型。
"""
import asyncio
import json
import os
import traceback
import urllib.request
from collections import defaultdict

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

# 复用 whisper 模式已写好的声纹引擎构建 / 持久化 / 阈值
from app.api.whisper_ws import (
    _build_speaker_engine,
    _persist_transcript,
    _SPK_COS_THRESHOLD,
    _SPK_GAP_MIN,
)

router = APIRouter()

QWEN_ASR_URL = os.getenv("QWEN_ASR_URL", "http://127.0.0.1:8030/asr")
QWEN_ASR_HEALTH_URL = os.getenv("QWEN_ASR_HEALTH_URL", "http://127.0.0.1:8030/health")
QWEN_ASR_LANG = os.getenv("QWEN_ASR_LANG", "Chinese")          # 与 whisper 模式一致，默认中文；可改 "auto"
_CLUSTER_THRESHOLD = 0.5           # CAM++ 在线聚类：余弦 >= 此值视为同一说话人


def _qwen_transcribe(pcm_int16_bytes: bytes, language: str = "Chinese"):
    """线程内执行：POST int16 PCM 到 Qwen-ASR 微服务，返回 (text, language)。"""
    url = "%s?language=%s" % (QWEN_ASR_URL, language)
    req = urllib.request.Request(
        url, data=pcm_int16_bytes,
        headers={"Content-Type": "application/octet-stream"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        d = json.loads(r.read().decode("utf-8"))
    if "error" in d:
        raise RuntimeError(d["error"])
    return d.get("text", ""), d.get("language")


def _identify(engine, audio_f32):
    """CAM++ 身份识别：返回 {name, cos, gap} 或 None。"""
    try:
        result = engine.identify(audio_f32)
        if result and result.matches:
            top = result.matches[0]
            second = float(result.matches[1].cosine_score) if len(result.matches) > 1 else 0.0
            return {"name": top.name or top.speaker_id,
                    "cos": float(top.cosine_score),
                    "gap": float(top.cosine_score) - second}
    except Exception as e:
        print("[Qwen-声纹] 识别异常: %s" % e, flush=True)
    return None


class _OnlineSpeakerCluster:
    """基于 CAM++ embedding 的在线说话人聚类，给每句分配 speaker_N（无需事先注册）。"""

    def __init__(self, threshold: float = _CLUSTER_THRESHOLD):
        self.threshold = threshold
        self.centroids = []  # [{label, emb(np.ndarray), n}]

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


def _qwen_service_ready() -> bool:
    try:
        with urllib.request.urlopen(QWEN_ASR_HEALTH_URL, timeout=3) as r:
            return json.loads(r.read()).get("ready", False)
    except Exception:
        return False


async def handle_qwen_websocket(websocket: WebSocket, meeting_id: str):
    await websocket.accept()
    # config（对齐 whisper 协议，前端复用同一套）
    try:
        await websocket.send_json({"type": "config", "useAudioWorklet": True, "mode": "full"})
    except Exception:
        return

    if not await asyncio.to_thread(_qwen_service_ready):
        await websocket.send_json({"type": "error", "message": "Qwen-ASR 微服务未就绪(%s)" % QWEN_ASR_HEALTH_URL})
        return

    # 说话人分离/识别引擎（复用 whisper 模式那套）
    speaker_engine = await asyncio.to_thread(_build_speaker_engine)
    extractor = getattr(speaker_engine, "extractor", None) if speaker_engine else None
    cluster = _OnlineSpeakerCluster()
    cluster_id_votes = defaultdict(lambda: defaultdict(float))   # 簇 -> {身份名: 累积余弦票}

    # VAD 切句（复用 FunASR 模式的 StreamingVAD，不改它）
    from app.asr.model_manager import get_model_manager
    from app.asr.streaming_pipeline import StreamingVAD
    vad = StreamingVAD(get_model_manager().get_vad_model())

    # 声纹突变切句：给 VAD 接上 CAM++ embedding 提取器（机制同 FunASR 模式）——
    # 后台每 100ms 提取一次 embedding，VAD 在说话人突变处也切句(reason="voice_change")，
    # 不止依赖静音停顿；无 extractor 时退化为纯静音切句。
    emb_task = None
    if extractor is not None:
        vad.set_embedding_extractor(extractor)

        async def _emb_loop():
            try:
                while True:
                    await asyncio.to_thread(vad.extract_pending_embeddings)
                    await asyncio.sleep(0.1)
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

        emb_task = asyncio.create_task(_emb_loop())

    async def _process_segment(seg):
        audio = seg.audio_data
        if audio is None or len(audio) < 1600:  # < 0.1s 跳过
            return
        pcm16 = (np.clip(audio, -1.0, 1.0) * 32768.0).astype(np.int16).tobytes()
        try:
            text, _lang = await asyncio.to_thread(_qwen_transcribe, pcm16, QWEN_ASR_LANG)
        except Exception as e:
            print("[Qwen] 转写失败: %s" % e, flush=True)
            return
        if not text or not text.strip():
            return

        # 说话人分离（在线聚类）
        spk_label = "speaker_1"
        if extractor is not None:
            try:
                emb = await asyncio.to_thread(extractor.extract, audio)
                spk_label = cluster.assign(emb) or spk_label
            except Exception:
                pass

        # 身份识别（逐句识别 → 按"簇"累积投票：同簇身份一致；不同簇若识别成同一人则自动并名）
        # 不依赖说话人数量：匿名聚类自适应有几个人，每个簇按累积票数贴名。
        identified, name, conf, best_name, best_score = False, None, 1.0, None, None
        if speaker_engine is not None:
            res = await asyncio.to_thread(_identify, speaker_engine, audio)
            if res:
                best_name, best_score = res["name"], res["cos"]
                if res["cos"] >= _SPK_COS_THRESHOLD and res["gap"] >= _SPK_GAP_MIN:
                    cluster_id_votes[spk_label][res["name"]] += float(res["cos"])
            votes = cluster_id_votes.get(spk_label)
            if votes:
                name = max(votes.items(), key=lambda kv: kv[1])[0]
                identified, conf = True, (float(best_score) if best_score else 1.0)

        disp_name = name if identified else spk_label
        out_speaker_id = name if identified else spk_label
        msg = {
            "type": "transcript.completed",
            "source": "qwen",
            "speaker_id": out_speaker_id,
            "text": text,
            "start_ms": int(seg.start_ms),
            "end_ms": int(seg.end_ms),
            "start_time": seg.start_ms / 1000.0,
            "end_time": seg.end_ms / 1000.0,
            "is_final": True,
            "speaker_confidence": conf if identified else 1.0,
            "speaker_name": disp_name,
            "identified": identified,
            "best_guess_name": best_name,
            "best_guess_score": best_score,
            "segment_reason": getattr(seg, "segment_reason", None),
        }
        try:
            await websocket.send_json(msg)
        except Exception:
            return
        asyncio.create_task(_persist_transcript(
            meeting_id=meeting_id, speaker_id=out_speaker_id, text=text,
            start_ms=seg.start_ms, end_ms=seg.end_ms,
            confidence=conf if identified else 1.0, speaker_name=disp_name,
        ))

    # ── 相邻短段合并 ──────────────────────────────────────────────
    # 把碎片（短插话 / 被静音切碎的句子）按同说话人累积到约 TARGET 秒再送 ASR+识别，
    # 大幅减少边界掉字，并让声纹有足够时长（提升识别）。voice_change 处先冲刷上一段。
    import types as _types
    _MERGE_TARGET_MS = 4000     # 累积语音到约 4s 再送
    _MERGE_MAX_MS = 12000       # 合并后最长 12s（限延迟+ASR 长度）
    pending = {"seg": None}

    def _seg_speech_ms(x):
        return len(x.audio_data) / 16000.0 * 1000.0

    def _merge_segs(a, b):
        return _types.SimpleNamespace(
            audio_data=np.concatenate([a.audio_data, b.audio_data]),
            start_ms=a.start_ms, end_ms=b.end_ms, segment_reason="merged",
        )

    async def _feed_merge(seg):
        p = pending["seg"]
        if p is None:
            pending["seg"] = seg
        elif seg.segment_reason == "voice_change":
            pending["seg"] = seg               # 说话人变了：先送旧的
            await _process_segment(p)
        elif _seg_speech_ms(p) < _MERGE_TARGET_MS and (_seg_speech_ms(p) + _seg_speech_ms(seg)) <= _MERGE_MAX_MS:
            pending["seg"] = _merge_segs(p, seg)  # 仍短：继续拼
        else:
            pending["seg"] = seg               # 够长了：送旧的、开新的
            await _process_segment(p)

    async def _flush_pending():
        if pending["seg"] is not None:
            x = pending["seg"]; pending["seg"] = None
            await _process_segment(x)

    try:
        while True:
            message = await websocket.receive_bytes()
            if message == b"":
                # 结束：补 1s 静音冲刷 VAD，再冲刷合并缓冲
                tail = await asyncio.to_thread(vad.feed, np.zeros(16000, dtype=np.float32))
                if tail is not None:
                    await _feed_merge(tail)
                await _flush_pending()
                break
            pcm = np.frombuffer(message, dtype=np.int16).astype(np.float32) / 32768.0
            seg = await asyncio.to_thread(vad.feed, pcm)
            if seg is not None:
                await _feed_merge(seg)
    except WebSocketDisconnect:
        print("[Qwen] 客户端断开 (meeting_id=%s)" % meeting_id, flush=True)
    except Exception as e:
        print("[Qwen] WS 主循环异常: %s" % e, flush=True)
        traceback.print_exc()
    finally:
        if emb_task is not None:
            emb_task.cancel()
        try:
            await websocket.send_json({"type": "ready_to_stop"})
        except Exception:
            pass
        print("[Qwen] 会话已清理 (meeting_id=%s)" % meeting_id, flush=True)


@router.websocket("/ws/meeting/{meeting_id}/qwen")
async def qwen_websocket_endpoint(websocket: WebSocket, meeting_id: str):
    await handle_qwen_websocket(websocket, meeting_id)
