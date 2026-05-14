"""
LLM 总结服务接口规范
==================
算法团队需实现此文件中的所有函数。
main.py 中的 FastAPI 路由会调用这些函数。
请勿修改函数签名。

接入说明：
- 输入为转写文本列表
- 输出为结构化的总结内容
- 模型文件放在 models/ 目录下（本地模型）
- 或通过环境变量配置外部 API（如 OpenAI、Claude）
- 依赖添加到 requirements.txt
"""


async def summarize_period(meeting_id: str, transcript_lines: list[dict]) -> dict:
    """
    阶段总结。

    输入：
        meeting_id: 会议 ID
        transcript_lines: 转写行列表
            [{"speaker": "发言人A", "text": "...", "start": 0.0, "end": 3.5}]

    输出：
        {"bullet_points": ["要点1", "要点2", "要点3"]}

    TODO: 算法团队实现此接口
    """
    # STUB 实现 - 返回 mock 数据
    return {
        "bullet_points": [
            "讨论了Q2产品路线图，确定了三个核心功能模块",
            "前端团队将采用React 18 + TypeScript技术栈",
            "后端使用FastAPI，预计6月底完成第一版",
        ]
    }


async def summarize_final(
    meeting_id: str,
    all_transcript_lines: list[dict],
    period_summaries: list[dict],
) -> dict:
    """
    最终总结。

    输入：
        meeting_id: 会议 ID
        all_transcript_lines: 全部转写行
        period_summaries: 阶段总结列表

    输出：
        {
            "overview": "会议概述",
            "key_decisions": ["决策1", "决策2"],
            "action_items": [
                {"content": "任务描述", "assignee": "负责人", "due_date": "2026-05-20"}
            ]
        }

    TODO: 算法团队实现此接口
    """
    # STUB 实现 - 返回 mock 数据
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
