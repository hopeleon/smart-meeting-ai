from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.summary import PeriodSummary, FinalSummary
from app.schemas.summary import PeriodSummaryResponse, PeriodSummaryListResponse, FinalSummaryResponse

router = APIRouter()


@router.get("/period", response_model=PeriodSummaryListResponse)
async def get_period_summaries(
    meeting_id: str,
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
    meeting_id: str,
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
