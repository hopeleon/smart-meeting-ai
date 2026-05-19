from fastapi import APIRouter

from app.api import meetings, audio, transcript, summary, speakers

api_router = APIRouter()

api_router.include_router(meetings.router, prefix="/meetings", tags=["meetings"])
api_router.include_router(audio.router, prefix="/meetings/{meeting_id}/audio", tags=["audio"])
api_router.include_router(transcript.router, prefix="/meetings/{meeting_id}/transcripts", tags=["transcript"])
api_router.include_router(summary.router, prefix="/meetings/{meeting_id}/summaries", tags=["summary"])
api_router.include_router(speakers.router, prefix="/speakers", tags=["speakers"])
