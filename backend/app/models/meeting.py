import uuid
from datetime import datetime

from sqlalchemy import String, DateTime, Text, Enum as SAEnum, ForeignKey, Float
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class Meeting(Base):
    __tablename__ = "meetings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="created")
    mode: Mapped[str] = mapped_column(String(20), default="realtime")  # "realtime" | "offline"
    participants: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON array
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    segments: Mapped[list["MeetingSegment"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    transcript_lines: Mapped[list["TranscriptLine"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    period_summaries: Mapped[list["PeriodSummary"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")
    final_summaries: Mapped[list["FinalSummary"]] = relationship(back_populates="meeting", cascade="all, delete-orphan")


class MeetingSegment(Base):
    __tablename__ = "meeting_segments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    meeting_id: Mapped[str] = mapped_column(ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False)
    audio_path: Mapped[str] = mapped_column(String(512), nullable=False)
    start_time: Mapped[float] = mapped_column(Float, default=0.0)
    end_time: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meeting: Mapped["Meeting"] = relationship(back_populates="segments")
