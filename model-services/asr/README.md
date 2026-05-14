# ASR 语音识别服务

## 接口说明

本服务提供语音转文字功能，包含两种模式：

### 1. 文件转写

```
POST /transcribe
Content-Type: application/json

{
  "audio_path": "/path/to/audio.wav",
  "meeting_id": "uuid"
}

Response:
{
  "segments": [
    {
      "text": "转写文本",
      "start": 0.0,
      "end": 3.5,
      "speaker_id": "Speaker_1",
      "confidence": 0.95
    }
  ]
}
```

### 2. 流式转写

```
POST /transcribe/stream
Content-Type: application/octet-stream
Query: meeting_id=uuid

Response: Server-Sent Events (SSE)
data: {"text": "...", "start": 0.0, "end": 1.5, "speaker_id": "Speaker_1", "confidence": 0.95}
```

## 音频格式要求

- 采样率：16kHz
- 位深：16bit
- 声道：Mono
- 编码：PCM / WAV

## 接入指南

算法团队需实现 `app/interface.py` 中的函数，签名不可修改。

### 本地测试

```bash
pip install -r requirements.txt
uvicorn app.main:app --port 8001
curl -X POST http://localhost:8001/transcribe \
  -H "Content-Type: application/json" \
  -d '{"audio_path": "/path/to/test.wav", "meeting_id": "test"}'
```
