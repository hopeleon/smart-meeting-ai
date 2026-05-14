from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


from app.models.meeting import Meeting, MeetingSegment  # noqa: E402, F401
from app.models.transcript import TranscriptLine  # noqa: E402, F401
from app.models.summary import PeriodSummary, FinalSummary  # noqa: E402, F401
