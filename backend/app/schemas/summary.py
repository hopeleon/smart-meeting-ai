from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class ActionItem(BaseModel):
    id: str
    content: str
    assignee: str | None = None
    due_date: str | None = None
    status: Literal["pending", "in-progress", "done"] = "pending"


class PeriodSummaryResponse(BaseModel):
    id: str
    meeting_id: str
    period_start: float
    period_end: float
    bullet_points: list[str]
    generated_at: datetime

    model_config = {"from_attributes": True}


class FinalSummaryResponse(BaseModel):
    id: str
    meeting_id: str
    overview: str
    key_decisions: list[str]
    action_items: list[ActionItem]
    generated_at: datetime

    model_config = {"from_attributes": True}


class PeriodSummaryListResponse(BaseModel):
    items: list[PeriodSummaryResponse]
