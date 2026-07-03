import uuid
import json
import tempfile
import os
import concurrent.futures
from io import BytesIO
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.meeting import Meeting
from app.models.summary import FinalSummary
from app.models.transcript import TranscriptLine
from app.schemas.meeting import MeetingCreate, MeetingUpdate, MeetingResponse, MeetingListResponse
from app.workers.summary_tasks import submit_final_summary

router = APIRouter()

UPLOAD_DIR = Path(__file__).parent.parent / "audio_files"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# 独立线程池执行 CPU/GPU 密集任务，避免阻塞 FastAPI 事件循环
_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2, thread_name_prefix="audio-worker")


def _run_bg(meeting_id: str, file_path: str):
    """在新线程中运行异步后台任务 — 创建独立的 asyncio 事件循环"""
    import asyncio
    import traceback
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(process_audio_in_background(meeting_id, file_path))
        finally:
            loop.close()
    except Exception as e:
        print(f"[Background] 处理失败: {e}", flush=True)
        traceback.print_exc()


# ==================== 会议 CRUD ====================

@router.post("", response_model=MeetingResponse, status_code=201)
async def create_meeting(data: MeetingCreate, db: AsyncSession = Depends(get_db)):
    meeting = Meeting(
        id=str(uuid.uuid4()),
        title=data.title,
        description=data.description,
        participants=json.dumps(data.participants, ensure_ascii=False),
        status="created",
        mode=data.mode or "realtime",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(meeting)
    await db.flush()
    await db.refresh(meeting)
    return _to_response(meeting)


@router.get("", response_model=MeetingListResponse)
async def list_meetings(page: int = 1, size: int = 20, db: AsyncSession = Depends(get_db)):
    total_q = await db.execute(select(func.count(Meeting.id)))
    total = total_q.scalar() or 0
    result = await db.execute(
        select(Meeting).order_by(Meeting.created_at.desc()).offset((page - 1) * size).limit(size)
    )
    items = [_to_response(m) for m in result.scalars().all()]
    return MeetingListResponse(items=items, total=total, page=page, size=size)


@router.get("/{meeting_id}", response_model=MeetingResponse)
async def get_meeting(meeting_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return _to_response(meeting)


@router.patch("/{meeting_id}", response_model=MeetingResponse)
async def update_meeting(meeting_id: str, data: MeetingUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    if data.title is not None:
        meeting.title = data.title
    if data.description is not None:
        meeting.description = data.description
    if data.status is not None:
        meeting.status = data.status
    if data.participants is not None:
        meeting.participants = json.dumps(data.participants, ensure_ascii=False)
    if data.mode is not None:
        meeting.mode = data.mode
    meeting.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(meeting)
    return _to_response(meeting)


@router.delete("/{meeting_id}", status_code=204)
async def delete_meeting(meeting_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    await db.delete(meeting)


# ==================== 离线处理相关 ====================

@router.post("/{meeting_id}/upload")
async def upload_audio(
    meeting_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """上传音频文件并触发离线处理（VibeVoice-ASR + CAM++）"""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    if meeting.status == "processing":
        raise HTTPException(status_code=409, detail="此会议正在处理中，请稍后再试")

    allowed_extensions = ['.wav', '.mp3', '.m4a', '.ogg', '.webm', '.flac']
    file_ext = Path(file.filename).suffix.lower() if file.filename else '.wav'
    if file_ext not in allowed_extensions:
        raise HTTPException(status_code=400, detail=f"不支持的文件格式: {file_ext}")

    file_path = UPLOAD_DIR / f"{meeting_id}_{uuid.uuid4()}{file_ext}"
    try:
        content = await file.read()
        with open(file_path, 'wb') as f:
            f.write(content)
        file_size = len(content)
        print(f"[API] 音频文件已保存: {file_path}, 大小: {file_size / 1024 / 1024:.2f} MB")

        meeting.status = "processing"
        meeting.updated_at = datetime.utcnow()
        await db.flush()

        # 用独立线程执行，事件循环不被阻塞
        _executor.submit(_run_bg, meeting_id, str(file_path))

        return {
            "status": "processing",
            "message": "音频文件上传成功，正在后台处理（VibeVoice-ASR + CAM++）",
            "file_path": str(file_path),
            "file_size": file_size,
            "meeting_id": meeting_id,
            "mode": "offline",
        }
    except Exception as e:
        if file_path.exists():
            file_path.unlink()
        raise HTTPException(status_code=500, detail=f"文件上传失败: {str(e)}")


@router.get("/{meeting_id}/status")
async def get_processing_status(meeting_id: str, db: AsyncSession = Depends(get_db)):
    """获取离线处理状态"""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {
        "meeting_id": meeting_id,
        "status": meeting.status,
        "title": meeting.title,
        "updated_at": meeting.updated_at.isoformat() if meeting.updated_at else None
    }


@router.post("/{meeting_id}/process")
async def trigger_processing(meeting_id: str, db: AsyncSession = Depends(get_db)):
    """手动触发离线处理"""
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    meeting.status = "processing"
    meeting.updated_at = datetime.utcnow()
    await db.flush()
    return {"status": "processing", "message": "处理已触发", "meeting_id": meeting_id}


# ==================== 转写查询 ====================

@router.get("/{meeting_id}/transcripts")
async def get_transcripts(
    meeting_id: str,
    limit: int = 1000,
    offset: int = 0,
    db: AsyncSession = Depends(get_db)
):
    """获取会议转写结果"""
    from app.models.transcript import TranscriptLine

    result = await db.execute(
        select(TranscriptLine)
        .where(TranscriptLine.meeting_id == meeting_id)
        .order_by(TranscriptLine.start_time)
        .offset(offset)
        .limit(limit)
    )
    items = [_transcript_to_dict(t) for t in result.scalars().all()]
    return {"items": items, "total": len(items)}


@router.get("/{meeting_id}/downloads/transcript")
async def download_transcript(meeting_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    transcript_result = await db.execute(
        select(TranscriptLine)
        .where(TranscriptLine.meeting_id == meeting_id)
        .order_by(TranscriptLine.start_time)
    )
    transcript_lines = transcript_result.scalars().all()
    if not transcript_lines:
        raise HTTPException(status_code=404, detail="暂无转写可下载")

    lines = [f"# {meeting.title} - 转写文档", ""]
    if meeting.description:
        lines.extend([f"说明：{meeting.description}", ""])
    lines.extend([
        f"会议ID：{meeting.id}",
        f"状态：{meeting.status}",
        f"创建时间：{meeting.created_at.isoformat() if meeting.created_at else 'N/A'}",
        "",
        "## 转写内容",
        "",
    ])
    for line in transcript_lines:
        start = f"{line.start_time:.2f}s" if line.start_time is not None else "N/A"
        end = f"{line.end_time:.2f}s" if line.end_time is not None else "N/A"
        speaker = line.speaker_label or line.speaker_id or "未知"
        lines.append(f"- [{start} - {end}] {speaker}：{line.text}")

    content = "\n".join(lines)
    filename = f"{meeting.title or meeting_id}-transcript.md".replace("/", "-")
    return StreamingResponse(
        BytesIO(content.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/{meeting_id}/downloads/summary")
async def download_summary(meeting_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    fs = await _ensure_final_summary(db, meeting_id)
    if not fs:
        raise HTTPException(status_code=404, detail="暂无总结可下载")

    content = (fs.markdown_text or fs.full_text or fs.overview or "").strip()
    if not content:
        raise HTTPException(status_code=404, detail="暂无总结可下载")

    filename = f"{meeting.title or meeting_id}-summary.md".replace("/", "-")
    return StreamingResponse(
        BytesIO(content.encode("utf-8")),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


def _transcript_to_dict(t) -> dict:
    return {
        "id": t.id,
        "meeting_id": t.meeting_id,
        "speaker_id": t.speaker_id,
        "speaker_label": t.speaker_label,
        "text": t.text,
        "start_time": t.start_time,
        "end_time": t.end_time,
        "confidence": t.confidence,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _summary_has_content(summary: FinalSummary | None) -> bool:
    if summary is None:
        return False
    if (summary.overview or "").strip():
        return True
    if summary.key_decisions or summary.action_items:
        return True
    if (summary.markdown_text or "").strip():
        return True
    return bool(summary.raw_summary_json)


async def _ensure_final_summary(db: AsyncSession, meeting_id: str) -> FinalSummary | None:
    existing_result = await db.execute(
        select(FinalSummary)
        .where(FinalSummary.meeting_id == meeting_id)
        .order_by(FinalSummary.generated_at.desc())
    )
    for existing in existing_result.scalars().all():
        if _summary_has_content(existing):
            return existing

    transcript_result = await db.execute(
        select(TranscriptLine)
        .where(TranscriptLine.meeting_id == meeting_id)
        .order_by(TranscriptLine.start_time)
    )
    transcript_models = transcript_result.scalars().all()
    if not transcript_models:
        return None

    transcript_lines = [
        {
            "id": line.id,
            "speaker": line.speaker_label,
            "speaker_id": line.speaker_id,
            "text": line.text,
            "start": line.start_time,
            "end": line.end_time,
            "confidence": line.confidence,
        }
        for line in transcript_models
    ]

    try:
        submit_final_summary(meeting_id, transcript_lines, [])
    except Exception as exc:
        print(f"[Summary] 生成最终总结失败: {exc}", flush=True)
        raise HTTPException(status_code=500, detail=f"总结生成失败: {str(exc)[:240]}") from exc

    refreshed_result = await db.execute(
        select(FinalSummary)
        .where(FinalSummary.meeting_id == meeting_id)
        .order_by(FinalSummary.generated_at.desc())
    )
    for refreshed in refreshed_result.scalars().all():
        if _summary_has_content(refreshed):
            return refreshed
    return None


# ==================== 摘要查询 ====================

@router.get("/{meeting_id}/summaries/final")
async def get_final_summary(meeting_id: str, db: AsyncSession = Depends(get_db)):
    """获取会议最终摘要"""
    result = await db.execute(
        select(Meeting).where(Meeting.id == meeting_id)
    )
    meeting = result.scalar_one_or_none()
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    fs = await _ensure_final_summary(db, meeting_id)
    if not fs:
        if meeting.status in ("ended", "completed"):
            raise HTTPException(status_code=404, detail="摘要生成中或尚未生成，请稍后重试")
        raise HTTPException(status_code=404, detail="摘要不存在，可能还在处理中")

    action_items = []
    for item in fs.action_items:
        action_items.append({
            "id": str(uuid.uuid4()),
            "content": item.get("content", ""),
            "assignee": item.get("assignee"),
            "due_date": item.get("due_date"),
            "status": item.get("status", "pending"),
        })

    return {
        "id": fs.id,
        "meeting_id": fs.meeting_id,
        "overview": fs.overview,
        "full_text": fs.full_text or fs.overview,
        "markdown": fs.markdown_text,
        "raw_json": fs.raw_summary_json,
        "key_decisions": fs.key_decisions,
        "action_items": action_items,
        "generated_at": fs.generated_at.isoformat() if fs.generated_at else None,
    }


@router.get("/{meeting_id}/summaries/periods")
async def get_period_summaries(meeting_id: str, db: AsyncSession = Depends(get_db)):
    """获取会议各阶段摘要"""
    from app.models.summary import PeriodSummary

    result = await db.execute(
        select(PeriodSummary)
        .where(PeriodSummary.meeting_id == meeting_id)
        .order_by(PeriodSummary.period_start)
    )
    items = []
    for ps in result.scalars().all():
        items.append({
            "id": ps.id,
            "meeting_id": ps.meeting_id,
            "period_start": ps.period_start,
            "period_end": ps.period_end,
            "bullet_points": ps.bullet_points,
            "generated_at": ps.generated_at.isoformat() if ps.generated_at else None,
        })
    return {"items": items}


# ==================== 后台处理（独立线程中运行） ====================

async def process_audio_in_background(meeting_id: str, file_path: str):
    """后台处理音频文件 — VibeVoice-ASR + CAM++ + 摘要生成"""
    print(f"[Background] 开始处理: {file_path}", flush=True)
    transcript_lines = []
    try:
        import soundfile as sf
        import numpy as np
        import subprocess

        # m4a 等 soundfile 不支持的格式，用 ffmpeg 转码
        ext = file_path.rsplit(".", 1)[-1].lower()
        if ext in ("m4a", "aac", "mp3", "ogg", "flac", "wma"):
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_wav:
                tmp_wav_path = tmp_wav.name
            try:
                result = subprocess.run(
                    ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                     "-i", file_path,
                     "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
                     tmp_wav_path],
                    capture_output=True, timeout=120,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"ffmpeg failed: {result.stderr.decode(errors='replace')}")
                audio, sample_rate = sf.read(tmp_wav_path, dtype='float32')
            finally:
                os.unlink(tmp_wav_path)
        else:
            audio, sample_rate = sf.read(file_path, dtype='float32')
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)

        print(f"[Background] 音频加载: {len(audio)} 样本, {sample_rate} Hz", flush=True)

        from app.services.offline_pipeline import process_audio

        async def on_progress(stage: str, progress: float):
            print(f"[Background] {stage}: {progress * 100:.1f}%", flush=True)

        result = await process_audio(
            meeting_id=meeting_id,
            audio_data=audio,
            sample_rate=sample_rate,
            on_progress=on_progress,
        )

        print(f"[Background] 处理完成: {len(result.turns)} turns, {len(result.speakers)} speakers", flush=True)

        try:
            from app.database import async_session
            from app.models.transcript import TranscriptLine
            from app.models.meeting import Meeting as MeetingModel

            async with async_session() as db:
                meeting_result = await db.execute(
                    select(MeetingModel).where(MeetingModel.id == meeting_id)
                )
                db_meeting = meeting_result.scalar_one_or_none()
                if db_meeting:
                    db_meeting.status = "ended"

                for turn in result.turns:
                    if not turn.speaker_id or not turn.text:
                        continue
                    line = TranscriptLine(
                        id=str(uuid.uuid4()),
                        meeting_id=meeting_id,
                        speaker_id=turn.speaker_id,
                        speaker_label=turn.speaker_name or turn.speaker_id,
                        text=turn.text,
                        start_time=turn.start_ms / 1000.0,
                        end_time=turn.end_ms / 1000.0,
                        confidence=turn.confidence,
                    )
                    db.add(line)
                    transcript_lines.append(line)
                await db.commit()
                print(f"[Background] 转写内容已保存", flush=True)
        except Exception as db_err:
            print(f"[Background] 保存失败: {db_err}")
            import traceback
            traceback.print_exc()

        # 用 process_audio 返回的 summary 写入数据库（避免重复调用 meetingsummary）
        if result.summary:
            import sqlite3 as sqlite3_mod
            summary_id = str(uuid.uuid4())
            db_path = Path(__file__).resolve().parents[2] / "local.db"
            conn = sqlite3_mod.connect(db_path, timeout=10)
            conn.execute("PRAGMA busy_timeout = 5000")
            try:
                conn.execute("""
                    INSERT INTO final_summaries
                    (id, meeting_id, overview, key_decisions_json, action_items_json, generated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    summary_id,
                    meeting_id,
                    result.summary.get("overview", ""),
                    json.dumps(result.summary.get("key_decisions", []), ensure_ascii=False),
                    json.dumps(result.summary.get("action_items", []), ensure_ascii=False),
                    datetime.utcnow().isoformat(),
                ))
                conn.commit()
                print(f"[Background] 摘要已写入数据库，summary_id={summary_id}", flush=True)
            finally:
                conn.close()

        # 清理音频文件
        try:
            Path(file_path).unlink()
        except Exception:
            pass

    except Exception as e:
        print(f"[Background] 处理失败: {e}")
        import traceback
        traceback.print_exc()

        try:
            from app.database import async_session
            from app.models.meeting import Meeting as MeetingModel

            async with async_session() as db:
                meeting_result = await db.execute(
                    select(MeetingModel).where(MeetingModel.id == meeting_id)
                )
                db_meeting = meeting_result.scalar_one_or_none()
                if db_meeting and db_meeting.status == "processing":
                    db_meeting.status = "failed"
                    await db.commit()
                    print(f"[Background] 已将会议状态重置为 failed")
        except Exception as db_err:
            print(f"[Background] 重置会议状态失败: {db_err}")


def _to_response(meeting: Meeting) -> MeetingResponse:
    participants = []
    if meeting.participants:
        try:
            participants = json.loads(meeting.participants)
        except (json.JSONDecodeError, TypeError):
            pass
    return MeetingResponse(
        id=meeting.id,
        title=meeting.title,
        description=meeting.description,
        status=meeting.status,
        participants=participants,
        mode=meeting.mode or "realtime",
        created_at=meeting.created_at,
        updated_at=meeting.updated_at,
    )
