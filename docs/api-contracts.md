# API 接口契约

## 健康检查

```
GET /health
```

**Response (200):**
```json
{
  "status": "ok",
  "env": "local"
}
```

## Swagger 文档

启动后端后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

---

## 后端 REST API

### 会议管理

#### 创建会议

```
POST /api/meetings
```

**Request:**
```json
{
  "title": "产品需求评审会",
  "description": "讨论 Q2 产品规划",
  "participants": ["张三", "李四"]
}
```

**Response (201):**
```json
{
  "id": "uuid",
  "title": "产品需求评审会",
  "description": "讨论 Q2 产品规划",
  "status": "created",
  "participants": ["张三", "李四"],
  "created_at": "2026-05-12T10:00:00Z",
  "updated_at": "2026-05-12T10:00:00Z"
}
```

#### 获取会议列表

```
GET /api/meetings?page=1&size=20
```

**Response (200):**
```json
{
  "items": [Meeting],
  "total": 100,
  "page": 1,
  "size": 20
}
```

#### 获取会议详情

```
GET /api/meetings/{meeting_id}
```

#### 更新会议状态

```
PATCH /api/meetings/{meeting_id}
```

**Request:**
```json
{
  "status": "recording"
}
```

#### 删除会议

```
DELETE /api/meetings/{meeting_id}
```

---

### 音频管理

#### 上传音频文件

```
POST /api/meetings/{meeting_id}/audio
Content-Type: multipart/form-data
```

**Form Fields:**
- `file`: 音频文件 (WAV, 16kHz, 16bit, mono)

**Response (200):**
```json
{
  "audio_id": "uuid",
  "filename": "meeting_recording.wav",
  "duration": 3600.0,
  "size_bytes": 115200000
}
```

#### 流式上传音频 chunk

```
POST /api/meetings/{meeting_id}/audio/chunk
Content-Type: application/octet-stream
```

**Headers:**
- `X-Chunk-Index`: chunk 序号
- `X-Is-Last`: 是否最后一个 chunk

---

### 转写结果

#### 获取转写结果

```
GET /api/meetings/{meeting_id}/transcripts?offset=0&limit=100
```

**Response (200):**
```json
{
  "items": [
    {
      "id": "uuid",
      "meeting_id": "uuid",
      "speaker_id": "Speaker_1",
      "speaker_label": "发言人A",
      "text": "大家好，今天我们讨论一下 Q2 的产品规划",
      "start_time": 0.0,
      "end_time": 3.5,
      "confidence": 0.95,
      "created_at": "2026-05-12T10:00:00Z"
    }
  ],
  "total": 500
}
```

---

### 总结

#### 获取阶段总结列表

```
GET /api/meetings/{meeting_id}/summaries/period
```

**Response (200):**
```json
{
  "items": [
    {
      "id": "uuid",
      "meeting_id": "uuid",
      "period_start": 0.0,
      "period_end": 120.0,
      "bullet_points": [
        "讨论了 Q2 产品路线图",
        "确定了三个核心功能模块",
        "分配了前端开发任务"
      ],
      "generated_at": "2026-05-12T10:02:00Z"
    }
  ]
}
```

#### 获取最终总结

```
GET /api/meetings/{meeting_id}/summaries/final
```

**Response (200):**
```json
{
  "id": "uuid",
  "meeting_id": "uuid",
  "overview": "本次会议讨论了 Q2 产品规划，确定了三个核心功能模块...",
  "key_decisions": [
    "采用 React 作为前端框架",
    "后端使用 FastAPI",
    "预计 6 月底完成第一版"
  ],
  "action_items": [
    {
      "id": "uuid",
      "content": "完成前端原型设计",
      "assignee": "张三",
      "due_date": "2026-05-20",
      "status": "pending"
    }
  ],
  "generated_at": "2026-05-12T11:00:00Z"
}
```

---

## WebSocket 消息格式

### 连接地址

```
ws://host/ws/meeting/{meeting_id}
```

### 消息类型

#### 转写结果推送

```json
{
  "type": "transcript",
  "data": {
    "id": "uuid",
    "meeting_id": "uuid",
    "speaker_id": "Speaker_1",
    "speaker_label": "发言人A",
    "text": "转写的文本内容",
    "start_time": 10.0,
    "end_time": 13.5,
    "confidence": 0.92,
    "created_at": "2026-05-12T10:00:10Z"
  }
}
```

#### 阶段总结推送

```json
{
  "type": "period_summary",
  "data": {
    "id": "uuid",
    "meeting_id": "uuid",
    "period_start": 0.0,
    "period_end": 120.0,
    "bullet_points": ["要点1", "要点2"],
    "generated_at": "2026-05-12T10:02:00Z"
  }
}
```

#### 会议状态变更

```json
{
  "type": "meeting_status",
  "data": {
    "status": "recording"
  }
}
```

状态值: `created` | `recording` | `paused` | `ended`

---

## 模型服务 HTTP 接口

### ASR 服务 (port 8001)

#### 流式转写

```
POST /transcribe/stream
Content-Type: application/octet-stream
```

**Query Params:** `meeting_id`

**Response:** Server-Sent Events (SSE)
```
data: {"text": "你好", "start": 0.0, "end": 1.5, "speaker_id": "Speaker_1", "confidence": 0.95}
```

#### 文件转写

```
POST /transcribe
Content-Type: application/json
```

**Request:**
```json
{
  "audio_path": "/path/to/audio.wav",
  "meeting_id": "uuid"
}
```

**Response:**
```json
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

### 说话人分离服务 (port 8002)

#### 识别说话人

```
POST /diarize
Content-Type: application/json
```

**Request:**
```json
{
  "audio_path": "/path/to/audio.wav",
  "num_speakers": 3
}
```

**Response:**
```json
{
  "segments": [
    {
      "speaker_id": "Speaker_1",
      "start": 0.0,
      "end": 5.0
    }
  ],
  "num_speakers_detected": 3
}
```

### LLM 总结服务 (port 8003)

#### 阶段总结

```
POST /summarize/period
Content-Type: application/json
```

**Request:**
```json
{
  "meeting_id": "uuid",
  "transcript_lines": [
    {"speaker": "发言人A", "text": "...", "start": 0.0, "end": 3.5}
  ]
}
```

**Response:**
```json
{
  "bullet_points": ["要点1", "要点2", "要点3"]
}
```

#### 最终总结

```
POST /summarize/final
Content-Type: application/json
```

**Request:**
```json
{
  "meeting_id": "uuid",
  "all_transcript_lines": [...],
  "period_summaries": [...]
}
```

**Response:**
```json
{
  "overview": "会议概述...",
  "key_decisions": ["决策1", "决策2"],
  "action_items": [
    {
      "content": "完成原型设计",
      "assignee": "张三",
      "due_date": "2026-05-20"
    }
  ]
}
```

---

## 音频格式要求

| 参数 | 值 |
|------|-----|
| 采样率 | 16kHz |
| 位深 | 16bit |
| 声道 | Mono (单声道) |
| 编码 | PCM / WAV |
| chunk 大小 | 4096 samples (~256ms) |

---

## 错误码规范

| HTTP 状态码 | 场景 |
|------------|------|
| 400 | 请求参数错误 |
| 404 | 资源不存在 |
| 422 | 数据验证失败 |
| 500 | 服务器内部错误 |
| 503 | 模型服务不可用 |

**错误响应格式:**
```json
{
  "detail": "错误描述信息"
}
```
