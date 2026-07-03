"""
FastAPI WebSocket 处理器 — 对齐 InsightEye

WebSocket 消息格式（对齐 InsightEye flat 格式）：
  客户端 → 服务端:
    {"type": "audio_chunk", "source": "mic", "audio": "<base64 PCM 16bit 或 base64 webm/opus>"}
    {"type": "ping"}
    {"type": "end_meeting"}

  服务端 → 客户端:
    {"type": "session.ready", ...}
    {"type": "transcript.completed", ...}
    {"type": "speaker.identified", ...}
    {"type": "meeting_status", "status": "..."}
    {"type": "pong"}
    {"type": "heartbeat", "timestamp": ...}
"""

import asyncio
import base64
import json
import time
import traceback
import uuid
from io import BytesIO
from typing import Optional

import numpy as np
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.asr.model_manager import get_model_manager
from app.config import settings
from app.asr.vad_asr_pipeline import (
    StreamingPipeline,
    create_streaming_pipeline,
    TranscriptDelta,
)
from app.asr.realtime_session import get_store
from app.asr.enhanced_engine import EnhancedRecognitionEngine
from app.services.speaker_db_service import get_speaker_db, bytes_to_ndarray

# 已处理 delta 的去重集合
_processed_deltas: dict[str, set[str]] = {}


def _to_native(obj):
    """将 numpy 类型转换为 Python 原生类型"""
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.ndarray):
        return _to_native(obj.tolist())
    if isinstance(obj, dict):
        return {k: _to_native(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_native(x) for x in obj]
    return obj


async def _send_json(websocket: WebSocket, message: dict) -> bool:
    """发送 JSON"""
    try:
        await websocket.send_text(json.dumps(message, ensure_ascii=False))
        return True
    except Exception:
        return False


class MeetingConnectionManager:
    """管理 WebSocket 连接，支持多客户端连接同一会议"""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}
        # transcript.completed 去重
        self._last_transcript_key: dict[str, str] = {}

    async def connect(self, meeting_id: str, websocket: WebSocket):
        await websocket.accept()
        if meeting_id not in self._connections:
            self._connections[meeting_id] = []
        self._connections[meeting_id].append(websocket)
        print(f"[Server] 会议 {meeting_id} WebSocket 连接已建立，"
              f"当前 {len(self._connections[meeting_id])} 个客户端", flush=True)

    def disconnect(self, meeting_id: str, websocket: WebSocket):
        if meeting_id in self._connections:
            try:
                self._connections[meeting_id].remove(websocket)
            except ValueError:
                pass
            if not self._connections[meeting_id]:
                del self._connections[meeting_id]
                self._last_transcript_key.pop(meeting_id, None)
                _processed_deltas.pop(meeting_id, None)
                print(f"[Server] 会议 {meeting_id} 所有客户端已断开", flush=True)

    async def broadcast(self, meeting_id: str, message: dict):
        """向同一会议的所有客户端广播消息"""
        if meeting_id not in self._connections:
            return

        msg_type = message.get("type", "unknown")
        # transcript.completed 去重
        if msg_type == "transcript.completed":
            key = f"{message.get('speaker_id', '')}|{message.get('text', '')}|{message.get('start_ms', 0)}"
            last_key = self._last_transcript_key.get(meeting_id, "")
            if key == last_key:
                return
            self._last_transcript_key[meeting_id] = key

        dead = []
        for ws in self._connections[meeting_id]:
            try:
                await ws.send_text(json.dumps(message, ensure_ascii=False))
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(meeting_id, ws)


_manager = MeetingConnectionManager()


async def handle_meeting_websocket(websocket: WebSocket, meeting_id: str):
    """WebSocket 处理函数"""
    _connection_closed = False

    await _manager.connect(meeting_id, websocket)
    store = get_store()
    session = store.create(meeting_id)
    pipeline: Optional[StreamingPipeline] = None
    enhanced_engine = None
    total_audio_samples = 0

    try:
        # session.ready
        await _send_json(websocket, {
            "type": "session.ready",
            "session_id": meeting_id,
            "message": "本地流式 FunASR + CAM++ 实时识别已就绪",
            "provider": "local",
            "supports_registration": True,
            "supports_auto_registration": True,
            "supports_multi_speaker": True,
            "streaming": True,
        })
        print(f"[WS发送] → 发送 session.ready, session_id={meeting_id}", flush=True)
        print(f"[Server] 会议 {meeting_id} 开始处理", flush=True)

        model_manager = get_model_manager()

        # 确保模型加载完成
        if not model_manager.is_initialized():
            print(f"[Server] 模型尚未加载，等待 initialize()...", flush=True)
            await model_manager.initialize()
            print(f"[Server] 模型初始化完成", flush=True)

        # 检查各模型状态
        vad_model = model_manager.get_vad_model()
        if vad_model is None:
            print("[Server] 警告: VAD 模型未加载，将使用能量检测", flush=True)
        else:
            print("[Server] Silero VAD 模型就绪", flush=True)

        asr_model = model_manager.get_asr_model()
        if asr_model is None:
            print("[Server] ASR 模型未加载，拒绝连接", flush=True)
            await _send_json(websocket, {
                "type": "error",
                "message": "ASR 模型未加载"
            })
            return

        print(f"[Server] FunASR 模型就绪，设备: {model_manager.device}", flush=True)

        camp_model = model_manager.get_camp_model()
        if camp_model is None:
            print("[Server] 警告: CAM++ 模型未加载，说话人识别将使用基础模式", flush=True)
        else:
            print("[Server] CAM++ 声纹模型就绪", flush=True)

        # 追踪所有 pending 的 transcript 任务
        _pending_transcript_tasks: list[asyncio.Task] = []

        # 加载已注册的说话人到增强引擎
        speaker_db = get_speaker_db()
        db_speakers = speaker_db.load_all(active_only=True)
        print(f"[Server] 从 DB 加载 {len(db_speakers)} 位说话人", flush=True)

        if camp_model is not None:
            from app.asr.model_manager import SpeakerEmbeddingExtractor
            extractor = SpeakerEmbeddingExtractor(camp_model, device=model_manager.device)
            enhanced_engine = EnhancedRecognitionEngine(extractor)
            print(f"[Server] 增强版声纹引擎已创建", flush=True)

            loaded = 0
            for sp in db_speakers:
                raw_emb = sp.get("embedding")
                emb = bytes_to_ndarray(raw_emb) if raw_emb is not None else None
                if emb is None:
                    if isinstance(raw_emb, np.ndarray):
                        emb = raw_emb
                    elif isinstance(raw_emb, (bytes, bytearray)):
                        emb = np.frombuffer(raw_emb, dtype=np.float32)
                if emb is not None:
                    enhanced_engine.register_embedding(
                        sp["speaker_id"],
                        emb,
                        name=sp.get("name"),
                        role=sp.get("role"),
                    )
                    loaded += 1
            print(f"[Server] 增强引擎已注册 {loaded} 位说话人", flush=True)

        # 创建流式管道
        pipeline = create_streaming_pipeline(model_manager, language="zh")

        # 注册说话人到管道
        registered_count = 0
        for sp in db_speakers:
            emb_raw = sp.get("embedding")
            if emb_raw is not None:
                if isinstance(emb_raw, (bytes, bytearray)):
                    emb = np.frombuffer(emb_raw, dtype=np.float32)
                elif isinstance(emb_raw, np.ndarray):
                    emb = emb_raw
                else:
                    continue
                pipeline.register_speaker(
                    sp["speaker_id"],
                    emb,
                    name=sp.get("name"),
                    role=sp.get("role"),
                )
                registered_count += 1

        if enhanced_engine is not None:
            pipeline.set_enhanced_registry(enhanced_engine)
            print(f"[Server] 管道已接入增强版声纹引擎", flush=True)

        # 配置多窗口投票
        pipeline.set_multi_window(enabled=False, n_windows=3, window_step_ratio=0.25)

        print(f"[Server] 声纹注册完成，共 {registered_count} 位说话人", flush=True)

        # ── on_transcript 回调 ───────────────────────────────────────
        async def _do_transcript(delta: TranscriptDelta):
            # 去重：基于 (speaker_id, text, start_ms) 防止同一 delta 被多次处理
            delta_key = f"{delta.speaker_id}|{delta.text}|{delta.start_ms}"
            if meeting_id not in _processed_deltas:
                _processed_deltas[meeting_id] = set()
            if delta_key in _processed_deltas[meeting_id]:
                return
            _processed_deltas[meeting_id].add(delta_key)
            # 清理旧记录（保留最近 1000 条）
            if len(_processed_deltas[meeting_id]) > 1000:
                oldest = next(iter(_processed_deltas[meeting_id]))
                _processed_deltas[meeting_id].discard(oldest)

            raw_speaker = delta.speaker_id or "unknown"
            speaker_display = raw_speaker
            if getattr(delta, 'speaker_name', None):
                speaker_display = delta.speaker_name
            elif raw_speaker == "interviewer":
                speaker_display = "面试官"
            elif raw_speaker == "candidate":
                speaker_display = "候选人"
            elif raw_speaker == "speaker_unk" or raw_speaker == "unknown":
                speaker_display = "未知"

            print(f"[转录] 【{speaker_display}】 {delta.text[:50]}", flush=True)

            msg_type = "transcript.completed" if delta.is_final else "transcript.delta"
            candidates_raw = getattr(delta, 'speaker_candidates', None)
            registered_sims_raw = getattr(delta, 'registered_speaker_sims', None)
            was_corrected = getattr(delta, 'was_corrected', False)
            corrected_text = getattr(delta, 'corrected_text', None)
            correction_errors = getattr(delta, 'correction_errors', None)
            uncertain_speaker = getattr(delta, 'uncertain_speaker', False)
            uncertain_reason = getattr(delta, 'speaker_uncertain_reason', None)
            multi_window_stats = getattr(delta, 'speaker_multi_window_stats', None)
            recognized_role = getattr(delta, 'recognized_role', None)

            event = {
                "type": msg_type,
                "source": "system",
                "speaker_id": raw_speaker,
                "text": delta.text,
                "is_final": delta.is_final,
                "start_ms": delta.start_ms,
                "end_ms": delta.end_ms,
                "speaker_confidence": float(delta.speaker_confidence),
                "segment_reason": delta.segment_reason,
                "interviewer_sim": float(delta.interviewer_sim),
                "candidate_sim": float(delta.candidate_sim),
                "recognized_role": recognized_role,
                "speaker_name": getattr(delta, 'speaker_name', None),
                "speaker_candidates": _to_native(candidates_raw),
                "registered_speaker_sims": _to_native(registered_sims_raw),
                "was_corrected": was_corrected,
                "corrected_text": corrected_text,
                "correction_errors": _to_native(correction_errors) if correction_errors else None,
                "uncertain_speaker": uncertain_speaker,
                "uncertain_reason": uncertain_reason,
                "speaker_multi_window_stats": _to_native(multi_window_stats) if multi_window_stats else None,
            }

            # 发送到 WebSocket（广播给所有客户端）
            await _manager.broadcast(meeting_id, event)

            # 持久化到数据库
            try:
                from app.database import async_session as _async_session
                from app.models.transcript import TranscriptLine
                from datetime import datetime as _dt

                async with _async_session() as db:
                    db_line = TranscriptLine(
                        id=str(uuid.uuid4()),
                        meeting_id=meeting_id,
                        speaker_id=raw_speaker,
                        speaker_label=raw_speaker,
                        text=delta.text,
                        start_time=delta.start_ms / 1000.0 if delta.start_ms else 0,
                        end_time=delta.end_ms / 1000.0 if delta.end_ms else 0,
                        confidence=float(delta.speaker_confidence),
                        created_at=_dt.utcnow(),
                    )
                    db.add(db_line)
                    await db.commit()
            except Exception as db_err:
                print(f"[Server] 转写持久化失败: {db_err}", flush=True)

        def on_transcript(delta: TranscriptDelta):
            """管道回调函数，同步调用异步处理"""
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            task = loop.create_task(_do_transcript(delta))
            _pending_transcript_tasks.append(task)
            task.add_done_callback(
                lambda t: _pending_transcript_tasks.remove(t) if t in _pending_transcript_tasks else None
            )

        pipeline.on_transcript = on_transcript

        # ── on_speaker 回调 ──────────────────────────────────────────
        async def _do_speaker(speaker_id: str, confidence: float):
            print(f"[WS发送] → 发送 speaker.identified: source=system, "
                  f"speaker_id={speaker_id}, confidence={confidence:.3f}", flush=True)
            await _manager.broadcast(meeting_id, {
                "type": "speaker.identified",
                "source": "system",
                "speaker_id": speaker_id,
                "confidence": float(confidence),
            })

        def on_speaker(speaker_id: str, confidence: float):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()
            loop.create_task(_do_speaker(speaker_id, confidence))

        pipeline.on_speaker = on_speaker

        # 启动管道
        print(f"[Server] 启动流式管道...", flush=True)
        await pipeline.start()
        store.set_pipeline(meeting_id, pipeline)
        print(f"[Server] 流式管道已启动，开始接收音频", flush=True)

        # 主循环：接收音频
        chunk_count = 0
        first_audio_logged = False

        async def _wait_for_message(timeout: float = 30.0):
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
                return raw
            except asyncio.TimeoutError:
                return None

        while True:
            raw = await _wait_for_message(30.0)

            if raw is None:
                try:
                    heartbeat = {"type": "heartbeat", "timestamp": time.time()}
                    await websocket.send_text(json.dumps(heartbeat))
                except Exception:
                    break
                continue

            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as je:
                print(f"[Server] 收到无效 JSON: {je}", flush=True)
                await _send_json(websocket, {
                    "type": "error",
                    "message": f"Invalid JSON: {str(je)}"
                })
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await _send_json(websocket, {"type": "pong"})
                continue

            if msg_type == "quality_mode":
                enabled = msg.get("enabled", False)
                if pipeline:
                    pipeline.set_quality_mode(enabled)
                print(f"[Server] 质量优先模式={'开启' if enabled else '关闭'}", flush=True)
                continue

            if msg_type == "end_meeting":
                print(f"[Server] 收到 end_meeting，结束会议", flush=True)
                store.end(meeting_id)
                if pipeline:
                    await pipeline.stop()

                try:
                    from app.database import async_session as _async_session
                    from app.models.transcript import TranscriptLine

                    async with _async_session() as db:
                        result = await db.execute(
                            select(TranscriptLine)
                            .where(TranscriptLine.meeting_id == meeting_id)
                            .order_by(TranscriptLine.start_time)
                        )
                        transcript_lines = [
                            {"id": line.id, "speaker": line.speaker_label, "speaker_id": line.speaker_id,
                             "text": line.text, "start": line.start_time, "end": line.end_time,
                             "confidence": line.confidence}
                            for line in result.scalars().all()
                        ]
                    if transcript_lines:
                        from app.workers.summary_tasks import submit_final_summary
                        submit_final_summary(meeting_id, transcript_lines, [])
                        print(f"[Server] 已提交最终总结任务", flush=True)
                except Exception as summary_err:
                    print(f"[Server] 触发总结失败: {summary_err}", flush=True)

                await _manager.broadcast(meeting_id, {
                    "type": "meeting_status",
                    "status": "ended"
                })
                break

            if msg_type == "audio_chunk":
                audio_b64 = msg.get("audio", "")
                source_name = msg.get("source", "system")
                try:
                    audio_bytes = base64.b64decode(audio_b64)

                    arr = None

                    # 方式 1：原始 PCM（2 字节对齐且有实际数据）
                    if len(audio_bytes) % 2 == 0:
                        try:
                            pcm = np.frombuffer(audio_bytes, dtype=np.int16)
                            if pcm.size > 0 and pcm.std() > 1:
                                arr = pcm.astype(np.float32) / 32768.0
                        except Exception:
                            pass

                    # 方式 2：容器格式（WebM / OGG）—— ffmpeg 解码（最可靠）
                    if arr is None:
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                'ffmpeg', '-loglevel', 'error',
                                '-f', 'matroska',  # WebM = 扩展名是 .webm 但容器是 matroska
                                '-i', 'pipe:0',
                                '-f', 's16le',
                                '-ar', '16000',
                                '-ac', '1',
                                'pipe:1',
                                stdin=asyncio.subprocess.PIPE,
                                stdout=asyncio.subprocess.PIPE,
                                stderr=asyncio.subprocess.PIPE,
                            )
                            stdout, _ = await asyncio.wait_for(proc.communicate(input=audio_bytes), timeout=5)
                            if proc.returncode == 0 and len(stdout) >= 2:
                                pcm_data = np.frombuffer(stdout, dtype=np.int16).astype(np.float32) / 32768.0
                                if pcm_data.size > 0:
                                    arr = pcm_data
                                    print(f"[Server] ffmpeg 解码成功: {len(audio_bytes)} bytes → {arr.size} 样本", flush=True)
                            elif proc.returncode != 0:
                                # matroska 失败，尝试 ogg 容器
                                try:
                                    proc2 = await asyncio.create_subprocess_exec(
                                        'ffmpeg', '-loglevel', 'error',
                                        '-f', 'ogg',
                                        '-i', 'pipe:0',
                                        '-f', 's16le',
                                        '-ar', '16000',
                                        '-ac', '1',
                                        'pipe:1',
                                        stdin=asyncio.subprocess.PIPE,
                                        stdout=asyncio.subprocess.PIPE,
                                        stderr=asyncio.subprocess.PIPE,
                                    )
                                    stdout2, _ = await asyncio.wait_for(proc2.communicate(input=audio_bytes), timeout=5)
                                    if proc2.returncode == 0 and len(stdout2) >= 2:
                                        pcm_data = np.frombuffer(stdout2, dtype=np.int16).astype(np.float32) / 32768.0
                                        if pcm_data.size > 0:
                                            arr = pcm_data
                                            print(f"[Server] ffmpeg (ogg) 解码成功: {len(audio_bytes)} bytes → {arr.size} 样本", flush=True)
                                except Exception:
                                    pass
                        except Exception as ffmpeg_err:
                            print(f"[Server] ffmpeg 解码失败: {ffmpeg_err}", flush=True)

                    if arr is None:
                        print(f"[Server] 无法解码音频块（{len(audio_bytes)} bytes），跳过", flush=True)
                        continue

                    total_audio_samples += arr.size
                    if not first_audio_logged:
                        audio_duration = arr.size / 16000
                        is_init = model_manager.is_initialized()
                        vad_ready = model_manager.get_vad_model() is not None
                        print(f"[Server] 收到音频: source={source_name}, 时长={audio_duration:.2f}s, "
                              f"样本数={arr.size}, 模型已初始化={is_init}, VAD模型={'有' if vad_ready else '无'}", flush=True)
                        first_audio_logged = True

                    await pipeline.feed_audio(arr)
                    chunk_count += 1

                except Exception as e:
                    print(f"[Server] 音频解码失败: {e}", flush=True)
                    traceback.print_exc()
                    continue

    except WebSocketDisconnect:
        print(f"[Server] WebSocket 断开 (meeting_id={meeting_id})", flush=True)
    except Exception as e:
        print(f"[Server] 会议 {meeting_id} 处理异常: {e}", flush=True)
        traceback.print_exc()
    finally:
        _connection_closed = True
        _manager.disconnect(meeting_id, websocket)
        _manager._last_transcript_key.pop(meeting_id, None)
        _processed_deltas.pop(meeting_id, None)
        if pipeline:
            try:
                await pipeline.stop()
                stats = pipeline.get_segment_stats()
                print(f"[Server] 管道已停止，分割统计: {stats}", flush=True)
                print(f"[Server] 总处理样本数: {total_audio_samples}", flush=True)
            except Exception as e:
                print(f"[Server] 管道停止失败: {e}", flush=True)
        # 等待所有 pending transcript 任务完成
        if _pending_transcript_tasks:
            pending = list(_pending_transcript_tasks)
            print(f"[Server] 等待 {len(pending)} 个 transcript 任务完成...", flush=True)
            for task in pending:
                try:
                    await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
                except asyncio.TimeoutError:
                    print(f"[Server] 任务超时，取消: {task}", flush=True)
                    task.cancel()
                except Exception as e:
                    print(f"[Server] 任务异常: {e}", flush=True)

        store.close(meeting_id)
        print(f"[Server] 会议 {meeting_id} 会话已清理", flush=True)


router = APIRouter()


@router.websocket("/ws/meeting/{meeting_id}")
async def websocket_endpoint(websocket: WebSocket, meeting_id: str):
    await handle_meeting_websocket(websocket, meeting_id)
