"""
老记 API
=======
一句话待办的独立接口层。当前仍复用日程解析、日程存储和短音频 ASR 服务，
但接口命名空间已经独立为 /api/laoji，便于后续拆成单独 app。
"""

from typing import Optional

import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.schemas.schedule import (
    ScheduleAsrTranscribeRequest,
    ScheduleAsrTranscribeResponse,
    ScheduleAudioParseRequest,
    ScheduleClarifyRequest,
    ScheduleEvent,
    ScheduleEventCreate,
    ScheduleEventListResponse,
    ScheduleEventUpdate,
    ScheduleParseRequest,
    ScheduleParseResponse,
)
from app.services import schedule_db_service as db
from app.services.schedule_parser_service import (
    apply_schedule_clarification,
    parse_schedule_audio,
    parse_schedule_text,
    transcribe_schedule_audio,
)


router = APIRouter()


def _to_parse_response(parsed: dict, raw_text: str = "") -> ScheduleParseResponse:
    return ScheduleParseResponse(
        title=parsed.get("title", "日程"),
        event_type=parsed.get("event_type", "once"),
        start_date=parsed.get("start_date", ""),
        start_time=parsed.get("start_time"),
        end_time=parsed.get("end_time"),
        is_all_day=parsed.get("is_all_day", False),
        description=parsed.get("description"),
        raw_text=parsed.get("raw_text", raw_text),
        parse_source=parsed.get("parse_source", "rules"),
        confidence=parsed.get("confidence", 0.0),
        needs_clarification=parsed.get("needs_clarification", False),
        clarification_question=parsed.get("clarification_question"),
    )


@router.post("/parse", response_model=ScheduleParseResponse)
async def parse_text(request: ScheduleParseRequest):
    """将自然语言文本解析为老记日程草稿。"""
    parsed = await parse_schedule_text(request.text)
    if parsed is None:
        raise HTTPException(status_code=422, detail="无法从文本中提取日程信息，请说得更明确一些")
    return _to_parse_response(parsed, raw_text=request.text)


@router.post("/clarify", response_model=ScheduleParseResponse)
async def clarify_text(request: ScheduleClarifyRequest):
    """把用户对追问的补充应用到当前日程草稿。"""
    parsed = apply_schedule_clarification(
        request.current.model_dump(),
        request.answer,
    )
    if parsed is None:
        raise HTTPException(status_code=422, detail="没有理解这次补充，请直接填写具体日期或时间")
    return _to_parse_response(parsed, raw_text=request.current.raw_text)


@router.post("/parse-audio", response_model=ScheduleParseResponse)
async def parse_audio(request: ScheduleAudioParseRequest):
    """将语音音频直接解析为日程：ASR 转写 -> 结构化解析。"""
    parsed = await parse_schedule_audio(request.audio_base64, request.filename)
    if parsed is None:
        raise HTTPException(
            status_code=422,
            detail="无法从语音中提取日程，请确保麦克风清晰并说得更明确",
        )
    return _to_parse_response(parsed)


@router.post("/asr/transcribe", response_model=ScheduleAsrTranscribeResponse)
async def transcribe_audio(request: ScheduleAsrTranscribeRequest):
    """老记轻量 ASR：录音后一次性转写，只返回文字。"""
    result = await transcribe_schedule_audio(request.audio_base64, request.filename)
    if result is None:
        raise HTTPException(status_code=422, detail="没有识别到有效语音，请确认音量和录音内容")
    return ScheduleAsrTranscribeResponse(**result)


@router.post("/events", response_model=ScheduleEvent, status_code=201)
def create_event(request: ScheduleEventCreate):
    """创建老记日程。"""
    event = db.create_event(
        title=request.title,
        event_type=request.event_type,
        start_date=request.start_date,
        start_time=request.start_time,
        end_time=request.end_time,
        is_all_day=request.is_all_day,
        description=request.description,
        raw_text=request.raw_text,
    )
    return ScheduleEvent(**event)


@router.get("/events", response_model=ScheduleEventListResponse)
def list_events(
    year: Optional[int] = Query(None, ge=2000, le=2100),
    month: Optional[int] = Query(None, ge=1, le=12),
):
    """查询老记日程列表。传入 year + month 可展开该月重复事件。"""
    events = db.list_events(year=year, month=month)
    return ScheduleEventListResponse(
        events=[ScheduleEvent(**event) for event in events],
        total=len(events),
    )


@router.get("/events/{event_id}", response_model=ScheduleEvent)
def get_event(event_id: int):
    """获取单个老记日程详情。"""
    event = db.get_event(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="日程不存在")
    return ScheduleEvent(**event)


@router.put("/events/{event_id}", response_model=ScheduleEvent)
def update_event(event_id: int, request: ScheduleEventUpdate):
    """更新老记日程。"""
    event = db.update_event(
        event_id=event_id,
        title=request.title,
        event_type=request.event_type,
        start_date=request.start_date,
        start_time=request.start_time,
        end_time=request.end_time,
        is_all_day=request.is_all_day,
        description=request.description,
    )
    if event is None:
        raise HTTPException(status_code=404, detail="日程不存在")
    return ScheduleEvent(**event)


@router.delete("/events/{event_id}", status_code=204)
def delete_event(event_id: int):
    """删除老记日程。"""
    deleted = db.delete_event(event_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="日程不存在")
