from datetime import datetime

from pydantic import BaseModel


class TranscriptSegment(BaseModel):
    id: str
    meeting_id: str
    speaker_id: str
    speaker_label: str
    text: str
    start_time: float
    end_time: float
    confidence: float
    created_at: datetime

    model_config = {"from_attributes": True}


class TranscriptSegmentCreate(BaseModel):
    speaker_id: str
    speaker_label: str
    text: str
    start_time: float
    end_time: float
    confidence: float = 0.0


class TranscriptListResponse(BaseModel):
    items: list[TranscriptSegment]
    total: int
