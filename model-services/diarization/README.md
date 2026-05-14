# 说话人分离服务

## 接口说明

本服务提供说话人分离（Speaker Diarization）功能。

### 识别说话人

```
POST /diarize
Content-Type: application/json

{
  "audio_path": "/path/to/audio.wav",
  "num_speakers": 3  // 可选，不传则自动检测
}

Response:
{
  "segments": [
    {"speaker_id": "Speaker_1", "start": 0.0, "end": 5.0}
  ],
  "num_speakers_detected": 3
}
```

## 音频格式要求

- 采样率：16kHz
- 位深：16bit
- 声道：Mono
- 编码：PCM / WAV

## 接入指南

算法团队需实现 `app/interface.py` 中的函数，签名不可修改。
