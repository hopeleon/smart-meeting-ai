from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Path
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.summary import PeriodSummary, FinalSummary
from app.models.transcript import TranscriptLine
from app.schemas.summary import PeriodSummaryResponse, PeriodSummaryListResponse, FinalSummaryResponse
from app.workers.summary_tasks import period_summary_task, final_summary_task

router = APIRouter()


@router.post("/generate", status_code=202)
async def generate_summary(
    meeting_id: str = Path(..., description="会议ID"),
    summary_type: str = "final",
    background_tasks: BackgroundTasks = None,
    db: AsyncSession = Depends(get_db),
):
    """
    触发会议总结生成（需要通过路径参数指定 meeting_id）。

    Args:
        meeting_id: 会议 ID（来自 URL 前缀）
        summary_type: "final" (最终总结) 或 "period" (阶段总结)
    """
    # 检查会议是否存在
    from app.models.meeting import Meeting
    meeting_result = await db.execute(select(Meeting).where(Meeting.id == meeting_id))
    if not meeting_result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail=f"会议 {meeting_id} 不存在")

    # 获取转写文本
    transcript_result = await db.execute(
        select(TranscriptLine)
        .where(TranscriptLine.meeting_id == meeting_id)
        .order_by(TranscriptLine.start_time)
    )
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
        for line in transcript_result.scalars().all()
    ]

    if not transcript_lines:
        raise HTTPException(status_code=400, detail="会议暂无转写文本")

    # 异步触发总结任务
    if summary_type == "period":
        # 阶段总结：使用最近 N 条转写
        recent_lines = transcript_lines[-20:] if len(transcript_lines) > 20 else transcript_lines
        task = period_summary_task.delay(meeting_id, recent_lines)
        return {"message": "阶段总结任务已提交", "task_id": task.id, "transcript_count": len(recent_lines)}
    else:
        # 最终总结：使用全部转写
        task = final_summary_task.delay(meeting_id, transcript_lines, [])
        return {"message": "最终总结任务已提交", "task_id": task.id, "transcript_count": len(transcript_lines)}


@router.get("/task/{task_id}")
async def get_summary_task_status(task_id: str):
    """查询总结任务状态"""
    from celery.result import AsyncResult
    result = AsyncResult(task_id)
    return {
        "task_id": task_id,
        "status": result.state,
        "result": result.result if result.ready() else None,
    }


@router.get("/period", response_model=PeriodSummaryListResponse)
async def get_period_summaries(
    meeting_id: str = Path(..., description="会议ID"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PeriodSummary)
        .where(PeriodSummary.meeting_id == meeting_id)
        .order_by(PeriodSummary.period_start)
    )
    items = [
        PeriodSummaryResponse(
            id=s.id,
            meeting_id=s.meeting_id,
            period_start=s.period_start,
            period_end=s.period_end,
            bullet_points=s.bullet_points,
            generated_at=s.generated_at,
        )
        for s in result.scalars().all()
    ]
    return PeriodSummaryListResponse(items=items)


@router.get("/final", response_model=FinalSummaryResponse)
async def get_final_summary(
    meeting_id: str = Path(..., description="会议ID"),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(FinalSummary).where(FinalSummary.meeting_id == meeting_id)
    )
    summary = result.scalar_one_or_none()
    if not summary:
        raise HTTPException(status_code=404, detail="Final summary not found")
    return FinalSummaryResponse(
        id=summary.id,
        meeting_id=summary.meeting_id,
        overview=summary.overview,
        key_decisions=summary.key_decisions,
        action_items=summary.action_items,
        generated_at=summary.generated_at,
    )
