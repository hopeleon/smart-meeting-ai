"""
InsightEye speaker_database shim for smart-meeting-ai

StreamingPipeline imports from app.speaker_database:
- SpeakerDatabase

We re-export from smart-meeting-ai's speaker_db_service.
"""

from app.services.speaker_db_service import SpeakerDatabase

__all__ = ["SpeakerDatabase"]
