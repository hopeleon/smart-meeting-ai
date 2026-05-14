import uuid
import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from app.database import get_db
from app.models.meeting import Meeting
from app.schemas.meeting import MeetingCreate, MeetingUpdate, MeetingResponse, MeetingListResponse

router = APIRouter()


@router.post("", response_model=MeetingResponse, status_code=201)
async def create_meeting(data: MeetingCreate, db: AsyncSession = Depends(get_db)):
    meeting = Meeting(
        id=str(uuid.uuid4()),
        title=data.title,
        description=data.description,
        participants=json.dumps(data.participants, ensure_ascii=False),
        status="created",
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
        created_at=meeting.created_at,
        updated_at=meeting.updated_at,
    )
