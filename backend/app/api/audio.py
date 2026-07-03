"""音频管理路由（保留，未来可能扩展）"""

from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.meeting_service import MeetingService

router = APIRouter()


@router.post("")
async def upload_audio(
    meeting_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    meeting_service = MeetingService(db)
    meeting = await meeting_service.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")
    return {"meeting_id": meeting_id, "filename": file.filename}
