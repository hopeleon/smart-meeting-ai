# 各团队接入指南

## 本地开发（所有团队）

最简单的方式，不需要 Docker、PostgreSQL、Redis：

```bash
bash start-local.sh
```

- 后端：http://localhost:8000（API 文档：http://localhost:8000/docs）
- 前端：http://localhost:5173
- 数据库：SQLite（`backend/local.db`）
- 任务队列：Celery eager 模式（同步执行）

---

## ASR 语音识别团队

### 目标

替换 `model-services/asr/app/interface.py` 中的 stub 实现。

### 接入步骤

1. **了解接口规范**

   阅读 `model-services/asr/app/interface.py`，其中定义了两个核心函数：
   - `transcribe_stream(audio_chunk) -> AsyncGenerator[dict, None]`：流式转写
   - `transcribe_file(audio_path) -> list[dict]`：文件转写

2. **音频格式要求**

   - 采样率：16kHz
   - 位深：16bit
   - 声道：Mono
   - 编码：PCM / WAV

3. **输出格式**

   每个转写片段必须返回以下字段：
   ```python
   {
       "text": str,           # 转写文本
       "start": float,        # 开始时间（秒）
       "end": float,          # 结束时间（秒）
       "speaker_id": str,     # 说话人 ID（如 "Speaker_1"）
       "confidence": float    # 置信度 0.0-1.0
   }
   ```

4. **安装依赖**

   在 `model-services/asr/requirements.txt` 中添加模型依赖。

5. **本地测试**

   ```bash
   cd model-services/asr
   pip install -r requirements.txt
   uvicorn app.main:app --port 8001
   curl -X POST http://localhost:8001/transcribe \
     -H "Content-Type: application/json" \
     -d '{"audio_path": "/path/to/test.wav", "meeting_id": "test"}'
   ```

6. **注意事项**

   - 请勿修改函数签名
   - 可以在 `interface.py` 中添加辅助函数和类
   - 模型文件放在 `model-services/asr/models/` 目录
   - 确保服务启动时间不超过 30 秒

---

## 说话人分离团队

### 目标

替换 `model-services/diarization/app/interface.py` 中的 stub 实现。

### 接入步骤

1. **接口函数**

   ```python
   async def diarize(audio_path: str, num_speakers: int | None = None) -> dict
   ```

2. **输出格式**

   ```python
   {
       "segments": [
           {"speaker_id": "Speaker_1", "start": 0.0, "end": 5.0}
       ],
       "num_speakers_detected": 3
   }
   ```

3. **音频要求**：同 ASR（16kHz, 16bit, mono WAV）

---

## LLM 总结团队

### 目标

替换 `model-services/llm-summary/app/interface.py` 中的 stub 实现。

### 接入步骤

1. **接口函数**

   - `summarize_period(transcript_lines) -> dict`：阶段总结
   - `summarize_final(all_lines, period_summaries) -> dict`：最终总结

2. **输出格式**

   阶段总结：
   ```python
   {"bullet_points": ["要点1", "要点2", "要点3"]}
   ```

   最终总结：
   ```python
   {
       "overview": "会议概述",
       "key_decisions": ["决策1"],
       "action_items": [
           {"content": "任务描述", "assignee": "负责人", "due_date": "2026-05-20"}
       ]
   }
   ```

3. **模型部署**

   - 本地模型：在 requirements.txt 中添加推理框架依赖
   - 外部 API：在 `.env` 中配置 API Key

---

## 前端团队

### 目标

基于现有框架开发前端页面和组件。

### 开发环境

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`

### Mock 模式

设置 `VITE_MOCK_MODE=true`（默认开启），所有 API 调用返回 mock 数据。

关闭 mock 模式连接真实后端：
```bash
VITE_MOCK_MODE=false npm run dev
```

### 关键目录

```
src/
├── types/meeting.ts      # 与后端 Schema 对齐的 TypeScript 类型
├── api/                  # API 调用封装
│   ├── client.ts         # axios 实例（支持 mock 切换）
│   ├── meetings.ts       # 会议 API
│   └── websocket.ts      # WebSocket 连接管理
├── stores/               # Zustand 全局状态
├── pages/                # 页面组件
└── components/           # 通用组件
```

### 添加新页面

1. 在 `src/pages/` 创建页面组件
2. 在 `src/App.tsx` 中添加路由
3. 如需新 API，在 `src/api/` 中添加调用函数

---

## 部署团队

### 开发环境

```bash
# 1. 克隆代码
git clone <repo-url>
cd meeting-platform

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 修改数据库密码等配置

# 3. 启动
make dev

# 4. 运行数据库迁移
make migrate
```

### 环境变量说明

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `POSTGRES_USER` | 数据库用户 | meeting |
| `POSTGRES_PASSWORD` | 数据库密码 | meeting123 |
| `POSTGRES_DB` | 数据库名 | meeting_platform |
| `DATABASE_URL` | 数据库连接串 | - |
| `REDIS_URL` | Redis 地址 | redis://redis:6379/0 |
| `ASR_SERVICE_URL` | ASR 服务地址 | http://asr-stub:8001 |
| `DIARIZATION_SERVICE_URL` | 分离服务地址 | http://diarization-stub:8002 |
| `LLM_SUMMARY_SERVICE_URL` | 总结服务地址 | http://llm-summary-stub:8003 |
| `SECRET_KEY` | 应用密钥 | - |
| `CORS_ORIGINS` | 允许的跨域来源 | - |

### 生产部署

```bash
# 使用生产配置
make build
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

生产环境需要额外配置：
- 修改 `SECRET_KEY` 为强随机字符串
- 配置外部 PostgreSQL 和 Redis
- 配置 HTTPS（Nginx SSL）
- 配置持久化存储卷
