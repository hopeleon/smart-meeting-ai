from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.services.audio_service import AudioService
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

    audio_service = AudioService()
    result = await audio_service.save_audio(meeting_id, file)
    return result


@router.post("/chunk")
async def upload_audio_chunk(
    meeting_id: str,
    file: UploadFile = File(...),
    chunk_index: int = 0,
    is_last: bool = False,
    db: AsyncSession = Depends(get_db),
):
    meeting_service = MeetingService(db)
    meeting = await meeting_service.get_meeting(meeting_id)
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    audio_service = AudioService()
    result = await audio_service.save_chunk(meeting_id, file, chunk_index, is_last)
    return result
