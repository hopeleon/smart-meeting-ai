from datetime import datetime

from typing import Literal

from pydantic import BaseModel


class MeetingCreate(BaseModel):
    title: str
    description: str | None = None
    participants: list[str] = []
    mode: Literal["realtime", "offline", "whisper", "qwen"] = "realtime"


class MeetingUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    participants: list[str] | None = None
    mode: Literal["realtime", "offline", "whisper", "qwen"] | None = None


class MeetingResponse(BaseModel):
    id: str
    title: str
    description: str | None
    status: str
    participants: list[str]
    mode: Literal["realtime", "offline", "whisper", "qwen"]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    total: int
    page: int
    size: int
