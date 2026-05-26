"""
FastAPI WebSocket 处理器 — 对齐 D:\InsightEye\app\realtime_ws_server.py

日志前缀（对齐 InsightEye）：
- [Server] — 会话建立、模型初始化、消息处理
- [WS发送] — 每条出站 WebSocket 消息
- [转录]  — ASR 识别结果
- [识别]  — 说话人识别结果
- [合并]  — 片段合并决策
- [缓冲]  — 缓冲区等待状态
- [去噪]  — 音频去噪状态

WebSocket 消息格式（对齐 InsightEye flat 格式）：
  客户端 → 服务端:
    {"type": "audio_chunk", "source": "mic", "audio": "<base64 PCM 16bit>"}
    {"type": "ping"}
    {"type": "end_meeting"}

  服务端 → 客户端:
    {"type": "session.ready", ...}
    {"type": "transcript.delta", "source": "...", "speaker_id": "...", "text": "...", ...}
    {"type": "transcript.completed", ...}
    {"type": "speaker.identified", "source": "...", "speaker_id": "...", "confidence": ...}
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
import sys
from typing import Optional

import numpy as np
from fastapi import WebSocket, WebSocketDisconnect
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

# 已处理 delta 的去重集合（基于 delta 哈希，防止同一 delta 被多次处理）
_processed_deltas: dict[str, set[str]] = {}  # meeting_id -> set of delta_key


def _to_native(obj):
    """将 numpy 类型转换为 Python 原生类型（对齐 InsightEye）"""
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
    """发送 JSON（对齐 InsightEye 风格）"""
    try:
        await websocket.send_text(json.dumps(message, ensure_ascii=False))
        return True
    except Exception:
        return False


class MeetingConnectionManager:
    """管理 WebSocket 连接，支持多客户端连接同一会议（对齐 InsightEye AudioSource 管理）"""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = {}
        # transcript.completed 去重：记录最近发送的消息摘要，避免同一条消息重复广播
        self._last_transcript_key: dict[str, str] = {}  # meeting_id -> content_hash

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
        # transcript.completed 去重：基于 (speaker_id, text, start_ms) 哈希去重
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
                print(f"[WS发送] → 广播 {msg_type}", flush=True)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(meeting_id, ws)


_manager = MeetingConnectionManager()


async def handle_meeting_websocket(websocket: WebSocket, meeting_id: str):
    """
    WebSocket 处理函数（对齐 InsightEye realtime_ws_server.py LocalRealtimeServer._handle_client）

    对齐要点：
    1. session.ready 消息包含 supports_multi_speaker / streaming 等字段
    2. 模型初始化在连接时同步完成
    3. 从 DB 加载已注册的说话人到增强引擎
    4. transcript.delta / transcript.completed 消息包含所有新字段
    5. 纠错字段 (was_corrected / corrected_text / correction_errors)
    6. uncertain 字段 (uncertain_speaker / speaker_multi_window_stats)
    7. 多客户端广播支持
    8. 定时阶段总结任务
    """
    _connection_closed = False

    await _manager.connect(meeting_id, websocket)
    store = get_store()
    session = store.create(meeting_id)
    pipeline: Optional[StreamingPipeline] = None
    enhanced_engine: Optional[EnhancedRecognitionEngine] = None
    total_audio_samples = 0

    try:
        # session.ready（对齐 InsightEye flat 格式）
        await websocket.send_text(json.dumps({
            "type": "session.ready",
            "session_id": meeting_id,
            "message": "本地流式 FunASR + CAM++ 实时识别已就绪",
            "provider": "local",
            "supports_registration": True,
            "supports_auto_registration": True,
            "supports_multi_speaker": True,
            "streaming": True,
        }, ensure_ascii=False))
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
            await websocket.send_text(json.dumps({
                "type": "error",
                "message": "ASR 模型未加载"
            }, ensure_ascii=False))
            return

        print(f"[Server] FunASR 模型就绪，设备: {model_manager.device}", flush=True)

        camp_model = model_manager.get_camp_model()
        if camp_model is None:
            print("[Server] 警告: CAM++ 模型未加载，说话人识别将使用基础模式", flush=True)
        else:
            print("[Server] CAM++ 声纹模型就绪", flush=True)

        # 追踪所有 pending 的 transcript 任务，确保连接关闭前全部完成
        _pending_transcript_tasks: list[asyncio.Task] = []

        # 加载已注册的说话人到增强引擎（对齐 InsightEye 模式二）
        speaker_db = get_speaker_db()
        db_speakers = speaker_db.load_all(active_only=True)
        print(f"[Server] 从 DB 加载 {len(db_speakers)} 位说话人", flush=True)

        if camp_model is not None:
            from app.asr.model_manager import SpeakerEmbeddingExtractor
            extractor = SpeakerEmbeddingExtractor(camp_model, device=model_manager.device)
            enhanced_engine = EnhancedRecognitionEngine(extractor)
            print(f"[Server] 增强版声纹引擎已创建", flush=True)

            # 从 DB 注入声纹（对齐 InsightEye _handle_client 模式二）
            # bytes_to_ndarray 是模块级函数，直接使用
            loaded = 0
            for sp in db_speakers:
                raw_emb = sp.get("embedding")
                emb = bytes_to_ndarray(raw_emb) if raw_emb is not None else None
                if emb is None:
                    # 备选：直接处理 bytes/bytearray/np.ndarray
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

        # 创建流式管道（对齐 InsightEye）
        pipeline = create_streaming_pipeline(model_manager, language="zh")

        # 注册说话人到管道（np 已在模块顶部导入）
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

        # 配置多窗口投票（对齐 InsightEye 默认参数）
        pipeline.set_multi_window(enabled=True, n_windows=3, window_step_ratio=0.25)

        print(f"[Server] 声纹注册完成，共 {registered_count} 位说话人", flush=True)

        # ── on_transcript 回调（对齐 InsightEye _on_transcript_delta） ──────────
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
            # 姓名优先级：delta.speaker_name > role名称 > speaker_id
            speaker_display = raw_speaker
            if getattr(delta, 'speaker_name', None):
                speaker_display = delta.speaker_name
            elif raw_speaker == "interviewer":
                speaker_display = "面试官"
            elif raw_speaker == "candidate":
                speaker_display = "候选人"
            elif raw_speaker == "speaker_unk" or raw_speaker == "unknown":
                speaker_display = "未知"

            # 相似度日志（对齐 InsightEye）
            sim_parts = []
            if delta.interviewer_sim > 0:
                sim_parts.append(f"面试官={delta.interviewer_sim:.2f}")
            if delta.candidate_sim > 0:
                sim_parts.append(f"候选人={delta.candidate_sim:.2f}")
            if delta.registered_speaker_sims:
                for spk_id, sim in delta.registered_speaker_sims.items():
                    if sim > 0:
                        sim_parts.append(f"{spk_id}={sim:.2f}")
            confidence_str = f"  ({', '.join(sim_parts)})" if sim_parts else ""

            # uncertain 状态
            uncertain_str = ""
            if getattr(delta, 'uncertain_speaker', False):
                uncertain_str = f" ⚠️[uncertain]"

            reason_str = f" [{delta.segment_reason}]" if delta.segment_reason else ""

            # 纠错状态（对齐 InsightEye）
            corrected_str = ""
            if getattr(delta, 'was_corrected', False) and getattr(delta, 'corrected_text', None):
                corrected_str = f" [纠: {getattr(delta, 'corrected_text', '')[:20]}...]"

            print(f"[转录] 【{speaker_display}{uncertain_str}{confidence_str}{reason_str}{corrected_str}】 "
                  f"{delta.text[:50]}{'...' if len(delta.text) > 50 else ''}", flush=True)

            msg_type = "transcript.completed" if delta.is_final else "transcript.delta"
            candidates_raw = getattr(delta, 'speaker_candidates', None)
            registered_sims_raw = getattr(delta, 'registered_speaker_sims', None)

            # 纠错字段（对齐 InsightEye）
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
                # 纠错字段（对齐 InsightEye）
                "was_corrected": was_corrected,
                "corrected_text": corrected_text,
                "correction_errors": _to_native(correction_errors) if correction_errors else None,
                # uncertain 字段
                "uncertain_speaker": uncertain_speaker,
                "uncertain_reason": uncertain_reason,
                "speaker_multi_window_stats": _to_native(multi_window_stats) if multi_window_stats else None,
            }

            print(f"[WS发送] → 发送 {msg_type}: speaker_id={raw_speaker}, "
                  f"uncertain={uncertain_speaker}, "
                  f"corrected={was_corrected}, "
                  f"interviewer_sim={event['interviewer_sim']}, "
                  f"candidate_sim={event['candidate_sim']}, "
                  f"is_final={event['is_final']}, "
                  f"text={delta.text[:30]}", flush=True)

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
            """管道回调函数，同步调用异步处理（对齐 InsightEye）"""
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

        # ── on_speaker 回调（对齐 InsightEye _on_speaker_identified） ─────────
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

        # 定时阶段总结任务（每 N 分钟，间隔可配置，默认 60 秒）
        last_summary_time = 0.0  # 初始值 0，保证首次必定触发
        period_interval = settings.PERIOD_SUMMARY_INTERVAL_SECONDS

        async def try_period_summary():
            nonlocal last_summary_time
            current_time = time.time()

            # 时间间隔检查：每次触发都要等够 period_interval 秒
            # 注意：last_summary_time 在成功生成总结后才更新
            if current_time - last_summary_time < period_interval:
                return

            from app.database import async_session as _async_session
            from app.models.transcript import TranscriptLine

            async with _async_session() as db:
                result = await db.execute(
                    select(TranscriptLine)
                    .where(TranscriptLine.meeting_id == meeting_id)
                    .order_by(TranscriptLine.start_time.desc())
                    .limit(50)
                )
                recent_lines = [
                    {"id": line.id, "speaker": line.speaker_label, "speaker_id": line.speaker_id,
                     "text": line.text, "start": line.start_time, "end": line.end_time,
                     "confidence": line.confidence}
                    for line in reversed(list(result.scalars().all()))
                ]

            if not recent_lines:
                return

            print(f"[Server] 已触发阶段总结，转写条数: {len(recent_lines)}", flush=True)
            if recent_lines:
                print(f"[Server] 最近转写时间范围: {recent_lines[0]['start']:.2f}s ~ {recent_lines[-1]['start']:.2f}s", flush=True)

            try:
                from app.services.summary_service import SummaryService
                from datetime import datetime as _dt

                service = SummaryService()

                def _call_llm():
                    """同步调用 subprocess，供 to_thread 使用"""
                    return service._call_meetingsummary_from_lines(recent_lines, prefix=f"period_{meeting_id[:8]}")

                llm_result = await asyncio.to_thread(_call_llm)
                bullet_points = llm_result.get("bullet_points", []) if llm_result else []

                # 计算时间范围
                if recent_lines:
                    period_start = min((l.get("start", 0) for l in recent_lines), default=0)
                    period_end = max((l.get("end", 0) for l in recent_lines), default=0)
                    print(f"[Server] 阶段总结时间: period_start={period_start:.3f}s, period_end={period_end:.3f}s", flush=True)
                    print(f"[Server] 最近3条: {[(l['start'], l['end'], l['text'][:20]) for l in recent_lines[-3:]]}", flush=True)

                summary_id = str(uuid.uuid4())

                async with _async_session() as db2:
                    from app.models.summary import PeriodSummary
                    summary = PeriodSummary(
                        id=summary_id,
                        meeting_id=meeting_id,
                        period_start=period_start,
                        period_end=period_end,
                        bullet_points=bullet_points,
                        generated_at=_dt.utcnow(),
                    )
                    db2.add(summary)
                    await db2.commit()

                await _manager.broadcast(meeting_id, {
                    "type": "period_summary",
                    "data": {
                        "id": summary_id,
                        "meeting_id": meeting_id,
                        "period_start": period_start,
                        "period_end": period_end,
                        "bullet_points": bullet_points,
                        "generated_at": _dt.utcnow().isoformat(),
                    }
                })
                print(f"[Server] 阶段总结已推送，bullet_points={len(bullet_points)}", flush=True)
                last_summary_time = time.time()
            except Exception as e:
                print(f"[Server] 触发阶段总结失败: {e}", flush=True)
                import traceback as _tb
                print(f"[Server] 阶段总结异常详情: {_tb.format_exc()}", flush=True)

        # 主循环：接收音频 + 定时阶段总结
        chunk_count = 0
        first_audio_logged = False

        async def _wait_for_message(timeout: float = 30.0):
            """等待并返回下一条消息，超时返回 None"""
            try:
                raw = await asyncio.wait_for(websocket.receive_text(), timeout=timeout)
                return raw
            except asyncio.TimeoutError:
                return None

        async def _period_summary_loop():
            """定时触发阶段总结的后台任务（每 30 秒检查一次是否该生成）"""
            while not _connection_closed:
                await asyncio.sleep(30)
                if not _connection_closed and chunk_count > 0:
                    await try_period_summary()

        period_task = asyncio.create_task(_period_summary_loop())

        while True:
            raw = await _wait_for_message(30.0)

            if raw is None:
                # 超时：发送心跳
                try:
                    heartbeat = {"type": "heartbeat", "timestamp": time.time()}
                    await websocket.send_text(json.dumps(heartbeat))
                except Exception:
                    break
                continue

            # JSON 错误处理
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError as je:
                print(f"[Server] 收到无效 JSON: {je}", flush=True)
                await websocket.send_text(json.dumps({
                    "type": "error",
                    "message": f"Invalid JSON: {str(je)}"
                }, ensure_ascii=False))
                continue

            msg_type = msg.get("type")

            if msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))
                continue

            if msg_type == "end_meeting":
                print(f"[Server] 收到 end_meeting，结束会议", flush=True)
                store.end(meeting_id)
                if pipeline:
                    await pipeline.stop()

                # 触发会议总结（对齐 InsightEye benchmark.evaluate 流程）
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
                        from app.workers.summary_tasks import final_summary_task
                        final_summary_task.delay(meeting_id, transcript_lines, [])
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
                    arr = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
                    total_audio_samples += arr.size
                    if arr.size == 0:
                        continue

                    # 首次音频接收日志（对齐 InsightEye）
                    if not first_audio_logged:
                        audio_duration = arr.size / 16000
                        is_init = model_manager.is_initialized()
                        vad_ready = model_manager.get_vad_model() is not None
                        print(f"[Server] 收到音频: source={source_name}, 时长={audio_duration:.2f}s, "
                              f"模型已初始化={is_init}, VAD模型={'有' if vad_ready else '无'}", flush=True)
                        first_audio_logged = True

                    await pipeline.feed_audio(arr)
                    chunk_count += 1

                except Exception as e:
                    print(f"[Server] 音频解码失败: {e}", flush=True)
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
                # 打印分割统计（对齐 InsightEye）
                stats = pipeline.get_segment_stats()
                print(f"[Server] 管道已停止，分割统计: {stats}", flush=True)
                print(f"[Server] 总处理样本数: {total_audio_samples}", flush=True)
            except Exception as e:
                print(f"[Server] 管道停止失败: {e}", flush=True)
        # 等待所有 pending transcript 任务完成（避免会议结束时数据丢失）
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

        # 取消定时阶段总结任务
        if not period_task.done():
            period_task.cancel()
            try:
                await period_task
            except asyncio.CancelledError:
                pass

        store.close(meeting_id)
        print(f"[Server] 会议 {meeting_id} 会话已清理", flush=True)
