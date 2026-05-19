"""
ASR 实时处理模块
对齐 InsightEye 模式二

子模块：
- model_manager  : FunASR、CAM++、Silero VAD 模型加载
- vad_asr_pipeline: 流式 VAD + ASR 管道（完整说话人变化检测 + 片段合并）
- enhanced_engine : 增强版声纹识别引擎（多音频注册 + 马氏距离 + PLDA）
- audio_denoiser  : RNNoise 实时音频去噪
- realtime_session : 会议会话状态管理
- realtime_ws_handler: FastAPI WebSocket 处理器
"""

from app.asr.model_manager import ModelManager, get_model_manager, SpeakerEmbeddingExtractor
from app.asr.vad_asr_pipeline import (
    StreamingPipeline,
    StreamingVAD,
    StreamingASR,
    StreamingSpeakerRecognition,
    StreamingChangeDetectorConfig,
    TranscriptDelta,
    SpeechSegment,
    create_streaming_pipeline,
    CHANGE_DETECTOR_CONFIG,
    VAD_SAMPLE_RATE,
    VAD_WINDOW_SIZE,
    VAD_THRESHOLD,
    MIN_SPEECH_DURATION_MS,
    MIN_SILENCE_DURATION_MS,
    MIN_SPEECH_ENERGY_THRESHOLD,
    SEGMENT_OVERLAP_TAIL_MS,
    SPEAKER_SIMILARITY_THRESHOLD,
    MAX_SPEAKERS,
    SPEAKER_TOP_GAP_THRESHOLD,
    SPEAKER_SHORT_AUDIO_THRESHOLD_MS,
    SPEAKER_STRICT_GAP_THRESHOLD,
    DENOISE_ENABLED,
    DENOISE_BACKEND,
)
from app.asr.enhanced_engine import (
    EnhancedRecognitionEngine,
    SpeakerEnrollment,
    SpeakerMatch,
    EnhancedIdentificationResult,
    RecognitionMethod,
    AudioAugmentor,
    SpeakerModelTrainer,
)
from app.asr.audio_denoiser import (
    AudioDenoiser,
    RNNoiseDenoiser,
    get_denoiser,
    denoise_audio,
)
from app.asr.realtime_session import RealtimeSessionStore, get_store

__all__ = [
    # model_manager
    "ModelManager",
    "get_model_manager",
    "SpeakerEmbeddingExtractor",
    # vad_asr_pipeline
    "StreamingPipeline",
    "StreamingVAD",
    "StreamingASR",
    "StreamingSpeakerRecognition",
    "StreamingChangeDetectorConfig",
    "TranscriptDelta",
    "SpeechSegment",
    "create_streaming_pipeline",
    "CHANGE_DETECTOR_CONFIG",
    "VAD_SAMPLE_RATE",
    "VAD_WINDOW_SIZE",
    "VAD_THRESHOLD",
    "MIN_SPEECH_DURATION_MS",
    "MIN_SILENCE_DURATION_MS",
    "MIN_SPEECH_ENERGY_THRESHOLD",
    "SEGMENT_OVERLAP_TAIL_MS",
    "SPEAKER_SIMILARITY_THRESHOLD",
    "MAX_SPEAKERS",
    "SPEAKER_TOP_GAP_THRESHOLD",
    "SPEAKER_SHORT_AUDIO_THRESHOLD_MS",
    "SPEAKER_STRICT_GAP_THRESHOLD",
    "DENOISE_ENABLED",
    "DENOISE_BACKEND",
    # enhanced_engine
    "EnhancedRecognitionEngine",
    "SpeakerEnrollment",
    "SpeakerMatch",
    "EnhancedIdentificationResult",
    "RecognitionMethod",
    "AudioAugmentor",
    "SpeakerModelTrainer",
    # audio_denoiser
    "AudioDenoiser",
    "RNNoiseDenoiser",
    "get_denoiser",
    "denoise_audio",
    # realtime_session
    "RealtimeSessionStore",
    "get_store",
]
