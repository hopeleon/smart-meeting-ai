# -*- coding: utf-8 -*-
"""
Hybrid realtime ASR endpoint.

Protocol:
  client -> server:
    binary int16 PCM, 16 kHz, mono
    empty binary frame or {"type": "end_meeting"} to stop

  server -> client:
    session.ready
    transcript.completed   FunASR low-latency draft
    transcript.revised     Qwen32B context-aware text revision
    ready_to_stop
"""
import asyncio
import json
import os
import re
import subprocess
import traceback
import uuid
from datetime import datetime
from difflib import SequenceMatcher
from urllib import error as urlerror
from urllib import request as urlrequest

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.database import async_session
from app.models.meeting import Meeting
from app.models.transcript import TranscriptLine

router = APIRouter()

_OLLAMA_BASE_URL = os.getenv("HYBRID_OLLAMA_BASE_URL", os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
_OLLAMA_MODEL = os.getenv("HYBRID_OLLAMA_MODEL", os.getenv("OLLAMA_MODEL", "qwen3:32b"))
_QWEN_TIMEOUT_SEC = float(os.getenv("HYBRID_QWEN_TIMEOUT_SEC", "90"))
_CONTEXT_REVISION_DELAY_SEC = float(os.getenv("HYBRID_CONTEXT_REVISION_DELAY_SEC", "4"))
_FINAL_REVISION_WAIT_SEC = float(os.getenv("HYBRID_FINAL_REVISION_WAIT_SEC", "35"))
_CONTEXT_BEFORE = int(os.getenv("HYBRID_CONTEXT_BEFORE", "8"))
_CONTEXT_AFTER = int(os.getenv("HYBRID_CONTEXT_AFTER", "4"))
_MIN_REVISION_CHARS = int(os.getenv("HYBRID_MIN_REVISION_CHARS", "4"))
_MIN_FREE_GPU_MB_FOR_QWEN = int(os.getenv("HYBRID_MIN_FREE_GPU_MB_FOR_QWEN", "22000"))

_REVISION_SYSTEM_PROMPT = """/no_think
你是会议实时转写校对助手。
任务：根据会议上下文，对指定的一条 FunASR 初稿做文本级校正。
硬性规则：
1. 只修正明显的错别字、同音错词、断句和标点。
2. 必须保留原句中的全部信息、语气、数字、专有名词和条件短语。
3. 不要总结、压缩、改写成更短表达，不要删除你不确定的内容。
4. 不能把上下文里其他人的内容合并到目标句段。
5. 如果不确定，或校正会丢失信息，原样返回 FunASR 初稿。
只输出 JSON：{"revised_text":"...","changed":true/false,"reason":"..."}"""


def _accept_revision(original: str, revised: str) -> bool:
    original = (original or "").strip()
    revised = (revised or "").strip()
    if not revised:
        return False
    if not original:
        return True
    if revised == original:
        return True
    min_ratio = 0.86 if len(original) <= 25 else 0.74
    if len(revised) < max(2, int(len(original) * min_ratio)):
        return False
    if len(revised) > max(32, int(len(original) * 2.2)):
        return False
    similarity = SequenceMatcher(None, original, revised).ratio()
    if len(original) <= 8 and similarity < 0.72:
        return False
    if len(original) <= 25 and similarity < 0.58:
        return False
    if similarity < 0.42:
        return False
    return True


def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()


def _extract_json_object(text: str) -> dict:
    cleaned = _strip_thinking(text)
    try:
        parsed = json.loads(cleaned)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        pass
    match = re.search(r"\{.*\}", cleaned, flags=re.S)
    if not match:
        return {}
    try:
        parsed = json.loads(match.group(0))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _call_qwen32b_revision(user_prompt: str) -> tuple[str, dict]:
    payload = {
        "model": _OLLAMA_MODEL,
        "stream": False,
        "think": False,
        "messages": [
            {"role": "system", "content": _REVISION_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": 0.0,
            "top_p": 0.8,
        },
    }
    req = urlrequest.Request(
        f"{_OLLAMA_BASE_URL}/api/chat",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(req, timeout=_QWEN_TIMEOUT_SEC) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urlerror.URLError as exc:
        raise RuntimeError(f"Ollama 不可用: {exc}") from exc
    content = data.get("message", {}).get("content", "")
    parsed = _extract_json_object(content)
    revised = str(parsed.get("revised_text") or "").strip()
    return revised, parsed


def _gpu_free_mb() -> int | None:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if result.returncode != 0:
            return None
        first = (result.stdout or "").strip().splitlines()[0].strip()
        return int(first)
    except Exception:
        return None


def _format_context(segments: list[dict], target_index: int) -> str:
    start = max(0, target_index - _CONTEXT_BEFORE)
    end = min(len(segments), target_index + _CONTEXT_AFTER + 1)
    lines = []
    for idx in range(start, end):
        seg = segments[idx]
        marker = " <= 待校正" if idx == target_index else ""
        speaker = seg.get("speaker_name") or seg.get("speaker_label") or seg.get("speaker_id") or "未知"
        text = seg.get("text") or ""
        lines.append(f"{idx + 1}. {speaker}: {text}{marker}")
    return "\n".join(lines)


def _build_revision_prompt(segments: list[dict], target_index: int) -> str:
    target = segments[target_index]
    speaker = target.get("speaker_name") or target.get("speaker_label") or target.get("speaker_id") or "未知"
    return (
        "下面是正在进行的会议转写上下文。请只校正标记为“待校正”的这一条。\n\n"
        "[会议上下文]\n"
        f"{_format_context(segments, target_index)}\n\n"
        "[待校正句段]\n"
        f"发言人：{speaker}\n"
        f"开始时间：{target.get('start_time', 0):.2f}s\n"
        f"FunASR 初稿：{target.get('original_text') or target.get('text') or ''}\n\n"
        "[输出要求]\n"
        "只输出 JSON，不要输出解释性正文。"
    )


async def _persist_initial(
    line_id: str,
    meeting_id: str,
    speaker_id: str,
    speaker_name: str,
    text: str,
    start_ms: int,
    end_ms: int,
    confidence: float,
):
    try:
        async with async_session() as db:
            result = await db.execute(select(Meeting.id).where(Meeting.id == meeting_id))
            if result.scalar_one_or_none() is None:
                return
            db.add(TranscriptLine(
                id=line_id,
                meeting_id=meeting_id,
                speaker_id=speaker_id,
                speaker_label=speaker_name or speaker_id,
                text=text,
                start_time=start_ms / 1000.0,
                end_time=end_ms / 1000.0,
                confidence=confidence,
                created_at=datetime.utcnow(),
            ))
            await db.commit()
    except Exception as exc:
        print("[Hybrid] FunASR 初稿持久化失败: %s" % exc, flush=True)


async def _persist_revision(line_id: str, revised_text: str):
    try:
        async with async_session() as db:
            result = await db.execute(select(TranscriptLine).where(TranscriptLine.id == line_id))
            line = result.scalar_one_or_none()
            if line is None:
                return
            line.text = revised_text
            await db.commit()
    except Exception as exc:
        print("[Hybrid] Qwen 修订持久化失败: %s" % exc, flush=True)


@router.websocket("/ws/meeting/{meeting_id}/hybrid")
async def hybrid_websocket_endpoint(websocket: WebSocket, meeting_id: str):
    await websocket.accept()
    revision_flag = (websocket.query_params.get("revision") or "").lower()
    fast_asr_only = (
        revision_flag in {"0", "false", "off", "none", "no"}
        or meeting_id.startswith("schedule-asr-")
    )

    try:
        await websocket.send_json({
            "type": "session.ready",
            "mode": "funasr_only" if fast_asr_only else "hybrid",
            "provider": "funasr" if fast_asr_only else "funasr+qwen32b",
            "message": "FunASR 快速识别已就绪" if fast_asr_only else "FunASR 实时初稿 + Qwen32B 上下文校正已就绪",
            "supports_microphone": True,
            "supports_revision": not fast_asr_only,
            "streaming": True,
            "revision_model": None if fast_asr_only else _OLLAMA_MODEL,
            "revision_policy": "disabled_fast_schedule_asr" if fast_asr_only else "context_text_only_preserve_content",
        })
    except Exception:
        return

    from app.asr.model_manager import get_model_manager
    from app.asr.streaming_pipeline import create_streaming_pipeline

    mm = get_model_manager()
    if not mm.is_initialized():
        await mm.initialize()

    pipe = create_streaming_pipeline(mm, language="zh")
    if fast_asr_only:
        print("[Hybrid] 快速 ASR 模式，跳过声纹和 Qwen 修订 (meeting=%s)" % meeting_id, flush=True)
    else:
        from app.asr.enhanced_engine import EnhancedRecognitionEngine
        from app.asr.model_manager import SpeakerEmbeddingExtractor
        from app.services.speaker_db_service import bytes_to_ndarray, get_speaker_db

        try:
            eng = EnhancedRecognitionEngine(SpeakerEmbeddingExtractor(mm.get_camp_model(), device=mm.device))
            loaded = 0
            for sp in get_speaker_db().load_all(active_only=True):
                emb = bytes_to_ndarray(sp.get("embedding"))
                if emb is not None:
                    eng.register_embedding(sp["speaker_id"], emb, name=sp.get("name"), role=sp.get("role"))
                    try:
                        pipe.register_speaker(sp["speaker_id"], emb, name=sp.get("name"), role=sp.get("role"))
                    except Exception:
                        pass
                    loaded += 1
            pipe.set_enhanced_registry(eng)
            print("[Hybrid] 声纹引擎就绪，注册 %d 人 (meeting=%s)" % (loaded, meeting_id), flush=True)
        except Exception as exc:
            print("[Hybrid] 声纹引擎初始化失败，转写继续: %s" % exc, flush=True)

    qwen_sem = asyncio.Semaphore(1)
    revision_tasks: set[asyncio.Task] = set()
    segments: list[dict] = []
    segments_lock = asyncio.Lock()

    async def revise_with_qwen(base_msg: dict, *, delay: float = _CONTEXT_REVISION_DELAY_SEC):
        segment_id = base_msg["segment_id"]
        if delay > 0:
            await asyncio.sleep(delay)

        async with segments_lock:
            target_index = next((i for i, item in enumerate(segments) if item.get("segment_id") == segment_id), -1)
            if target_index < 0:
                return
            target = dict(segments[target_index])
            original_text = (target.get("original_text") or target.get("text") or "").strip()
            if len(original_text) < _MIN_REVISION_CHARS:
                return
            user_prompt = _build_revision_prompt([dict(item) for item in segments], target_index)

        if not original_text:
            return

        free_mb = await asyncio.to_thread(_gpu_free_mb)
        if free_mb is not None and free_mb < _MIN_FREE_GPU_MB_FOR_QWEN:
            await websocket.send_json({
                "type": "transcript.revised",
                **base_msg,
                "source": "qwen32b",
                "text": original_text,
                "original_text": original_text,
                "qwen_text": "",
                "revision_status": "resource_skipped",
                "revision_error": f"GPU free {free_mb}MB < required {_MIN_FREE_GPU_MB_FOR_QWEN}MB",
                "revision_model": _OLLAMA_MODEL,
                "stable": True,
            })
            async with segments_lock:
                for item in segments:
                    if item.get("segment_id") == segment_id:
                        item["revision_status"] = "resource_skipped"
                        item["stable"] = True
                        break
            return

        try:
            async with qwen_sem:
                qwen_text, raw_payload = await asyncio.wait_for(
                    asyncio.to_thread(_call_qwen32b_revision, user_prompt),
                    timeout=_QWEN_TIMEOUT_SEC,
                )
        except Exception as exc:
            await websocket.send_json({
                "type": "transcript.revised",
                **base_msg,
                "source": "qwen32b",
                "text": original_text,
                "original_text": original_text,
                "revision_status": "timeout_or_error",
                "revision_error": str(exc)[:160],
                "revision_model": _OLLAMA_MODEL,
                "stable": True,
            })
            return

        qwen_text = (qwen_text or "").strip()
        accepted = _accept_revision(original_text, qwen_text)
        final_text = qwen_text if accepted else original_text
        revised_msg = {
            "type": "transcript.revised",
            **base_msg,
            "source": "qwen32b",
            "text": final_text,
            "original_text": original_text,
            "qwen_text": qwen_text,
            "revision_reason": str(raw_payload.get("reason") or "")[:240] if isinstance(raw_payload, dict) else "",
            "revision_model": _OLLAMA_MODEL,
            "revision_status": "enhanced" if accepted else "kept",
            "revision_accepted": accepted,
            "stable": True,
        }
        try:
            await websocket.send_json(revised_msg)
        except Exception:
            return
        async with segments_lock:
            for item in segments:
                if item.get("segment_id") == segment_id:
                    item["text"] = final_text
                    item["qwen_text"] = qwen_text
                    item["revision_status"] = revised_msg["revision_status"]
                    item["revision_accepted"] = accepted
                    item["stable"] = True
                    break
        if accepted and final_text != original_text:
            asyncio.create_task(_persist_revision(segment_id, final_text))

    async def on_transcript(delta):
        text = (getattr(delta, "text", "") or "").strip()
        if not text:
            return

        sid = getattr(delta, "speaker_id", None) or "unknown"
        name = getattr(delta, "speaker_name", None) or sid
        start_ms = int(getattr(delta, "start_ms", 0) or 0)
        end_ms = int(getattr(delta, "end_ms", 0) or 0)
        conf = float(getattr(delta, "speaker_confidence", 1.0) or 1.0)
        segment_id = str(uuid.uuid4())
        identified = bool(name and sid not in ("unknown", "__unknown__", None))

        msg = {
            "type": "transcript.completed",
            "segment_id": segment_id,
            "id": segment_id,
            "source": "funasr",
            "speaker_id": sid,
            "speaker_name": name,
            "speaker_label": name,
            "text": text,
            "start_ms": start_ms,
            "end_ms": end_ms,
            "start_time": start_ms / 1000.0,
            "end_time": end_ms / 1000.0,
            "is_final": True,
            "speaker_confidence": conf,
            "confidence": conf,
            "identified": identified,
            "revision_status": "asr_only" if fast_asr_only else "pending",
            "stable": fast_asr_only,
            "original_text": text,
        }
        try:
            await websocket.send_json(msg)
        except Exception:
            return

        async with segments_lock:
            segments.append(dict(msg))

        if not fast_asr_only:
            asyncio.create_task(_persist_initial(
                line_id=segment_id,
                meeting_id=meeting_id,
                speaker_id=sid,
                speaker_name=name,
                text=text,
                start_ms=start_ms,
                end_ms=end_ms,
                confidence=conf,
            ))

            task = asyncio.create_task(revise_with_qwen(msg))
            revision_tasks.add(task)
            task.add_done_callback(lambda t: revision_tasks.discard(t))

    pipe.on_transcript = on_transcript
    await pipe.start()

    try:
        while True:
            packet = await websocket.receive()

            if "bytes" in packet and packet["bytes"] is not None:
                data = packet["bytes"]
                if data == b"":
                    break
                arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
                if arr.size:
                    await pipe.feed_audio(arr)
                continue

            if "text" in packet and packet["text"] is not None:
                try:
                    msg = json.loads(packet["text"])
                except Exception:
                    continue
                if msg.get("type") == "end_meeting":
                    break
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                continue

    except WebSocketDisconnect:
        print("[Hybrid] 客户端断开 (meeting=%s)" % meeting_id, flush=True)
    except Exception as exc:
        if "disconnect message has been received" in str(exc):
            print("[Hybrid] 客户端断开 (meeting=%s)" % meeting_id, flush=True)
        else:
            print("[Hybrid] 主循环异常: %s" % exc, flush=True)
            traceback.print_exc()
    finally:
        try:
            await pipe.stop()
        except Exception:
            pass
        if revision_tasks and not fast_asr_only:
            try:
                await asyncio.wait(revision_tasks, timeout=_FINAL_REVISION_WAIT_SEC)
            except Exception:
                pass
        try:
            await websocket.send_json({"type": "ready_to_stop"})
        except Exception:
            pass
        print("[Hybrid] 会话清理 (meeting=%s)" % meeting_id, flush=True)
