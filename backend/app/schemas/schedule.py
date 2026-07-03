"""
日程管理 API Schema
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class ScheduleEventCreate(BaseModel):
    """创建日程请求"""
    title: str = Field(..., max_length=100)
    event_type: str = Field(default="once")  # once | daily | weekly | monthly | yearly
    start_date: str = Field(..., description="YYYY-MM-DD 或 MM-DD（monthly/yearly 时）")
    start_time: Optional[str] = Field(None, description="HH:MM，None 表示全天事件")
    end_time: Optional[str] = Field(None, description="HH:MM")
    is_all_day: bool = Field(default=False)
    description: Optional[str] = Field(None, max_length=500)
    raw_text: Optional[str] = Field(None, max_length=2000, description="用户原始语音文本")


class ScheduleEventUpdate(BaseModel):
    """更新日程请求"""
    title: Optional[str] = Field(None, max_length=100)
    event_type: Optional[str] = None
    start_date: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_all_day: Optional[bool] = None
    description: Optional[str] = Field(None, max_length=500)


class ScheduleEvent(BaseModel):
    """日程事件响应"""
    id: int
    title: str
    event_type: str
    start_date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_all_day: bool = False
    description: Optional[str] = None
    raw_text: Optional[str] = None
    created_at: str
    updated_at: str


class ScheduleParseRequest(BaseModel):
    """日程解析请求（纯文本）"""
    text: str = Field(..., max_length=2000, description="用户语音转写的文本")


class ScheduleParseResponse(BaseModel):
    """日程解析响应"""
    title: str
    event_type: str
    start_date: str
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    is_all_day: bool = False
    description: Optional[str] = None
    raw_text: str
    parse_source: str = Field(default="rules", description="rules | local_llm")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: Optional[str] = None


class ScheduleClarifyRequest(BaseModel):
    """对已有解析结果进行追问补充"""
    current: ScheduleParseResponse
    answer: str = Field(..., max_length=1000, description="用户针对追问的补充回答")


class ScheduleAudioParseRequest(BaseModel):
    """语音解析请求（包含 base64 音频）"""
    audio_base64: str = Field(..., description="wav/m4a 音频 base64 编码")
    filename: str = Field(default="recording.wav")


class ScheduleAsrTranscribeRequest(BaseModel):
    """轻量 ASR 转写请求（老记一句话录音）"""
    audio_base64: str = Field(..., description="16k 单声道 wav 音频 base64 编码")
    filename: str = Field(default="recording.wav")


class ScheduleAsrTranscribeResponse(BaseModel):
    """轻量 ASR 转写响应"""
    text: str
    duration_sec: float = 0.0
    provider: str = "funasr"


class ScheduleEventListResponse(BaseModel):
    """日程列表响应"""
    events: List[ScheduleEvent]
    total: int
