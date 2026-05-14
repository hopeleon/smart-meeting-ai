"""
ASR 异步任务
===========
Celery 任务：调用 ASR 服务进行语音转写。
"""

import asyncio
import uuid
from datetime import datetime

from app.workers.celery_app import celery_app


@celery_app.task(bind=True, name="asr.transcribe_file")
def transcribe_file_task(self, audio_path: str, meeting_id: str):
    """
    异步文件转写任务。

    TODO: 算法团队接入后，此任务会调用真实的 ASR 服务。
    当前为 stub 实现。
    """
    from app.services.asr_service import ASRService

    service = ASRService()

    # Celery 任务中使用 asyncio
    loop = asyncio.new_event_loop()
    try:
        segments = loop.run_until_complete(service.transcribe_file(audio_path, meeting_id))
        return {
            "meeting_id": meeting_id,
            "segments": [
                {
                    "id": str(s.id),
                    "speaker_id": s.speaker_id,
                    "speaker_label": s.speaker_label,
                    "text": s.text,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "confidence": s.confidence,
                }
                for s in segments
            ],
        }
    finally:
        loop.close()


@celery_app.task(bind=True, name="asr.transcribe_chunk")
def transcribe_chunk_task(self, chunk_path: str, meeting_id: str, chunk_index: int):
    """
    异步 chunk 转写任务（用于实时流式场景）。

    TODO: 算法团队接入后，此任务会调用真实的 ASR 流式接口。
    """
    from app.services.asr_service import ASRService

    service = ASRService()

    loop = asyncio.new_event_loop()
    try:
        segments = loop.run_until_complete(
            service.transcribe_file(chunk_path, meeting_id)
        )
        return {
            "meeting_id": meeting_id,
            "chunk_index": chunk_index,
            "segments": [
                {
                    "text": s.text,
                    "start_time": s.start_time,
                    "end_time": s.end_time,
                    "speaker_id": s.speaker_id,
                    "confidence": s.confidence,
                }
                for s in segments
            ],
        }
    finally:
        loop.close()
