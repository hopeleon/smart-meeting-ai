"""
ASR 服务接口层
============
当前为 STUB 实现，返回 mock 数据。
算法团队接入时，修改 _call_asr_model() 方法即可，无需改动上层调用。

接入要求：
- model-services/asr 服务需在 8001 端口提供 HTTP API
- 接口规范见 docs/api-contracts.md
"""

import uuid
from typing import AsyncGenerator
from datetime import datetime

import httpx

from app.schemas.transcript import TranscriptSegment
from app.config import settings


class ASRService:
    def __init__(self):
        self.model_url = settings.ASR_SERVICE_URL  # 默认 http://asr-stub:8001

    async def transcribe_stream(
        self, audio_chunk: bytes, meeting_id: str
    ) -> AsyncGenerator[TranscriptSegment, None]:
        """
        实时流式转写接口。
        接收音频 chunk，yield 转写结果。

        TODO: 算法团队替换此处实现
        当前返回 mock 数据用于联调。
        """
        mock_segments = self._mock_transcripts(meeting_id)
        for seg in mock_segments:
            yield seg

    async def transcribe_file(self, audio_path: str, meeting_id: str) -> list[TranscriptSegment]:
        """
        离线文件转写接口（会议结束后全量处理）。

        TODO: 算法团队替换此处实现
        """
        # STUB: 调用 model-services/asr
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{self.model_url}/transcribe",
                    json={"audio_path": audio_path, "meeting_id": meeting_id},
                )
                resp.raise_for_status()
                data = resp.json()
                return [
                    TranscriptSegment(
                        id=str(uuid.uuid4()),
                        meeting_id=meeting_id,
                        speaker_id=s.get("speaker_id", "Speaker_1"),
                        speaker_label=s.get("speaker_label", "发言人A"),
                        text=s["text"],
                        start_time=s["start"],
                        end_time=s["end"],
                        confidence=s.get("confidence", 0.9),
                        created_at=datetime.utcnow(),
                    )
                    for s in data.get("segments", [])
                ]
        except Exception:
            # 降级返回 mock
            return self._mock_transcripts(meeting_id)

    def _mock_transcripts(self, meeting_id: str) -> list[TranscriptSegment]:
        """Mock 数据，仅用于开发阶段联调"""
        mock_data = [
            {"speaker_id": "Speaker_1", "speaker_label": "发言人A", "text": "大家好，今天我们讨论一下Q2的产品规划。", "start": 0.0, "end": 3.5, "confidence": 0.95},
            {"speaker_id": "Speaker_2", "speaker_label": "发言人B", "text": "好的，我先汇报一下上个季度的完成情况。", "start": 3.5, "end": 7.0, "confidence": 0.92},
            {"speaker_id": "Speaker_1", "speaker_label": "发言人A", "text": "请说，我们都在听。", "start": 7.0, "end": 8.5, "confidence": 0.97},
            {"speaker_id": "Speaker_2", "speaker_label": "发言人B", "text": "上季度我们完成了三个核心模块的开发，用户反馈整体不错。", "start": 8.5, "end": 13.0, "confidence": 0.93},
            {"speaker_id": "Speaker_3", "speaker_label": "发言人C", "text": "我想补充一下，性能优化方面还有提升空间。", "start": 13.0, "end": 16.5, "confidence": 0.91},
        ]
        return [
            TranscriptSegment(
                id=str(uuid.uuid4()),
                meeting_id=meeting_id,
                speaker_id=d["speaker_id"],
                speaker_label=d["speaker_label"],
                text=d["text"],
                start_time=d["start"],
                end_time=d["end"],
                confidence=d["confidence"],
                created_at=datetime.utcnow(),
            )
            for d in mock_data
        ]
