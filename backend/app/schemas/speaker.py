"""
声纹管理 API Schema
"""

from typing import Optional, List, Dict, Any
from pydantic import BaseModel


class SpeakerRegisterRequest(BaseModel):
    """声纹注册请求（JSON 方式）"""
    name: str
    speaker_id: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    audio_base64: Optional[str] = None  # 可选，支持 base64 音频


class SpeakerProfile(BaseModel):
    """说话人信息（不含 embedding）"""
    speaker_id: str
    name: Optional[str] = None
    role: Optional[str] = None
    department: Optional[str] = None
    sample_count: int = 0
    quality: float = 0.0
    registered_at: Optional[str] = None
    updated_at: Optional[str] = None
    is_active: bool = True
    total_identifications: int = 0
    avg_confidence: Optional[float] = None
    last_recognized_at: Optional[str] = None
    last_confidence: Optional[float] = None
    embedding_std_mean: Optional[float] = None
    embedding_std_max: Optional[float] = None


class SpeakerStats(BaseModel):
    """声纹数据库统计"""
    total: int = 0
    active: int = 0
    inactive: int = 0
    db_path: str = ""
    total_identifications: int = 0
    today_identifications: int = 0
    unique_speakers_identified: int = 0
    quality_avg: float = 0.0
    sample_avg: float = 0.0
    department_distribution: Dict[str, int] = {}
    role_distribution: Dict[str, int] = {}


class SpeakerListResponse(BaseModel):
    """说话人列表响应"""
    speakers: List[SpeakerProfile]
    stats: SpeakerStats
    total: int


class SpeakerSimilarityPair(BaseModel):
    """声纹相似度对"""
    speaker_a_id: str
    speaker_a_name: str
    speaker_b_id: str
    speaker_b_name: str
    similarity: float
    distance: float
    embedding_std_a: Optional[float] = None
    embedding_std_b: Optional[float] = None


class SpeakerStatsDetailResponse(SpeakerStats):
    """详细统计响应（含相似度分布）"""
    similarity_distribution: List[SpeakerSimilarityPair] = []


class RegisterResponse(BaseModel):
    """注册响应"""
    success: bool
    speaker_id: str
    name: str
    message: str
    quality: float
    quality_level: Optional[str] = None
    quality_description: Optional[str] = None
    sample_count: int
    total_duration: Optional[float] = None
    quality_issues: List[str] = []


class DeleteRequest(BaseModel):
    """删除请求"""
    speaker_id: str


class DeleteResponse(BaseModel):
    """删除响应"""
    success: bool
    speaker_id: str
    message: str


class SearchResponse(BaseModel):
    """搜索响应"""
    speakers: List[SpeakerProfile]
    total: int
