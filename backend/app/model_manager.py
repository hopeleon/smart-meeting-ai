"""
InsightEye model_manager shim for smart-meeting-ai

StreamingPipeline imports from app.model_manager:
- SpeakerEmbeddingExtractor (used by VAD and pipeline)
- ModelManager (used by create_streaming_pipeline)

We re-export from our local ModelManager which uses smart-meeting-ai's settings.
"""

from app.asr.model_manager import (
    ModelManager,
    SpeakerEmbeddingExtractor,
    get_model_manager,
)

__all__ = ["ModelManager", "SpeakerEmbeddingExtractor", "get_model_manager"]
