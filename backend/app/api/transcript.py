from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.transcript import TranscriptLine
from app.schemas.transcript import TranscriptSegment, TranscriptListResponse

router = APIRouter()


@router.get("", response_model=TranscriptListResponse)
async def get_transcripts(
    meeting_id: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
):
    total_q = await db.execute(
        select(func.count(TranscriptLine.id)).where(TranscriptLine.meeting_id == meeting_id)
    )
    total = total_q.scalar() or 0

    result = await db.execute(
        select(TranscriptLine)
        .where(TranscriptLine.meeting_id == meeting_id)
        .order_by(TranscriptLine.start_time)
        .offset(offset)
        .limit(limit)
    )
    items = [
        TranscriptSegment.model_validate(line) for line in result.scalars().all()
    ]
    return TranscriptListResponse(items=items, total=total)
