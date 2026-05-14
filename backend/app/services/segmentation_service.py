"""
语音分割服务接口层
================
当前为 STUB 实现，返回 mock 数据。
算法团队接入时，修改 _call_segmentation_model() 方法即可，无需改动上层调用。

接入要求：
- model-services/segmentation 逻辑集成在 ASR 服务中
- 或独立部署，通过 HTTP 调用
"""

import httpx

from app.config import settings


class SegmentationService:
    def __init__(self):
        self.model_url = settings.ASR_SERVICE_URL  # 复用 ASR 服务地址

    async def segment_audio(self, audio_path: str) -> list[dict]:
        """
        语音分割接口。
        将长音频按语音活动检测（VAD）分割为多个片段。

        TODO: 算法团队替换此处实现

        Returns:
            [{"start": 0.0, "end": 5.2}, {"start": 5.5, "end": 10.8}, ...]
        """
        # STUB: 尝试调用远程服务
        try:
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    f"{self.model_url}/segment",
                    json={"audio_path": audio_path},
                )
                resp.raise_for_status()
                return resp.json().get("segments", [])
        except Exception:
            # 降级返回 mock
            return self._mock_segments()

    def _mock_segments(self) -> list[dict]:
        """Mock 数据，仅用于开发阶段联调"""
        return [
            {"start": 0.0, "end": 5.2},
            {"start": 5.5, "end": 12.8},
            {"start": 13.0, "end": 20.5},
            {"start": 21.0, "end": 30.0},
        ]
