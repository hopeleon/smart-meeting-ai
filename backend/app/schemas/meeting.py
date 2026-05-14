from datetime import datetime

from pydantic import BaseModel


class MeetingCreate(BaseModel):
    title: str
    description: str | None = None
    participants: list[str] = []


class MeetingUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    status: str | None = None
    participants: list[str] | None = None


class MeetingResponse(BaseModel):
    id: str
    title: str
    description: str | None
    status: str
    participants: list[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class MeetingListResponse(BaseModel):
    items: list[MeetingResponse]
    total: int
    page: int
    size: int
