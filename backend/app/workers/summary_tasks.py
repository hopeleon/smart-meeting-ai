"""
总结异步任务
===========
Celery 任务：调用 LLM 服务生成会议总结。
"""

import asyncio
import uuid
from datetime import datetime

from app.workers.celery_app import celery_app


@celery_app.task(bind=True, name="summary.period_summary")
def period_summary_task(self, meeting_id: str, transcript_lines: list[dict]):
    """
    异步阶段总结任务。

    TODO: 算法团队接入后，此任务会调用真实的 LLM 总结服务。
    """
    from app.services.summary_service import SummaryService

    service = SummaryService()

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            service.summarize_period(meeting_id, transcript_lines)
        )
        return {
            "meeting_id": meeting_id,
            "bullet_points": result.get("bullet_points", []),
            "generated_at": datetime.utcnow().isoformat(),
        }
    finally:
        loop.close()


@celery_app.task(bind=True, name="summary.final_summary")
def final_summary_task(
    self, meeting_id: str, all_transcript_lines: list[dict], period_summaries: list[dict]
):
    """
    异步最终总结任务。

    TODO: 算法团队接入后，此任务会调用真实的 LLM 总结服务。
    """
    from app.services.summary_service import SummaryService

    service = SummaryService()

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            service.summarize_final(meeting_id, all_transcript_lines, period_summaries)
        )
        return {
            "meeting_id": meeting_id,
            "overview": result.get("overview", ""),
            "key_decisions": result.get("key_decisions", []),
            "action_items": result.get("action_items", []),
            "generated_at": datetime.utcnow().isoformat(),
        }
    finally:
        loop.close()
