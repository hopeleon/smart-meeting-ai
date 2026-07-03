# Smart Meeting AI

智能会议记录与“老记”一句话待办项目。

当前仓库只保留运行和开发必须内容：会议记录、老记、会议总结模块、部署配置和文档。历史备份、旧版整包、测试语料、临时音频、日志和构建依赖缓存已清理。

## 当前入口

| 模块 | 地址 | 说明 |
| --- | --- | --- |
| 会议记录 | `https://localhost:5179` | 主会议系统前端 |
| 老记 | `https://localhost:5181/laoji` | 独立老记页面 |
| 会议后端 | `http://127.0.0.1:8020` | 主 FastAPI 服务 |
| 老记后端 | `http://127.0.0.1:8035` | 轻量 FastAPI 服务，只挂载老记 API |

通过 VS Code Remote SSH 使用时，需要在“端口”面板转发 `5179`、`5181`、`8020`、`8035`。

## 文档导航

建议从 [docs/README.md](docs/README.md) 开始阅读。

重点文档：

- [会议记录功能说明](docs/meeting-feature-spec.md)
- [老记功能说明](docs/laoji-feature-spec.md)
- [运行与排障手册](docs/runbook.md)
- [目录规范](docs/project-structure.md)
- [API 契约](docs/api-contracts.md)

## 目录概览

```text
smart-meeting-ai/
├── backend/                 # FastAPI 后端，含会议主服务、老记轻量服务、本地模型
├── frontend/                # React + TypeScript 前端，含已构建 dist
├── meetingsummary/          # 会议总结与本地 LLM 调用模块
├── docs/                    # 产品、接口、运行和架构文档
├── deploy/                  # 部署配置
├── models/                  # 轻量模型占位或共享模型目录
├── logs/                    # 运行日志目录，内容不入库
├── Makefile                 # 常用命令
├── docker-compose*.yml      # 容器部署配置
└── start-local.sh           # 本地启动脚本
```

更详细的目录规则见 [docs/project-structure.md](docs/project-structure.md)。

## 当前工程状态

- 会议记录和老记在同一仓库内共存。
- 老记已拆出独立 `/api/laoji` 接口和 `app.laoji.main` 轻量后端入口。
- 老记前端可通过 `/laoji` 独立访问，适合后续包装成 Web App/PWA 或移动端调用接口。
- 会议侧推荐方向是 FunASR 保实时、Qwen 做句段修正和总结增强。
- 已删除旧版整包、历史备份、测试会议数据、Primewords 语料、旧 Flutter App、临时上传音频和缓存目录。

## 快速启动

当前服务器上的实用启动命令见 [docs/runbook.md](docs/runbook.md)。

注意：`frontend/node_modules` 已清理；如果需要重新构建前端，请先在 `frontend/` 下执行 `npm install`。
