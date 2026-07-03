import uuid
import json
from datetime import datetime
from pathlib import Path

from sqlalchemy import String, DateTime, Float, Text, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models import Base


class PeriodSummary(Base):
    __tablename__ = "period_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    period_start: Mapped[float] = mapped_column(Float, nullable=False)
    period_end: Mapped[float] = mapped_column(Float, nullable=False)
    bullet_points_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meeting: Mapped["Meeting"] = relationship(back_populates="period_summaries")

    @property
    def bullet_points(self) -> list[str]:
        return json.loads(self.bullet_points_json)

    @bullet_points.setter
    def bullet_points(self, value: list[str]):
        self.bullet_points_json = json.dumps(value, ensure_ascii=False)


class FinalSummary(Base):
    __tablename__ = "final_summaries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    meeting_id: Mapped[str] = mapped_column(
        ForeignKey("meetings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    overview: Mapped[str] = mapped_column(Text, nullable=False)
    key_decisions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    action_items_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    generated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    meeting: Mapped["Meeting"] = relationship(back_populates="final_summaries")

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

    @property
    def summary_dir(self) -> Path:
        return Path(__file__).resolve().parents[2] / "summaries" / "final"

    def _latest_summary_path(self, suffix: str) -> Path | None:
        for pattern in (
            f"final3_{self.meeting_id[:8]}_*{suffix}",
            f"final_{self.meeting_id[:8]}_*{suffix}",
        ):
            matches = sorted(self.summary_dir.glob(pattern), reverse=True)
            for path in matches:
                name = path.name
                if name.endswith("_eval.json") or name.endswith("_completeness.json"):
                    continue
                return path
        return None

    @property
    def markdown_text(self) -> str | None:
        path = self._latest_summary_path(".md")
        if not path:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    @property
    def raw_summary_json(self) -> dict | None:
        path = self._latest_summary_path(".json")
        if not path:
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    @property
    def full_text(self) -> str:
        markdown = self.markdown_text
        if markdown:
            return markdown.strip()

        raw_json = self.raw_summary_json
        if raw_json:
            return json.dumps(raw_json, ensure_ascii=False, indent=2)

        sections: list[str] = []
        overview = (self.overview or "").strip()
        if overview:
            sections.append(overview)

        key_decisions = self.key_decisions
        if key_decisions:
            sections.append("关键决策：\n" + "\n".join(f"- {item}" for item in key_decisions if item))

        action_items = self.action_items
        if action_items:
            lines = []
            for item in action_items:
                content = (item.get("content") or "").strip()
                if not content:
                    continue
                meta = []
                if item.get("assignee"):
                    meta.append(f"负责人：{item['assignee']}")
                if item.get("due_date"):
                    meta.append(f"截止：{item['due_date']}")
                suffix = f"（{'，'.join(meta)}）" if meta else ""
                lines.append(f"- {content}{suffix}")
            if lines:
                sections.append("待办事项：\n" + "\n".join(lines))

        return "\n\n".join(section for section in sections if section).strip()
