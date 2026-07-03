# -*- coding: utf-8 -*-
"""
FunASR 简洁实时 WebSocket 端点 —— 专供 API/SDK 对接。

与 /ws/meeting/{id}（前端用、JSON+base64+broadcast 富协议）不同，本端点协议与
whisper/qwen 完全一致：
  客户端 → 二进制 PCM(int16/16k/单声道) 帧；空帧 b"" 结束
  服务端 → {"type":"config"} / {"type":"transcript.completed", ...} / {"type":"ready_to_stop"}
这样三种模式 SDK 可统一对接。底层复用 FunASR 流式管线(含变点/识别/去噪门控改进)。
"""
import asyncio
import traceback

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.api.whisper_ws import _persist_transcript

router = APIRouter()


@router.websocket("/ws/meeting/{meeting_id}/funasr")
async def funasr_websocket_endpoint(websocket: WebSocket, meeting_id: str):
    await websocket.accept()
    try:
        await websocket.send_json({"type": "config", "useAudioWorklet": True, "mode": "full"})
    except Exception:
        return

    from app.asr.model_manager import get_model_manager, SpeakerEmbeddingExtractor
    from app.asr.streaming_pipeline import create_streaming_pipeline
    from app.asr.enhanced_engine import EnhancedRecognitionEngine
    from app.services.speaker_db_service import get_speaker_db, bytes_to_ndarray

    mm = get_model_manager()
    if not mm.is_initialized():
        await mm.initialize()

    pipe = create_streaming_pipeline(mm, language="zh")
    try:
        eng = EnhancedRecognitionEngine(SpeakerEmbeddingExtractor(mm.get_camp_model(), device=mm.device))
        n = 0
        for sp in get_speaker_db().load_all(active_only=True):
            emb = bytes_to_ndarray(sp.get("embedding"))
            if emb is not None:
                eng.register_embedding(sp["speaker_id"], emb, name=sp.get("name"), role=sp.get("role"))
                try:
                    pipe.register_speaker(sp["speaker_id"], emb, name=sp.get("name"), role=sp.get("role"))
                except Exception:
                    pass
                n += 1
        pipe.set_enhanced_registry(eng)
        print("[FunASR-API] 引擎就绪，注册声纹 %d 人 (meeting=%s)" % (n, meeting_id), flush=True)
    except Exception as e:
        print("[FunASR-API] 声纹引擎初始化失败(转写继续，不做识别): %s" % e, flush=True)

    async def on_transcript(delta):
        text = (getattr(delta, "text", "") or "").strip()
        if not text:
            return
        sid = getattr(delta, "speaker_id", None)
        name = getattr(delta, "speaker_name", None)
        identified = bool(name and sid not in (None, "unknown", "__unknown__"))
        s0 = int(getattr(delta, "start_ms", 0) or 0)
        s1 = int(getattr(delta, "end_ms", 0) or 0)
        conf = float(getattr(delta, "speaker_confidence", 1.0) or 1.0)
        msg = {
            "type": "transcript.completed",
            "source": "funasr",
            "speaker_id": sid or "unknown",
            "speaker_name": name or sid or "unknown",
            "text": text,
            "start_ms": s0,
            "end_ms": s1,
            "start_time": s0 / 1000.0,
            "end_time": s1 / 1000.0,
            "is_final": True,
            "speaker_confidence": conf,
            "identified": identified,
        }
        try:
            await websocket.send_json(msg)
        except Exception:
            return
        asyncio.create_task(_persist_transcript(
            meeting_id=meeting_id, speaker_id=msg["speaker_id"], text=text,
            start_ms=s0, end_ms=s1, confidence=conf, speaker_name=msg["speaker_name"],
        ))

    pipe.on_transcript = on_transcript
    await pipe.start()

    try:
        while True:
            message = await websocket.receive_bytes()
            if message == b"":
                break
            arr = np.frombuffer(message, dtype=np.int16).astype(np.float32) / 32768.0
            await pipe.feed_audio(arr)
    except WebSocketDisconnect:
        print("[FunASR-API] 客户端断开 (meeting=%s)" % meeting_id, flush=True)
    except Exception as e:
        print("[FunASR-API] 主循环异常: %s" % e, flush=True)
        traceback.print_exc()
    finally:
        try:
            await pipe.stop()
        except Exception:
            pass
        try:
            await websocket.send_json({"type": "ready_to_stop"})
        except Exception:
            pass
        print("[FunASR-API] 会话清理 (meeting=%s)" % meeting_id, flush=True)
