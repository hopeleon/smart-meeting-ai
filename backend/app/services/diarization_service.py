"""
说话人分离服务接口层
==================
当前为 STUB 实现，返回 mock 数据。
算法团队接入时，修改 _call_diarization_model() 方法即可，无需改动上层调用。

接入要求：
- model-services/diarization 服务需在 8002 端口提供 HTTP API
- 接口规范见 docs/api-contracts.md
"""

import httpx

from app.config import settings


class DiarizationService:
    def __init__(self):
        self.model_url = settings.DIARIZATION_SERVICE_URL  # 默认 http://diarization-stub:8002

    async def diarize(self, audio_path: str, num_speakers: int | None = None) -> dict:
        """
        说话人分离接口。
        输入音频文件路径，返回各时间段的说话人标签。

        TODO: 算法团队替换此处实现

        Returns:
            {
                "segments": [{"speaker_id": "Speaker_1", "start": 0.0, "end": 5.0}],
                "num_speakers_detected": 3
            }
        """
        # STUB: 调用 model-services/diarization
        try:
            async with httpx.AsyncClient(timeout=300) as client:
                resp = await client.post(
                    f"{self.model_url}/diarize",
                    json={"audio_path": audio_path, "num_speakers": num_speakers},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception:
            # 降级返回 mock
            return self._mock_diarization()

    def _mock_diarization(self) -> dict:
        """Mock 数据，仅用于开发阶段联调"""
        return {
            "segments": [
                {"speaker_id": "Speaker_1", "start": 0.0, "end": 5.0},
                {"speaker_id": "Speaker_2", "start": 5.0, "end": 10.0},
                {"speaker_id": "Speaker_1", "start": 10.0, "end": 15.0},
                {"speaker_id": "Speaker_3", "start": 15.0, "end": 20.0},
            ],
            "num_speakers_detected": 3,
        }
