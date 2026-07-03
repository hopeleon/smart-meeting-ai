"""
vad_asr_pipeline.py - 兼容性适配层

将旧版 InsightEye 的 vad_asr_pipeline 模块映射到新的 module 结构：
- StreamingPipeline, create_streaming_pipeline 从 streaming_pipeline.py 重新导出
- TranscriptDelta, TranscriptSegment 等数据结构在此重新导出
- EnhancedRecognitionEngine, AudioDenoiser, RealtimeSessionStore 等从各子模块导入

注意：
- 旧的 EnhancedRecognitionEngine 已迁移到 enhanced_engine.py
- 新的 StreamingPipeline 不再使用内嵌的 enhanced_engine，改为通过 set_enhanced_registry 注入
"""

from app.asr.streaming_pipeline import (
    StreamingPipeline,
    create_streaming_pipeline,
    TranscriptDelta,
    StreamingVAD,
    StreamingASR,
    StreamingSpeakerRecognition,
    StreamingChangeDetectorConfig,
    CHANGE_DETECTOR_CONFIG,
)
from app.asr.enhanced_engine import EnhancedRecognitionEngine
from app.asr.audio_denoiser import AudioDenoiser, RNNoiseDenoiser
from app.asr.realtime_session import RealtimeSessionStore, get_store

__all__ = [
    "StreamingPipeline",
    "create_streaming_pipeline",
    "TranscriptDelta",
    "StreamingVAD",
    "StreamingASR",
    "StreamingSpeakerRecognition",
    "EnhancedRecognitionEngine",
    "AudioDenoiser",
    "RNNoiseDenoiser",
    "RealtimeSessionStore",
    "get_store",
]
