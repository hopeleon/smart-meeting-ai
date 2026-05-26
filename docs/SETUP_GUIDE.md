# Smart Meeting AI - 新机器配置指南

本文档说明如何在全新的 Windows / Linux / macOS 机器上快速配置并运行 Smart Meeting AI 项目。

---

## 目录

1. [环境要求](#1-环境要求)
2. [快速安装（推荐）](#2-快速安装推荐)
3. [详细手动配置](#3-详细手动配置)
4. [模型下载](#4-模型下载)
5. [LLM 服务配置](#5-llm-服务配置)
6. [启动项目](#6-启动项目)
7. [Docker 部署（可选）](#7-docker-部署可选)
8. [常见问题](#8-常见问题)

---

## 1. 环境要求

### 硬件

| 组件 | 最低配置 | 推荐配置 |
|------|---------|---------|
| CPU | 4 核 | 8 核+ |
| 内存 | 8 GB | 16 GB+ |
| GPU | 无（CPU 推理可用） | NVIDIA GPU + CUDA 12.1 |
| 磁盘 | 10 GB 可用 | 20 GB+ SSD |

### 软件依赖

| 软件 | 版本 | 用途 |
|------|------|------|
| **Node.js** | 18+ | 前端构建 |
| **Python** | 3.11+ | 后端服务 |
| **Git** | 最新 | 代码管理 |
| **CUDA 12.1** | 12.1+ | GPU 加速（可选） |
| **Ollama** | 最新 | 本地 LLM（可选） |

---

## 2. 快速安装（推荐）

### Windows

1. **安装 Python 和 Node.js**

   下载并安装以下软件（安装时勾选 "Add to PATH"）:
   - Python 3.11+: https://www.python.org/downloads/windows/
   - Node.js 18+: https://nodejs.org/

2. **安装 Ollama**（用于会议总结生成）

   下载地址: https://ollama.com/download

3. **下载项目代码**

   ```bash
   git clone https://github.com/your-repo/smart-meeting-ai.git
   cd smart-meeting-ai
   ```

4. **运行启动脚本**

   ```bash
   .\start.bat
   ```

   脚本会自动：
   - 检查 Python 和 Node.js 环境
   - 安装后端 Python 依赖
   - 安装前端 npm 依赖
   - 启动后端服务（端口 8000）
   - 启动前端服务（端口 5173）
   - 在浏览器中打开应用

### Linux / macOS

```bash
git clone https://github.com/your-repo/smart-meeting-ai.git
cd smart-meeting-ai
chmod +x start-local.sh
./start-local.sh
```

---

## 3. 详细手动配置

### 3.1 Python 环境配置

#### 使用 uv（推荐，高性能）

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 或 Windows PowerShell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# 在项目目录创建虚拟环境
cd smart-meeting-ai/backend
uv venv .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 安装依赖
uv pip install -r requirements.txt
```

#### 使用 pip（传统方式）

```bash
cd smart-meeting-ai/backend

# 创建虚拟环境
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

> **注意：** `requirements.txt` 包含 PyTorch 和 FunASR，完整安装约 3-5 GB，请确保网络通畅。

### 3.2 Node.js 和前端配置

```bash
cd smart-meeting-ai/frontend

# 安装依赖
npm install

# 验证安装成功
npm run dev -- --version
```

### 3.3 后端配置

复制环境变量模板并编辑：

```bash
# 在项目根目录
copy .env.example .env
```

编辑 `.env` 文件（可选，有默认值）：

```env
# 本地开发使用 SQLite，无需修改数据库配置
ENV=local

# CORS 配置（允许的前端地址）
CORS_ORIGINS=["http://localhost","http://localhost:5173"]

# 模型路径（使用默认路径可跳过）
LOCAL_MODEL_DIR=F:\smart-meeting-ai\backend\models
LOCAL_DEVICE=cuda  # 改为 cpu 如果没有 GPU
```

---

## 4. 模型下载

项目使用以下机器学习模型，首次运行会自动下载（需要网络）：

| 模型 | 用途 | 大小 | 下载位置 |
|------|------|------|---------|
| **FunASR (Paraformer-large)** | 语音识别 | ~2 GB | `backend/models/funasr/` |
| **CAM++ 中文** | 声纹识别（说话人分离） | ~200 MB | `backend/models/campplus/zh-cn/` |
| **CAM++ 英文** | 声纹识别（英文） | ~200 MB | `backend/models/campplus/en/` |
| **Silero VAD** | 语音活动检测 | ~10 MB | `backend/models/silero-vad/` |

### 自动下载

模型在首次运行后端时自动从 HuggingFace 下载。如果下载失败或速度慢，可手动下载：

```bash
# FunASR 模型
# 下载地址: https://huggingface.co/FunAudioLLM/Paraformer-zh-ShiYi80M

# CAM++ 模型
# 下载地址: https://huggingface.co/n Alessandro/3dspeaker_speaker-campplus-cn-common

# Silero VAD（自动缓存）
# 模型会自动下载到 ~/.cache/torch/hub/nvidia
```

### 手动放置模型文件

如果已有所需的模型文件，手动放置到对应目录：

```
backend/models/
├── funasr/
│   ├── model.pt           ← FunASR 权重
│   ├── am.mvn
│   ├── config.yaml
│   └── ...
├── campplus/
│   ├── zh-cn/
│   │   ├── campplus_cn_common.bin   ← 中文声纹模型
│   │   └── config.yaml
│   └── en/
│       ├── campplus_voxceleb.bin    ← 英文声纹模型
│       └── configuration.json
└── silero-vad/
    └── snakers4_silero-vad_master/
        └── src/silero_vad/
            └── silero_vad.jit       ← VAD 模型
```

### GPU 配置（可选）

如果使用 NVIDIA GPU，需要安装 CUDA 12.1 和 cuDNN：

1. 安装 [CUDA Toolkit 12.1](https://developer.nvidia.com/cuda-downloads)
2. 安装 [cuDNN 8.x](https://developer.nvidia.com/cudnn)
3. 将 CUDA 添加到 PATH：

   ```bash
   # Windows PowerShell（管理员）
   [Environment]::SetEnvironmentVariable(
       "Path", $env:Path + ";C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.1\bin",
       "Machine"
   )
   ```

4. 验证 GPU 可用：

   ```python
   python -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
   ```

---

## 5. LLM 服务配置

会议总结生成需要 LLM 服务。可选择以下方式：

### 方式 A：使用 Ollama（推荐，本地免费）

1. **安装 Ollama**

   ```bash
   # Windows/macOS: 下载安装包
   # https://ollama.com/download

   # Linux/macOS 终端:
   curl -fsSL https://ollama.com/install.sh | sh
   ```

2. **启动 Ollama 服务**

   ```bash
   ollama serve
   ```

3. **下载模型**

   ```bash
   # 推荐使用 qwen 或 deepseek 模型（中文能力强）
   ollama pull qwen3:8b
   # 或
   ollama pull deepseek-r1:8b
   ```

4. **配置项目使用 Ollama**

   编辑 `meetingsummary/config.json`：

   ```json
   {
       "ollama": {
           "base_url": "http://localhost:11434",
           "model": "qwen3:8b",
           "provider": "ollama",
           "api_key": "",
           "chat_path": "/v1/chat/completions"
       }
   }
   ```

### 方式 B：使用云端 API（无需本地配置）

编辑 `meetingsummary/config.json`，使用 OpenAI 或兼容 API：

```json
{
    "ollama": {
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "provider": "openai",
        "api_key": "sk-your-api-key-here",
        "chat_path": "/v1/chat/completions"
    }
}
```

支持的 provider: `ollama`、`openai`、`azure`、`groq`、`anthropic`

### 验证 LLM 服务

```bash
curl http://localhost:11434/api/tags
# 应返回已下载的模型列表
```

---

## 6. 启动项目

### 开发模式（推荐）

#### Windows

双击运行 `start.bat`，或在终端中：

```bash
cd smart-meeting-ai
.\start.bat
```

#### Linux/macOS

```bash
cd smart-meeting-ai
./start-local.sh
```

#### 手动启动（不依赖启动脚本）

**终端 1 - 后端：**

```bash
cd smart-meeting-ai/backend

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/macOS:
source .venv/bin/activate

# 设置环境变量
# Windows:
set ENV=local
# Linux/macOS:
export ENV=local

# 启动服务
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**终端 2 - 前端：**

```bash
cd smart-meeting-ai/frontend
npm run dev
```

**终端 3 - Ollama（如果使用）：**

```bash
ollama serve
```

### 服务地址

| 服务 | 地址 |
|------|------|
| 前端应用 | http://localhost:5173 |
| 后端 API | http://localhost:8000 |
| API 文档 | http://localhost:8000/docs |
| Ollama | http://localhost:11434 |

---

## 7. Docker 部署（可选）

使用 Docker 一键部署全套服务（推荐用于生产或不想手动配置的环境）。

### 前置条件

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)（Windows/macOS）
- 或 Docker Engine + Docker Compose（Linux）

### 步骤

1. **复制环境变量**

   ```bash
   copy .env.example .env
   ```

2. **编辑 .env**

   ```env
   ENV=production
   DATABASE_URL=postgresql+asyncpg://meeting:meeting123@postgres:5432/meeting_platform
   REDIS_URL=redis://redis:6379/0
   ```

3. **构建并启动**

   ```bash
   docker-compose up --build
   ```

   首次构建约需 15-30 分钟（需要下载 CUDA 基础镜像和编译模型）。

4. **访问服务**

   - 前端: http://localhost:80
   - 后端 API: http://localhost:8000/docs
   - API 文档: http://localhost:8000/docs

### Docker 服务说明

```
┌─────────────────────────────────────────────┐
│                  nginx (port 80)            │
│           反向代理 + 静态文件服务            │
├──────────────┬──────────────────────────────┤
│  frontend    │        backend               │
│  (Vite :5173)│    (FastAPI :8000)          │
├──────────────┴──────────────┬────────────────┤
│        postgres :5432      │  redis :6379   │
│        数据库              │  任务队列       │
├────────────────────────────┼────────────────┤
│      celery-worker         │   ollama       │
│      异步任务处理          │   LLM 服务     │
└────────────────────────────┴────────────────┘
```

### 停止 Docker 服务

```bash
docker-compose down
# 保留数据：
docker-compose down -v
```

---

## 8. 常见问题

### Q1: `torch` 导入报错 "DLL load failed"

**原因:** Windows 上 PyTorch 的 CUDA DLL 找不到。

**解决方法：**
```bash
pip uninstall torch torchvision torchaudio
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu121
```

### Q2: FunASR 模型下载失败

**解决方法：**
```bash
# 设置 HuggingFace 镜像
export HF_ENDPOINT=https://hf-mirror.com
# 或在 Python 中：
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
```

### Q3: 后端启动报 "No module named 'app'"

**原因:** 当前目录不对。

**解决方法：**
```bash
cd smart-meeting-ai/backend
# 确保激活了虚拟环境
python -m uvicorn app.main:app --reload
```

### Q4: 声纹识别不工作

**原因:** CAM++ 模型文件未找到。

**解决方法：** 确认 `backend/models/campplus/zh-cn/campplus_cn_common.bin` 文件存在。首次运行后端会自动下载。

### Q5: 阶段总结 / 最终总结无法生成

**检查项：**
1. Ollama 是否运行: `curl http://localhost:11434/api/tags`
2. `meetingsummary/config.json` 是否存在且配置正确
3. `meetingsummary/` 目录是否在项目根目录
4. 查看后端日志中的 `[SummaryTask-subprocess]` 输出

### Q6: SQLite 数据库错误

**原因:** 数据库文件损坏或迁移问题。

**解决方法：**
```bash
cd smart-meeting-ai/backend
# 删除旧数据库（会丢失历史数据）
del local.db
# 重启后端，会自动重建
```

### Q7: 前端端口 5173 被占用

**解决方法：**
```bash
# 修改前端端口（在 frontend/vite.config.ts）：
export default defineConfig({
  server: { port: 5174 }
})
```

### Q8: GPU 显存不足（OOM）

**解决方法：**
1. 将 `.env` 中的 `LOCAL_DEVICE=cpu` 改为使用 CPU 推理
2. 或减少批处理大小
3. 或使用更小的模型

---

## 环境检查清单

启动前确认以下项目：

```
□ Python 3.11+ 已安装（python --version）
□ Node.js 18+ 已安装（node --version）
□ pip 可用（pip --version）
□ Ollama 已安装并运行（ollama serve）
□ 所需模型已下载（qwen3:8b 或其他 LLM）
□ meetingsummary/config.json 已配置
□ backend 依赖已安装
□ frontend 依赖已安装
□ 端口 8000、5173 未被占用
```

---

*最后更新: 2026-05-26*
