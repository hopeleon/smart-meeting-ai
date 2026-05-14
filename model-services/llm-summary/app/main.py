"""
LLM 总结服务 - FastAPI 入口
==========================
当前为 STUB 实现，返回 mock 数据。
算法团队接入时，只需修改 interface.py 中的实现。
"""

from fastapi import FastAPI, Request

from app.interface import summarize_period, summarize_final

app = FastAPI(title="LLM 会议总结服务", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "llm-summary"}


@app.post("/summarize/period")
async def summarize_period_endpoint(request: Request):
    """
    阶段总结接口。

    请求格式：
    {
        "meeting_id": "uuid",
        "transcript_lines": [
            {"speaker": "发言人A", "text": "...", "start": 0.0, "end": 3.5}
        ]
    }

    响应格式：
    {
        "bullet_points": ["要点1", "要点2", "要点3"]
    }
    """
    body = await request.json()
    meeting_id = body.get("meeting_id", "")
    transcript_lines = body.get("transcript_lines", [])
    result = await summarize_period(meeting_id, transcript_lines)
    return result


@app.post("/summarize/final")
async def summarize_final_endpoint(request: Request):
    """
    最终总结接口。

    请求格式：
    {
        "meeting_id": "uuid",
        "all_transcript_lines": [...],
        "period_summaries": [...]
    }

    响应格式：
    {
        "overview": "会议概述",
        "key_decisions": ["决策1"],
        "action_items": [{"content": "...", "assignee": "...", "due_date": "..."}]
    }
    """
    body = await request.json()
    meeting_id = body.get("meeting_id", "")
    all_lines = body.get("all_transcript_lines", [])
    period_summaries = body.get("period_summaries", [])
    result = await summarize_final(meeting_id, all_lines, period_summaries)
    return result
