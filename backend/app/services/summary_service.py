"""
LLM 总结服务接口层
================
当前为 STUB 实现，返回 mock 数据。
算法团队接入时，修改 _call_llm_model() 方法即可，无需改动上层调用。

接入要求：
- model-services/llm-summary 服务需在 8003 端口提供 HTTP API
- 接口规范见 docs/api-contracts.md
"""

import httpx

from app.config import settings


class SummaryService:
    def __init__(self):
        self.model_url = settings.LLM_SUMMARY_SERVICE_URL  # 默认 http://llm-summary-stub:8003

    async def summarize_period(self, meeting_id: str, transcript_lines: list[dict]) -> dict:
        """
        阶段总结接口。
        输入一段转写文本，返回要点列表。

        TODO: 算法团队替换此处实现

        Args:
            meeting_id: 会议 ID
            transcript_lines: 转写行列表 [{"speaker": "发言人A", "text": "...", "start": 0.0, "end": 3.5}]

        Returns:
            {"bullet_points": ["要点1", "要点2", "要点3"]}
        """
        # STUB: 调用 model-services/llm-summary
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.model_url}/summarize/period",
                    json={
                        "meeting_id": meeting_id,
                        "transcript_lines": transcript_lines,
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception:
            # 降级返回 mock
            return self._mock_period_summary()

    async def summarize_final(
        self,
        meeting_id: str,
        all_transcript_lines: list[dict],
        period_summaries: list[dict],
    ) -> dict:
        """
        最终总结接口。
        输入全部转写文本和阶段总结，返回完整会议纪要。

        TODO: 算法团队替换此处实现

        Returns:
            {
                "overview": "会议概述",
                "key_decisions": ["决策1", "决策2"],
                "action_items": [{"content": "...", "assignee": "...", "due_date": "..."}]
            }
        """
        # STUB: 调用 model-services/llm-summary
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{self.model_url}/summarize/final",
                    json={
                        "meeting_id": meeting_id,
                        "all_transcript_lines": all_transcript_lines,
                        "period_summaries": period_summaries,
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception:
            # 降级返回 mock
            return self._mock_final_summary()

    def _mock_period_summary(self) -> dict:
        """Mock 数据，仅用于开发阶段联调"""
        return {
            "bullet_points": [
                "讨论了Q2产品路线图，确定了三个核心功能模块",
                "前端团队将采用React 18 + TypeScript技术栈",
                "后端使用FastAPI，预计6月底完成第一版",
            ]
        }

    def _mock_final_summary(self) -> dict:
        """Mock 数据，仅用于开发阶段联调"""
        return {
            "overview": "本次会议讨论了Q2产品规划，确定了技术选型和开发计划。与会人员就前端框架、后端架构、AI模型集成等关键问题达成一致。",
            "key_decisions": [
                "采用React 18 + TypeScript作为前端技术栈",
                "后端使用FastAPI + Celery异步架构",
                "ASR和LLM模型独立部署，通过HTTP接口调用",
            ],
            "action_items": [
                {
                    "content": "完成前端原型设计",
                    "assignee": "张三",
                    "due_date": "2026-05-20",
                },
                {
                    "content": "搭建后端API框架",
                    "assignee": "李四",
                    "due_date": "2026-05-25",
                },
                {
                    "content": "完成ASR模型服务化",
                    "assignee": "王五",
                    "due_date": "2026-06-01",
                },
            ],
        }
