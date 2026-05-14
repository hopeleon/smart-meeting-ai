"""
ASR 模型服务 - FastAPI 入口
==========================
当前为 STUB 实现，返回 mock 数据。
算法团队接入时，只需修改 interface.py 中的实现。
"""

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
import json

from app.interface import transcribe_stream, transcribe_file

app = FastAPI(title="ASR 语音识别服务", version="0.1.0")


@app.get("/health")
async def health():
    return {"status": "ok", "service": "asr"}


@app.post("/transcribe")
async def transcribe(request: Request):
    """
    文件转写接口。

    请求格式：
    {
        "audio_path": "/path/to/audio.wav",
        "meeting_id": "uuid"
    }

    响应格式：
    {
        "segments": [
            {
                "text": str,
                "start": float,
                "end": float,
                "speaker_id": str,
                "confidence": float
            }
        ]
    }
    """
    body = await request.json()
    audio_path = body.get("audio_path", "")
    segments = await transcribe_file(audio_path)
    return {"segments": segments}


@app.post("/transcribe/stream")
async def transcribe_stream_endpoint(request: Request):
    """
    流式转写接口。

    请求：PCM 音频二进制流
    响应：Server-Sent Events (SSE)
    """
    meeting_id = request.query_params.get("meeting_id", "")

    async def event_generator():
        body = await request.body()
        async for segment in transcribe_stream(body):
            yield f"data: {json.dumps(segment)}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.post("/segment")
async def segment_audio(request: Request):
    """
    语音分割接口（可选）。

    请求格式：
    {
        "audio_path": "/path/to/audio.wav"
    }
    """
    body = await request.json()
    # STUB: 返回 mock 分割结果
    return {
        "segments": [
            {"start": 0.0, "end": 5.2},
            {"start": 5.5, "end": 12.8},
            {"start": 13.0, "end": 20.5},
        ]
    }
