"""
总结异步任务
===========
Celery 任务：调用 LLM 服务生成会议总结，并存储到数据库。
"""

import asyncio
import json
import uuid
from datetime import datetime

from celery import chain, group
from app.workers.celery_app import celery_app


@celery_app.task(bind=True, name="summary.period_summary")
def period_summary_task(self, meeting_id: str, transcript_lines: list[dict]):
    """
    异步阶段总结任务。

    1. 调用 LLM 生成阶段要点
    2. 存储结果到 PeriodSummary 表
    """
    from app.database import engine, async_session
    from app.models.summary import PeriodSummary
    from app.services.summary_service import SummaryService

    service = SummaryService()

    # 获取或创建事件循环，并用 try/finally 确保清理
    # 关键：先 set_event_loop 再 run_until_complete，避免与 FastAPI 的循环冲突
    try:
        _loop = asyncio.get_running_loop()
        # 已在运行循环中（eager 模式下），用新 loop + set
        _new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_new_loop)
        _used_loop = _new_loop
    except RuntimeError:
        # 没有运行中的循环，正常创建
        _used_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_used_loop)

    try:
        # 调用 LLM 服务
        result = _used_loop.run_until_complete(
            service.summarize_period(meeting_id, transcript_lines)
        )

        # 计算时间范围
        bullet_points = result.get("bullet_points", [])
        period_start = min((l.get("start", 0) for l in transcript_lines), default=0)
        period_end = max((l.get("end", 0) for l in transcript_lines), default=0)

        # 存储到数据库
        async def _store():
            async with async_session() as db:
                summary = PeriodSummary(
                    id=str(uuid.uuid4()),
                    meeting_id=meeting_id,
                    period_start=period_start,
                    period_end=period_end,
                    bullet_points=bullet_points,
                    generated_at=datetime.utcnow(),
                )
                db.add(summary)
                await db.commit()
                await db.refresh(summary)
                return summary.id

        summary_id = _used_loop.run_until_complete(_store())

        return {
            "meeting_id": meeting_id,
            "summary_id": summary_id,
            "bullet_points": bullet_points,
            "period_start": period_start,
            "period_end": period_end,
            "generated_at": datetime.utcnow().isoformat(),
        }
    finally:
        _used_loop.close()
        # 恢复默认循环策略，避免影响其他代码
        asyncio.set_event_loop(None)


@celery_app.task(bind=True, name="summary.final_summary")
def final_summary_task(
    self, meeting_id: str, all_transcript_lines: list[dict], period_summaries: list[dict]
):
    """
    异步最终总结任务。

    1. 调用 LLM 生成完整会议纪要
    2. 存储结果到 FinalSummary 表
    """
    from app.database import async_session
    from app.models.summary import FinalSummary
    from app.services.summary_service import SummaryService

    service = SummaryService()

    # 获取或创建事件循环，避免与 FastAPI 运行中的事件循环冲突
    try:
        _loop = asyncio.get_running_loop()
        _new_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_new_loop)
        _used_loop = _new_loop
    except RuntimeError:
        _used_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_used_loop)

    try:
        # 调用 LLM 服务
        result = _used_loop.run_until_complete(
            service.summarize_final(meeting_id, all_transcript_lines, period_summaries)
        )

        overview = result.get("overview", "")
        key_decisions = result.get("key_decisions", [])
        action_items = result.get("action_items", [])

        # 存储到数据库
        async def _store():
            async with async_session() as db:
                summary = FinalSummary(
                    id=str(uuid.uuid4()),
                    meeting_id=meeting_id,
                    overview=overview,
                    key_decisions=key_decisions,
                    action_items=action_items,
                    generated_at=datetime.utcnow(),
                )
                db.add(summary)
                await db.commit()
                await db.refresh(summary)
                return summary.id

        summary_id = _used_loop.run_until_complete(_store())

        return {
            "meeting_id": meeting_id,
            "summary_id": summary_id,
            "overview": overview,
            "key_decisions": key_decisions,
            "action_items": action_items,
            "generated_at": datetime.utcnow().isoformat(),
        }
    finally:
        _used_loop.close()
        asyncio.set_event_loop(None)
