"""
说话人分离服务 - FastAPI 入口
============================
当前为 STUB 实现，返回 mock 数据。
算法团队接入时，只需修改 interface.py 中的实现。
"""

from fastapi import FastAPI, Request

from app.interface import diarize

app = FastAPI(title="说话人分离服务", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "diarization"}


@app.post("/diarize")
async def diarize_endpoint(request: Request):
    """
    说话人分离接口。

    请求格式：
    {
        "audio_path": "/path/to/audio.wav",
        "num_speakers": 3  // 可选
    }

    响应格式：
    {
        "segments": [
            {"speaker_id": "Speaker_1", "start": 0.0, "end": 5.0}
        ],
        "num_speakers_detected": 3
    }
    """
    body = await request.json()
    audio_path = body.get("audio_path", "")
    num_speakers = body.get("num_speakers")
    result = await diarize(audio_path, num_speakers)
    return result
