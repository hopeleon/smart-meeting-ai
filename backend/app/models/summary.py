import uuid
import json
from datetime import datetime

from sqlalchemy import String, DateTime, Float, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base


class PeriodSummary(Base):
    __tablename__ = "period_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    period_start: Mapped[float] = mapped_column(Float, nullable=False)
    period_end: Mapped[float] = mapped_column(Float, nullable=False)
    bullet_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def bullet_points(self) -> list[str]:
        return json.loads(self.bullet_points_json)

    @bullet_points.setter
    def bullet_points(self, value: list[str]):
        self.bullet_points_json = json.dumps(value, ensure_ascii=False)


class FinalSummary(Base):
    __tablename__ = "final_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id"), nullable=False, index=True)
    overview: Mapped[str] = mapped_column(Text, nullable=False)
    key_decisions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    action_items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    @property
    def key_decisions(self) -> list[str]:
        return json.loads(self.key_decisions_json)

    @key_decisions.setter
    def key_decisions(self, value: list[str]):
        self.key_decisions_json = json.dumps(value, ensure_ascii=False)

    @property
    def action_items(self) -> list[dict]:
        return json.loads(self.action_items_json)

    @action_items.setter
    def action_items(self, value: list[dict]):
        self.action_items_json = json.dumps(value, ensure_ascii=False)
