# Smart Meeting AI

智能会议音频处理与总结系统 — 支持实时语音转写、说话人识别、自动会议总结。

## 快速本地启动

只需 3 步，无需 Docker、无需 PostgreSQL、无需 Redis：

```bash
# 1. 克隆项目
git clone https://github.com/hopeleon/smart-meeting-ai.git
cd smart-meeting-ai

# 2. 一键启动
bash start-local.sh

# 3. 访问
#    前端界面：http://localhost:5173
#    API 文档：http://localhost:8000/docs
```

**前置要求：** Node.js 18+ 和 Python 3.11+（Redis 可选，没装也能跑）

脚本会自动：
- 检查 node / python 版本
- 创建 Python 虚拟环境并安装依赖
- npm install 前端依赖
- 用 SQLite 替代 PostgreSQL（数据存在 `backend/local.db`）
- Celery eager 模式（任务同步执行，不需要 Redis）
- 启动后端（端口 8000）和前端（端口 5173）
- Ctrl+C 全部退出

## Docker 启动（完整环境）

```bash
cp .env.example .env
make dev
```

访问 http://localhost（Nginx 反向代理统一入口）

```bash
make dev          # 启动开发环境
make down         # 停止所有服务
make logs         # 查看日志
make migrate      # 运行数据库迁移
make test         # 运行后端测试
make lint         # 代码检查
make build        # 构建生产镜像
```

## 项目架构

```
smart-meeting-ai/
├── start-local.sh          # 本地一键启动脚本
├── docker-compose.yml      # Docker 开发环境
├── Makefile                # 常用命令封装
│
├── backend/                # FastAPI 后端网关
│   ├── app/
│   │   ├── api/            # REST 路由 + WebSocket
│   │   ├── models/         # SQLAlchemy ORM 模型
│   │   ├── schemas/        # Pydantic 契约 Schema
│   │   ├── services/       # 业务逻辑（含 ASR/LLM stub）
│   │   ├── workers/        # Celery 异步任务
│   │   └── utils/          # 工具函数
│   └── requirements.txt    # Python 依赖
│
├── frontend/               # React 18 + TypeScript 前端
│   └── src/
│       ├── api/            # 后端 API 调用封装
│       ├── components/     # UI 组件
│       ├── pages/          # 页面
│       ├── stores/         # Zustand 状态管理
│       └── types/          # TypeScript 类型定义
│
├── model-services/         # 模型服务（算法团队实现）
│   ├── asr/                # ASR 语音识别
│   ├── diarization/        # 说话人分离
│   └── llm-summary/        # LLM 会议总结
│
├── docs/                   # 接口文档与架构说明
│   ├── architecture.md     # 系统架构图（Mermaid）
│   ├── api-contracts.md    # 各模块接口契约
│   └── integration-guide.md # 各团队接入指引
│
└── deploy/                 # 部署配置
    ├── k8s/                # K8s manifests（预留）
    └── nginx/nginx.conf    # Nginx 反向代理
```

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | React 18 + TypeScript + Vite + Tailwind CSS + Zustand |
| 后端网关 | FastAPI (Python 3.11+) |
| 任务队列 | Celery + Redis（本地模式可跳过） |
| 实时通信 | WebSocket（FastAPI 原生） |
| 数据库 | PostgreSQL（Docker）/ SQLite（本地开发） |
| 文件存储 | 本地文件系统（预留 S3 接口） |
| 模型服务 | 独立 Python 服务，通过 HTTP 调用（stub 占位） |
| 部署 | Docker Compose + Nginx |

## 核心模块

| 模块 | 目录 | 负责团队 | 状态 |
|------|------|----------|------|
| 前端界面 | `frontend/` | 平台团队 | 框架搭建 |
| 后端网关 | `backend/` | 平台团队 | 框架搭建 |
| ASR 语音识别 | `model-services/asr/` | 算法团队 | Stub |
| 说话人分离 | `model-services/diarization/` | 算法团队 | Stub |
| LLM 总结 | `model-services/llm-summary/` | 算法团队 | Stub |

## API 概览

### REST API（`/api`）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/meetings` | 创建会议 |
| GET | `/api/meetings` | 会议列表 |
| GET | `/api/meetings/{id}` | 会议详情 |
| PATCH | `/api/meetings/{id}` | 更新会议 |
| DELETE | `/api/meetings/{id}` | 删除会议 |
| POST | `/api/meetings/{id}/audio` | 上传音频 |
| GET | `/api/meetings/{id}/transcripts` | 转写结果 |
| GET | `/api/meetings/{id}/summaries/period` | 阶段总结 |
| GET | `/api/meetings/{id}/summaries/final` | 最终总结 |

### WebSocket（`/ws`）

```
ws://host/ws/meeting/{meeting_id}
```

推送消息格式：
```json
{"type": "transcript", "data": {...}}
{"type": "period_summary", "data": {...}}
{"type": "meeting_status", "data": {"status": "recording"}}
```

完整 API 文档见 [docs/api-contracts.md](docs/api-contracts.md)

## 团队接入

各团队接入指南详见 [docs/integration-guide.md](docs/integration-guide.md)。

- **算法团队**：实现 `model-services/*/app/interface.py` 中的接口
- **前端团队**：基于 `frontend/src/api/` 和 `frontend/src/types/` 开发
- **部署团队**：参考 `docker-compose.yml` 和 `deploy/` 目录

## License

MIT
