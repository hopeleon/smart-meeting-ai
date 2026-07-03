from fastapi import APIRouter

from app.api import meetings, transcript, summary, speakers, schedule
from app.laoji.router import router as laoji_router

api_router = APIRouter()

api_router.include_router(meetings.router, prefix="/meetings", tags=["meetings"])
api_router.include_router(transcript.router, prefix="/meetings/{meeting_id}/transcripts", tags=["transcript"])
api_router.include_router(summary.router, prefix="/meetings/{meeting_id}/summaries", tags=["summary"])
api_router.include_router(speakers.router, prefix="/speakers", tags=["speakers"])
api_router.include_router(laoji_router, prefix="/laoji", tags=["laoji"])
api_router.include_router(schedule.router, prefix="/schedule", tags=["schedule"])
