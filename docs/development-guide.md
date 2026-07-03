# 智能会议记录平台 — 开发文档

> 本文档面向开发者，介绍项目的整体架构、核心流程、开发规范和部署指南。

---

## 目录

1. [项目概述](#1-项目概述)
2. [技术栈](#2-技术栈)
3. [项目结构](#3-项目结构)
4. [快速开始](#4-快速开始)
5. [核心业务流](#5-核心业务流)
6. [API 参考](#6-api-参考)
7. [离线处理管线](#7-离线处理管线)
8. [说话人识别](#8-说话人识别)
9. [LLM 摘要生成](#9-llm-摘要生成)
10. [数据库模型](#10-数据库模型)
11. [前端开发指南](#11-前端开发指南)
12. [后端开发指南](#12-后端开发指南)
13. [配置参考](#13-配置参考)
14. [常见问题](#14-常见问题)

---

## 1. 项目概述

智能会议记录平台是一个端到端的会议转录与摘要系统，支持：

- **离线处理**：上传已录制的音频文件（支持 WAV/MP3/M4A/OGG/WebM/FLAC），自动完成转录、说话人识别和摘要生成
- **说话人管理**：通过上传音频注册说话人声纹，自动匹配会议中的已知说话人
- **AI 摘要**：基于 Ollama 本地 LLM（默认 `qwen3:32b`）生成结构化摘要

核心技术栈：
- **ASR**：VibeVoice-ASR-HF（INT8 量化，~2GB 显存）+ CAM++ 声纹识别
- **LLM**：Ollama 本地推理（qwen3:32b 或其他 GGUF 模型）
- **前端**：React + TypeScript + Vite / Python 静态服务器（无需 Node.js）

---

## 2. 技术栈

### 后端

| 组件 | 技术 | 说明 |
|------|------|------|
| Web 框架 | FastAPI | 异步 API，支持 OpenAPI 自动文档 |
| ASGI 服务器 | Uvicorn | 生产环境建议用 gunicorn |
| 数据库 | SQLite（本地）/ PostgreSQL（生产） | SQLAlchemy async ORM |
| ASR 模型 | VibeVoice-ASR-HF（INT8 量化） | 端到端语音识别，~2GB 显存 |
| 声纹模型 | 3D-Speaker CAM++ | 说话人验证 |
| LLM | Ollama | 本地大模型推理（qwen3:32b 等） |

### 前端

| 组件 | 技术 | 说明 |
|------|------|------|
| 框架 | React 18+ | 组件化 UI |
| 语言 | TypeScript | 类型安全 |
| 构建工具 | Vite | 快速 HMR |
| 状态管理 | Zustand | 轻量级全局状态 |
| HTTP 客户端 | Axios | 带重试拦截器 |
| 路由 | React Router DOM | SPA 路由 |

---

## 3. 项目结构

```
smart-meeting-ai/
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── main.py           # FastAPI 应用入口，生命周期管理
│   │   ├── config.py         # Pydantic Settings，从 .env 加载
│   │   ├── database.py       # SQLAlchemy async engine + session
│   │   ├── model_manager.py  # 模型 shim，重导出 asr.model_manager
│   │   │
│   │   ├── api/              # 路由层
│   │   │   ├── router.py        # 路由聚合
│   │   │   ├── meetings.py      # 会议 CRUD + 音频上传
│   │   │   ├── transcript.py     # 转写结果查询
│   │   │   ├── summary.py       # 摘要生成 + 查询
│   │   │   └── speakers.py       # 说话人声纹管理
│   │   │
│   │   ├── models/            # SQLAlchemy ORM 模型
│   │   │   ├── meeting.py       # Meeting
│   │   │   ├── transcript.py    # TranscriptLine
│   │   │   └── summary.py       # PeriodSummary, FinalSummary
│   │   │
│   │   ├── schemas/           # Pydantic 请求/响应模型
│   │   │
│   │   ├── services/          # 业务逻辑层
│   │   │   ├── offline_pipeline.py      # 离线处理（VibeVoice + CAM++）
│   │   │   ├── offline_pipeline3.py    # 离线处理增强版（含 VibeVoice 卸载逻辑）
│   │   │   ├── speaker_db_service.py    # 声纹 SQLite 数据库
│   │   │   └── summary_service.py       # 摘要服务（调用 meetingsummary）
│   │   │
│   │   ├── asr/              # ASR 模型（CAM++ 声纹提取）
│   │   │   └── model_manager.py       # SpeakerEmbeddingExtractor
│   │   │
│   │   └── workers/          # Celery 异步任务
│   │       ├── celery_app.py
│   │       └── summary_tasks.py
│   │
│   ├── models/               # ML 模型文件（大型二进制文件，Git 忽略）
│   │   ├── vibevoice/       # VibeVoice-ASR-HF 模型（含 INT8 量化版本）
│   │   │   └── vibevoice-int8/   # INT8 量化版本（推荐，显存占用更小）
│   │   ├── campplus/        # CAM++ 中文/英文模型
│   │   ├── funasr/          # Paraformer 标点恢复模型
│   │   └── silero-vad/       # Silero VAD 模型
│   │
│   └── requirements.txt
│
├── frontend/                 # React 前端
│   ├── src/
│   │   ├── api/             # API 客户端
│   │   │   ├── client.ts       # Axios 实例（带重试拦截器）
│   │   │   ├── meetings.ts    # 会议 API
│   │   │   ├── speakers.ts    # 说话人 API
│   │   │   └── transcript.ts  # 转写 API
│   │   │
│   │   ├── pages/           # 页面组件
│   │   │   ├── HomePage.tsx            # 首页（会议列表）
│   │   │   ├── MeetingSummaryPage.tsx   # 会议摘要 + 音频上传
│   │   │   └── SpeakerDbPage.tsx       # 说话人管理
│   │   │
│   │   ├── components/      # 可复用组件
│   │   │   ├── audio/
│   │   │   │   └── AudioUploader.tsx    # 音频上传
│   │   │   └── summary/
│   │   │
│   │   ├── stores/          # Zustand 状态管理
│   │   │   └── meetingStore.ts
│   │   │
│   │   └── types/           # TypeScript 类型定义
│   │
│   ├── serve_with_proxy.py  # Python 静态文件服务器 + API 代理（无需 Node.js）
│   ├── vite.config.ts       # Vite 配置（含代理规则）
│   └── package.json
│
├── meetingsummary/           # 独立摘要生成包（Python CLI）
│   ├── main.py              # CLI 入口
│   ├── config.py            # 配置加载
│   ├── config.json          # Ollama 连接配置
│   ├── ollama_client.py     # LLM API 客户端（支持 Ollama/OpenAI兼容）
│   ├── json_parser.py       # 从 LLM 输出中提取 JSON
│   ├── map_reduce.py        # 长文本 Map-Reduce 处理
│   ├── semantic_splitter.py  # 语义分块
│   ├── evaluator.py          # 事实性评估
│   ├── completeness_check.py  # 完整性检查
│   ├── action_items.py       # 行动项提取
│   ├── verify.py             # 自检验证
│   ├── markdown_generator.py # Markdown 输出生成
│   └── prompts/             # LLM 系统提示词
│
├── .env                      # 环境变量配置
├── start-local.sh           # 本地一键启动脚本
├── Makefile                 # 开发命令（make dev, make build 等）
└── docs/                    # 文档
```

---

## 4. 快速开始

### 4.1 环境要求

- Python 3.11+
- Node.js 18+（可选，用于开发时热重载；生产使用 Python 静态服务器）
- **CUDA 12.4+**（如需 GPU 加速 ASR/声纹推理）
- Ollama（用于摘要生成 LLM 推理）

### 4.1.1 硬件推荐配置

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| GPU | NVIDIA GPU 8GB+ | RTX 4090 24GB / RTX 5090 32GB |
| 显存 | 8GB（仅 VibeVoice INT8） | 32GB（VibeVoice + qwen3:32b） |
| 内存 | 16GB | 32GB+ |
| 磁盘 | 50GB | 100GB+ |

**RTX 5090 特别说明**：Blackwell 架构 (sm_120) 需要 CUDA 12.8+。当前 PyTorch 2.6.0+cu124 最高支持 sm_90，VibeVoice 推理会自动回退到 CPU，**速度较慢**。建议使用 RTX 4090 或等 PyTorch 2.7 稳定版支持 sm_120。

### 4.1.2 Ollama 安装与配置

Ollama 安装在用户目录 `~/.local/bin/`，无需 sudo：

```bash
# 下载二进制
curl -fsSL https://github.com/ollama/ollama/releases/download/v0.24.0/ollama-linux-amd64.tar.zst -o /tmp/ollama.tar.zst

# 解压到用户目录
mkdir -p ~/.local
tar -xf /tmp/ollama.tar.zst -C ~/.local/

# 启动 Ollama 服务
~/.local/bin/ollama serve

# 拉取会议摘要 LLM 模型
~/.local/bin/ollama pull qwen3:32b    # 20GB，推荐（需要 32GB 显存）
~/.local/bin/ollama pull qwen3:8b     # 5.2GB，适合 8GB 显存
```

> **显存不够时**：VibeVoice 推理完成后会自动卸载（释放 ~15.5GB），然后再加载 qwen3:32b。32GB 显存可以同时容纳。

### 4.2 安装依赖

```bash
# 后端
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 激活虚拟环境快捷方式（后续命令都需要先执行这个）
source .venv/bin/activate

# 安装 PyTorch CUDA 版本（GPU 加速）
pip install torch==2.6.0+cu124 torchaudio==2.6.0+cu124 --index-url https://download.pytorch.org/whl/cu124

# 安装 ML 模型（首次运行自动下载，或手动放置到 backend/models/）

# 前端（可选，用于开发热重载）
cd frontend
npm install
```

> **前端说明**：开发时可用 `npm run dev`（Vite），生产环境使用 `frontend/serve_with_proxy.py`（Python 静态服务器 + API 代理，无需 Node.js）。

### 4.3 配置

复制 `.env` 文件并修改必要配置：

```bash
cp .env.example .env
```

> **Linux/Mac 用户**：确保 `.env` 中的路径为 Linux 格式（如 `/home/zhong/SMART-MEETING/...`），不要使用 Windows 路径（如 `F:/...`）。

关键配置项说明见[配置参考](#13-配置参考)。

### 4.4 启动

#### 一键启动（推荐）

```bash
./start-local.sh
```

> 如果 `start-local.sh` 提示找不到 `vite` 或 `npm` 命令，说明 Node.js 环境不可用。脚本会自动切换到 Python 静态服务器（`serve_with_proxy.py`）提供前端服务，无需 Node.js。

#### 手动启动

**1. 启动 Ollama（用于摘要生成）**

```bash
# 方式 A：后台运行
~/.local/bin/ollama serve &

# 方式 B：前台运行（便于查看日志）
~/.local/bin/ollama serve
```

**2. 启动后端**

```bash
cd backend && source .venv/bin/activate
ENV=local CORS_ORIGINS='["http://localhost","http://localhost:5173","http://127.0.0.1:5173"]' \
PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True' \
.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop asyncio
```

> 注意：`--loop asyncio` 是必需的，用于支持嵌套 `asyncio.run()` 调用。

**3. 启动前端**

```bash
# 方式 A：Python 静态服务器 + API 代理（无需 Node.js，推荐）
cd frontend
python3 serve_with_proxy.py --port 5173 --backend http://localhost:8000

# 方式 B：Vite 开发服务器（需要 Node.js）
cd frontend && npm run dev
```

访问 http://localhost:5173

#### 启动顺序说明

```
Ollama → 后端 → 前端
```

- **Ollama** 监听 `11434` 端口，提供 LLM 推理服务
- **后端** 监听 `8000` 端口，处理 ASR、声纹识别、API 请求
- **前端** 监听 `5173` 端口，提供 Web UI 并代理 API 请求到后端

### 4.5 构建前端

```bash
cd frontend
node --experimental-vm-modules ./node_modules/vite/bin/vite.js build
```

`start-local.sh` 已自动包含此步骤。

---

## 5. 核心业务流

```
┌─────────────────────────────────────────────────────┐
│                 音频处理完整流程                       │
└─────────────────────────────────────────────────────┘

用户上传音频
     │
     ▼
POST /api/meetings/{id}/upload
     │
     ├─ 保存文件到 backend/app/audio_files/
     ├─ 会议状态 → "processing"
     │
     ▼
ThreadPoolExecutor 启动独立线程
（非 FastAPI 事件循环，避免阻塞）
     │
     ▼
OfflinePipeline.process()
     │
     ├─ Step 1: VibeVoice-ASR 端到端推理
     │    输入: 音频 → 输出: [{speaker, text, start, end}, ...]
     │
     ├─ Step 2: 卸载 VibeVoice（释放 GPU 显存）
     │
     ├─ Step 3: 3D-Speaker CAM++ 声纹对比
     │    为每个片段提取 192 维声纹向量
     │    与已注册说话人比对（余弦相似度 ≥ 0.20）
     │
     ├─ Step 4: 片段合并（同一说话人、间隔 < 5s 合并）
     │
     ├─ Step 5: Ollama LLM 生成摘要
     │    调用 meetingsummary 包（Map-Reduce 处理长文本）
     │
     ▼
数据库持久化
├─ TranscriptLine 记录
├─ FinalSummary 记录
└─ 会议状态 → "completed"
     │
     ▼
前端轮询 /api/meetings/{id}/status
获取最新状态和总结数据
```

---

## 6. API 参考

### 6.1 会议管理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/meetings` | 创建会议 |
| GET | `/api/meetings` | 列出会议（分页） |
| GET | `/api/meetings/{id}` | 获取会议详情 |
| PATCH | `/api/meetings/{id}` | 更新会议 |
| DELETE | `/api/meetings/{id}` | 删除会议 |

### 6.2 音频处理

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/meetings/{id}/upload` | 上传音频，触发处理 |
| GET | `/api/meetings/{id}/status` | 查询处理状态 |

**上传响应**（立即返回）：

```json
{
  "status": "processing",
  "message": "音频文件上传成功，正在后台处理",
  "file_path": "/path/to/file.wav",
  "file_size": 1048576,
  "meeting_id": "uuid",
  "mode": "offline"
}
```

**状态响应**：

```json
{
  "meeting_id": "uuid",
  "status": "completed",   // created | processing | completed | failed
  "title": "会议标题",
  "updated_at": "2026-05-31T12:00:00"
}
```

### 6.3 转写结果

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/meetings/{id}/transcripts` | 获取转写结果（分页） |

### 6.4 摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/meetings/{id}/summaries/generate` | 触发摘要生成（Celery 任务） |
| GET | `/api/meetings/{id}/summaries/task/{task_id}` | 查询 Celery 任务状态 |
| GET | `/api/meetings/{id}/summaries/period` | 获取阶段摘要 |
| GET | `/api/meetings/{id}/summaries/final` | 获取最终摘要 |

### 6.5 说话人管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/speakers` | 列出所有说话人 |
| GET | `/api/speakers/stats` | 获取详细统计（含相似度分布） |
| GET | `/api/speakers/search?name=` | 按姓名搜索 |
| POST | `/api/speakers/register` | 注册新说话人声纹 |
| POST | `/api/speakers/supplement` | 补充说话人音频（增量更新声纹） |
| POST | `/api/speakers/delete` | 软删除说话人 |

### 6.6 健康检查

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/health` | 后端健康状态 |
| GET | `/api/health` | 同上（带 /api 前缀） |

---

## 7. 离线处理管线

### 7.1 管线概述

`OfflinePipeline`（`backend/app/services/offline_pipeline.py`）是离线处理的核心，负责：

1. **VibeVoice-ASR 端到端推理**：直接从音频得到带时间戳的文本片段
2. **声纹提取与比对**：使用 CAM++ 提取声纹向量，与已注册说话人匹配
3. **片段合并**：合并同一说话人的相邻片段
4. **摘要生成**：调用 `meetingsummary` 包生成结构化摘要

### 7.2 模型加载策略

```
应用启动时：
  不预加载任何 ASR 模型（节省内存、避免 GPU OOM）

首次上传音频时：
  1. 在独立线程中加载 VibeVoice INT8 + CAM++
  2. 执行 ASR 推理
  3. 立即卸载 VibeVoice（torch.cuda.empty_cache）—— 释放 ~15.5GB 显存
  4. CAM++ 保留在内存中（声纹对比频繁使用）
  5. LLM 摘要阶段：加载 qwen3:32b（~20GB）→ 完成后卸载
```

> **显存优化**：VibeVoice 推理完成后会自动卸载，释放显存给 LLM 使用。32GB 显存可同时运行 qwen3:32b（约 20GB）和 CAM++（忽略不计）。

### 7.3 VibeVoice 推理参数

```python
# VibeVoice INT8 量化模型路径（显存占用更小）
snapshot_dir = os.path.join(cache_dir, "vibevoice-int8")

# 最大推理时长 = 音频时长 + 600s（预留缓冲）
max_new_tokens = int(audio_duration_s) + 600

# 温度：0（确定性输出）
temperature = 0

# 返回格式：parsed（结构化 JSON）
return_format = "parsed"
```

### 7.4 声纹对比参数

```python
SIM_THRESHOLD = 0.20  # 余弦相似度阈值，>= 视为同一人

# 合并规则：同一说话人、相邻、间隔 < 5s
MERGE_GAP_MS = 5000
```

### 7.5 OOM 处理

VibeVoice 推理完成后的卸载逻辑（`offline_pipeline3.py`）：

```python
def _unload_vibevoice(self):
    """卸载 VibeVoice 模型，释放显存"""
    import gc, torch
    if self._vibevoice_model is not None:
        del self._vibevoice_model
        self._vibevoice_model = None
    if self._vibevoice_processor is not None:
        del self._vibevoice_processor
        self._vibevoice_processor = None
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    print(f"[Pipeline3] VibeVoice 已卸载, GPU free: {free:.1f} GB", flush=True)
```

当 GPU 显存不足以加载模型时，管线自动降级到 CPU 模式：

```python
try:
    model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
        snapshot_dir, device_map="cuda", torch_dtype=torch.float16
    )
except RuntimeError as e:
    if "out of memory" in str(e):
        torch.cuda.empty_cache()
        # 降级到 CPU
        model = VibeVoiceAsrForConditionalGeneration.from_pretrained(
            snapshot_dir, device_map="cpu", torch_dtype=torch.float32
        )
```

---

## 8. 说话人识别

### 8.1 声纹数据库

`speaker_db_service.py` 使用 SQLite 管理说话人声纹：

```
speaker_profiles
  - speaker_id (PK)
  - name
  - role
  - department
  - quality (音频质量评分)
  - is_active

speaker_embeddings
  - speaker_id (FK, PK)
  - embedding (192 维 float32, 768 bytes)
  - embedding_mean (均值更新)
  - embedding_count (样本数量)
```

### 8.2 注册说话人

1. 上传音频（WAV/MP3/M4A/OGG/WebM/FLAC）
2. ffmpeg 转换为 16kHz mono PCM
3. CAM++ 提取 192 维嵌入向量
4. 质量评估（能量、时长、样本数）
5. 存入 SQLite

### 8.3 增量更新（补充音频）

使用 **Welford 在线算法** 更新声纹向量：

```python
# 新均值 = 旧均值 + (新样本 - 旧均值) / 新样本数
new_mean = old_mean + (new_embedding - old_mean) / new_count
```

### 8.4 说话人识别引擎

**基础模式**（CAM++）：
- 直接余弦相似度
- Top-3 候选
- 阈值：0.65

**增强模式**：
- 级联匹配策略
- 动态阈值（均值 + 0.3 × 标准差）
- 多窗口投票（3 窗口，步长 0.25）
- 马氏距离过滤

---

## 9. LLM 摘要生成

### 9.1 meetingsummary 包

独立 Python CLI 包，不依赖 FastAPI，通过子进程调用。

### 9.2 LLM 模型选择

| 模型 | 大小 | 显存需求 | 适用场景 |
|------|------|---------|---------|
| **qwen3:32b** | ~20GB | 32GB | 推荐，摘要质量高（需要 RTX 5090 或类似 32GB 显存 GPU） |
| **qwen3:8b** | ~5.2GB | 10GB | 通用，8GB 显存可用 |
| **qwen2.5:14b** | ~8.8GB | 16GB | 中文能力强 |
| DeepSeek API | 云端 | 无本地需求 | 无 GPU 时的备选方案 |

> **显存说明**：由于 VibeVoice 推理完成后会自动卸载（释放 ~15.5GB），qwen3:32b（~20GB）可以在同一 GPU 上满血运行，无需同时加载两个大模型。

### 9.3 处理策略

| 文本长度 | 策略 |
|----------|------|
| < 5000 中文字符 | 直接摘要（单次 LLM 调用） |
| ≥ 5000 中文字符 | Map-Reduce（分块 → 事实提取 → 合并 → 最终摘要） |

### 9.3 Map-Reduce 流程

```
Map 阶段：
  文本分块（语义分块 或 按说话人分段，每块 ~15 句话）
       ↓
  每个块独立调用 LLM，提取「事实列表」（不摘要）
       ↓
合并去重：
  计算事实对相似度（80% 阈值）
  合并高度相似的事实
       ↓
Reduce 阶段：
  合并后的所有事实调用 LLM
  生成结构化摘要 {overview, key_decisions, action_items}
```

### 9.4 后处理（可选）

| 步骤 | 说明 |
|------|------|
| 自检验证 | LLM 自我检查一致性 |
| 事实性评估 | 与原始转写对比，输出准确率 |
| 完整性检查 | 确保覆盖所有重要话题 |
| 行动项提取 | 独立提取任务、负责人、优先级 |

### 9.5 输出格式

```json
{
  "meeting": {
    "summary": "会议整体摘要...",
    "topics_discussed": ["话题1", "话题2"]
  },
  "tldr": "一句话总结",
  "discussion_points": [
    { "title": "讨论标题", "summary": "讨论内容" }
  ],
  "decisions": [
    { "description": "决定描述" }
  ],
  "action_items": [
    {
      "task": "任务描述",
      "assignee": "负责人",
      "deadline": "截止日期",
      "priority": "P0"
    }
  ]
}
```

### 9.6 配置

修改 `meetingsummary/config.json`：

```json
{
  "ollama": {
    "base_url": "http://localhost:11434",
    "model": "qwen3:32b",
    "provider": "ollama",
    "api_key": "",
    "chat_path": "/v1/chat/completions"
  }
}
```

> **Ollama 启动命令**：`~/.local/bin/ollama serve`（后台运行）
>
> **模型路径**：`~/.ollama/models/`（Ollama 自动管理）

---

## 10. 数据库模型

### 10.1 Meeting

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) | UUID 主键 |
| title | VARCHAR(255) | 会议标题 |
| description | TEXT | 会议描述 |
| status | VARCHAR(20) | created / processing / completed / failed |
| participants | TEXT | JSON 数组，参会人列表 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### 10.2 TranscriptLine

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) | UUID 主键 |
| meeting_id | VARCHAR(36) | 索引，外键 |
| speaker_id | VARCHAR(50) | 说话人 ID |
| speaker_label | VARCHAR(100) | 说话人标签（姓名） |
| text | TEXT | 转写文本 |
| start_time | FLOAT | 开始时间（秒） |
| end_time | FLOAT | 结束时间（秒） |
| confidence | FLOAT | 置信度 |
| created_at | DATETIME | 创建时间 |

### 10.3 PeriodSummary

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) | UUID 主键 |
| meeting_id | VARCHAR(36) | 索引，外键 |
| period_start | FLOAT | 阶段开始时间 |
| period_end | FLOAT | 阶段结束时间 |
| bullet_points_json | TEXT | 要点 JSON 数组 |
| generated_at | DATETIME | 生成时间 |

### 10.4 FinalSummary

| 字段 | 类型 | 说明 |
|------|------|------|
| id | VARCHAR(36) | UUID 主键 |
| meeting_id | VARCHAR(36) | 索引，外键 |
| overview | TEXT | 会议概览 |
| key_decisions_json | TEXT | 决策 JSON 数组 |
| action_items_json | TEXT | 行动项 JSON 数组 |
| generated_at | DATETIME | 生成时间 |

---

## 11. 前端开发指南

### 11.1 目录结构

```
frontend/src/
├── api/                 # API 客户端（与后端通信）
│   ├── client.ts        # Axios 实例，所有 API 共用
│   ├── meetings.ts      # 会议相关 API
│   ├── speakers.ts      # 说话人 API
│   └── transcript.ts    # 转写 API
│
├── pages/               # 页面级组件
│   ├── HomePage.tsx            # 首页（会议列表）
│   ├── MeetingSummaryPage.tsx  # 会议摘要 + 音频上传
│   └── SpeakerDbPage.tsx      # 说话人管理
│
├── components/         # 可复用组件
│   ├── audio/
│   │   └── AudioUploader.tsx    # 音频上传
│   └── summary/
│
├── stores/             # Zustand 全局状态
│   └── meetingStore.ts
│
├── types/              # TypeScript 类型
│   └── meeting.ts    # Meeting、Transcript 等类型定义
│
├── App.tsx            # 路由配置
└── main.tsx           # React DOM 入口
```

### 11.2 添加新 API

1. 在 `src/api/` 下新建或编辑文件
2. 从 `client.ts` 导入 `apiClient`
3. 导出类型化函数

```typescript
// src/api/example.ts
import { apiClient } from './client'

export interface ExampleResponse {
  id: string
  name: string
}

export async function getExample(id: string): Promise<ExampleResponse> {
  const resp = await apiClient.get<ExampleResponse>(`/example/${id}`)
  return resp.data
}
```

### 11.3 Axios 配置说明

```typescript
// src/api/client.ts

export const apiClient = axios.create({
  baseURL: API_BASE,        // 默认 /api，生产环境可配置 VITE_API_BASE_URL
  timeout: 30000,           // 默认 30s
})

// 全局重试拦截器：5xx 错误和网络错误自动重试（指数退避）
apiClient.interceptors.response.use(
  (response) => response,
  async (error) => {
    // 重试逻辑...
  }
)
```

**注意**：上传音频等耗时长请求，`uploadAudioFile` 已单独设置 `timeout: 600000`（10 分钟）。

### 11.4 构建部署

```bash
# 开发
npm run dev

# 构建生产版本
npm run build
# 输出: dist/

# 预览构建结果
npm run preview
```

`start-local.sh` 已自动执行 `vite build`，无需手动构建。

---

## 12. 后端开发指南

### 12.1 添加新 API 路由

**步骤 1**：在 `app/api/` 下创建或编辑路由文件

```python
# app/api/example.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db

router = APIRouter()

@router.get("/example/{item_id}")
async def get_example(item_id: str, db: AsyncSession = Depends(get_db)):
    # ...
    return {"item_id": item_id}
```

**步骤 2**：在 `app/api/router.py` 中注册

```python
from app.api.example import router as example_router

api_router.include_router(example_router, prefix="/example", tags=["Example"])
```

### 12.2 添加数据库模型

**步骤 1**：创建模型

```python
# app/models/example.py
from sqlalchemy import Column, String, DateTime
from app.database import Base

class Example(Base):
    __tablename__ = "examples"
    id = Column(String(36), primary_key=True)
    name = Column(String(255), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
```

**步骤 2**：迁移数据库（在 `main.py` 中已自动创建）

```python
# main.py
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

### 12.3 后台任务处理

使用 `ThreadPoolExecutor` 处理 CPU/GPU 密集型任务：

```python
import concurrent.futures

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

def _run_bg(meeting_id: str, file_path: str):
    import nest_asyncio
    nest_asyncio.apply()
    asyncio.run(process_audio_in_background(meeting_id, file_path))

@router.post("/{meeting_id}/upload")
async def upload_audio(meeting_id: str, ...):
    # ...
    _executor.submit(_run_bg, meeting_id, str(file_path))
    return {"status": "processing"}
```

### 12.4 日志规范

```python
# 使用 flush=True 确保日志实时写入
print(f"[Pipeline] 阶段1完成", flush=True)

# 分级日志
import logging
logger = logging.getLogger(__name__)
logger.info("处理完成")
logger.warning("GPU 显存不足，切换到 CPU")
logger.error("处理失败", exc_info=True)
```

### 12.5 错误处理

```python
@router.post("/{meeting_id}/upload")
async def upload_audio(...):
    try:
        # 业务逻辑
        ...
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except PermissionError as e:
        raise HTTPException(status_code=403, detail="权限不足")
    except Exception as e:
        # 记录但不暴露内部细节
        logger.error(f"上传失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="服务器内部错误")
```

---

## 13. 配置参考

### 13.1 `.env` 关键配置

```bash
# ── 环境 ─────────────────────────────
ENV=local                         # local | production

# ── 数据库 ───────────────────────────
DATABASE_URL=sqlite+aiosqlite:///home/zhong/SMART-MEETING/smart-meeting-ai/backend/local.db

# ── Ollama LLM ──────────────────────
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:32b

# ── 前端 ───────────────────────────
VITE_API_BASE_URL=http://localhost:8000/api

# ── 本地模型路径 ───────────────────
VIBEVOICE_MODEL_DIR=/home/zhong/SMART-MEETING/smart-meeting-ai/backend/models/vibevoice
CAMPPLUS_MODEL_DIR=/home/zhong/SMART-MEETING/smart-meeting-ai/backend/models/campplus/zh-cn
LOCAL_DEVICE=cuda                 # cuda | cpu

# ── 显存优化 ───────────────────────
# 启用显存扩展段分配，减少 OOM（建议始终设置）
PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

# ── 说话人识别阈值 ─────────────────
SPEAKER_SIMILARITY_THRESHOLD=0.6  # 余弦相似度阈值
SPEAKER_MIN_CONFIDENCE=0.5        # 最小置信度
SPEAKER_VOTE_WINDOWS=3            # 多窗口投票数量

# ── CORS ───────────────────────────
CORS_ORIGINS=["http://localhost","http://localhost:5173","http://127.0.0.1:5173"]
```

### 13.2 Ollama 环境变量

```bash
# Ollama 安装路径（非默认 /usr/local/bin 时需要）
export PATH=$PATH:/home/zhong/.local/bin

# Ollama 监听地址（允许远程访问时设为 0.0.0.0）
export OLLAMA_HOST=0.0.0.0

# Ollama 模型存放路径（可选，默认 ~/.ollama/models/）
export OLLAMA_MODELS=/path/to/models
```

### 13.3 meetingsummary 配置

```json
// meetingsummary/config.json
{
  "ollama": {
    "base_url": "http://localhost:11434",
    "model": "qwen3:32b",
    "provider": "ollama",
    "api_key": "",
    "chat_path": "/v1/chat/completions"
  }
}
```

### 13.4 前端代理配置

**Vite 开发服务器**（`vite.config.ts`）：

```typescript
server: {
  proxy: {
    '/api': {
      target: 'http://127.0.0.1:8000',
      changeOrigin: true,
    },
  },
}
```

**Python 静态服务器**（`serve_with_proxy.py`，无需 Node.js）：

```bash
cd frontend
python3 serve_with_proxy.py --port 5173 --backend http://localhost:8000
```

---

## 14. 常见问题

### Q: 前端黑屏/白屏

**原因**：dist 目录中的 JS 文件与源代码不同步（旧的编译文件）。

**解决方法**：

```bash
cd frontend
node --experimental-vm-modules ./node_modules/vite/bin/vite.js build
```

`start-local.sh` 已自动执行此步骤。

### Q: 上传音频后前端显示错误但后端仍在处理

**原因**：axios 默认超时设置较短，在后端处理完成前就报错了。

**解决方法**：已在 `client.ts` 中将默认超时改为 30 秒，`uploadAudioFile` 显式设置 10 分钟超时。

### Q: VibeVoice 模型加载后 GPU 显存不足

**原因**：VibeVoice-ASR-HF 模型约 16GB（FP16），加上 CAM++ 和其他模型，总显存需求超过可用量。

**解决方法**：`OfflinePipeline3` 已内置 VibeVoice 卸载逻辑——ASR 推理完成后自动释放显存（约 15.5GB），然后再加载 qwen3:32b 做摘要。32GB 显存可以同时容纳 qwen3:32b（~20GB）和 CAM++。

**如果是 INT8 模型**：显存占用更小（约 2GB），更容易与 qwen3:32b 共存。

### Q: RTX 5090 (Blackwell sm_120) 上 VibeVoice 推理慢

**原因**：PyTorch 2.6.0+cu124 最高支持 CUDA sm_90（RTX 4090），RTX 5090 的 Blackwell sm_120 内核不受支持，推理回退到 CPU。

**解决方法**：
1. **推荐**：使用 RTX 4090（24GB）或类似 GPU，PyTorch 完全支持
2. **备选**：等 PyTorch 2.7+ 稳定版（预计支持 sm_120）
3. **尝鲜**：安装 PyTorch nightly 版（`--pre`），可能支持更新的 CUDA

> 注意：即使在 CPU 模式下推理，流程逻辑（ASR → 声纹 → 卸载 → LLM 摘要）是完全正确的，只是速度较慢。

### Q: 后端启动时报错 `asyncio.run() cannot be called from a running event loop`

**原因**：FastAPI/Uvicorn 使用自己的事件循环，pipeline 中直接调用 `asyncio.run()` 会冲突。

**解决方法**：`backend/app/main.py` 中已使用 `nest_asyncio.apply()` 修复：

```python
import nest_asyncio
try:
    nest_asyncio.apply()
except ValueError:
    pass  # Already patched
```

同时后端启动时需要加 `--loop asyncio`：

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop asyncio
```

### Q: Ollama 摘要生成超时

**原因**：subprocess 有超时限制。

**解决方法**：Ollama API 请求本身无超时（`timeout=0`），但 subprocess 执行有超时限制。可以通过修改 `meetingsummary/ollama_client.py` 的 `timeout` 参数调整。

### Q: 说话人识别准确率低

**原因**：注册音频质量差（噪声大、时长短）。

**解决方法**：确保注册音频 ≥ 10 秒、噪声低、音量适中。`SpeakerDbPage` 中有质量评分显示。

### Q: 如何添加新的 ASR 模型？

1. 下载模型到 `backend/models/`
2. 在 `.env` 中配置模型路径
3. 在 `offline_pipeline.py` 中添加新的处理管线
4. 可选：在 `asr/model_manager.py` 中添加新的声纹提取逻辑

### Q: 如何切换 LLM 模型？

修改 `meetingsummary/config.json` 中的 `model` 字段：

```json
{
  "ollama": {
    "model": "llama3:70b"
  }
}
```

然后执行：

```bash
~/.local/bin/ollama pull llama3:70b
```
