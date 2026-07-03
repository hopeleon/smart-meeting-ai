import uuid
import json
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.models.meeting import Meeting
from app.schemas.meeting import MeetingCreate, MeetingUpdate


class MeetingService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_meeting(self, meeting_id: str) -> Meeting | None:
        result = await self.db.execute(select(Meeting).where(Meeting.id == meeting_id))
        return result.scalar_one_or_none()

    async def create_meeting(self, data: MeetingCreate) -> Meeting:
        meeting = Meeting(
            id=str(uuid.uuid4()),
            title=data.title,
            description=data.description,
            participants=json.dumps(data.participants, ensure_ascii=False),
            status="created",
            mode=data.mode,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(meeting)
        await self.db.flush()
        await self.db.refresh(meeting)
        return meeting

    async def update_status(self, meeting_id: str, status: str) -> Meeting | None:
        meeting = await self.get_meeting(meeting_id)
        if not meeting:
            return None
        meeting.status = status
        meeting.updated_at = datetime.utcnow()
        await self.db.flush()
        return meeting
