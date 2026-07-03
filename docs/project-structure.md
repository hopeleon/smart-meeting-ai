# 目录规范

更新时间：2026-07-03

当前仓库已经按“只保留可运行源码和必要文档”的原则清理。旧版整包、历史 `.bak_*`、测试语料、临时音频、日志产物、旧 Flutter App、前端依赖缓存已删除。

## 1. 当前顶层目录

```text
smart-meeting-ai/
├── backend/                 # FastAPI 后端代码和本地模型
├── frontend/                # React + TypeScript 前端代码与 dist
├── meetingsummary/          # 会议总结模块
├── docs/                    # 项目文档
├── deploy/                  # 部署配置
├── models/                  # 轻量模型占位或共享模型目录
├── logs/                    # 运行日志目录，内容不入库
├── Makefile                 # 常用命令封装
├── docker-compose.yml       # Docker 开发环境
├── docker-compose.prod.yml  # Docker 生产环境配置
├── start-local.sh           # 本地启动脚本
├── README.md                # 项目入口
├── .env.example             # 环境变量示例
└── .gitignore               # 忽略规则
```

## 2. 后端目录

```text
backend/
├── app/
│   ├── api/                 # 会议主服务 REST/WebSocket 路由
│   ├── laoji/               # 老记轻量服务入口和 API 路由
│   ├── asr/                 # ASR 流式处理、音频清洗、模型适配
│   ├── services/            # 业务服务，含会议、总结、日程解析
│   ├── models/              # 数据库模型
│   ├── schemas/             # Pydantic Schema
│   └── workers/             # 异步任务相关代码
├── models/                  # FunASR、声纹等本地模型，当前运行依赖，保留
├── qwen_asr_service/        # Qwen ASR 独立服务
├── .venv/                   # 当前 Python 运行环境，保留
├── local.db                 # 本地 SQLite 数据库
└── requirements*.txt        # Python 依赖
```

已清理：

- `backend/app/audio_files/`：历史上传音频，约 6.4G
- `backend/data/`：评测生成数据
- `backend/docker-wheels/`：离线 wheel 缓存
- `backend/summaries/`：历史总结输出
- `backend/.venv_new/`：重复虚拟环境
- `backend/test_*.py`、注册脚本、临时生成脚本
- 所有 `__pycache__/`

## 3. 前端目录

```text
frontend/
├── dist/                    # 当前 HTTPS 代理默认静态目录，保留
├── public/                  # 静态资源
├── src/                     # 前端源码
├── serve_with_proxy.py      # 本地 HTTPS 静态服务与 API/WS 代理
├── package.json             # 前端依赖声明
├── package-lock.json        # 依赖锁定
└── vite.config.ts           # Vite 配置
```

已清理：

- `frontend/node_modules/`：依赖缓存，可通过 `npm install` 恢复
- 前端日志和临时服务日志

注意：当前线上调试依赖 `frontend/dist/`，因此暂时保留。修改前端源码后，需要重新安装依赖并构建。

## 4. 文档目录

```text
docs/
├── README.md                                      # 文档入口
├── meeting-feature-spec.md                       # 会议功能说明
├── laoji-feature-spec.md                         # 老记功能说明
├── laoji-examples.json                           # 老记输入输出样例
├── project-structure.md                          # 当前目录规范
├── runbook.md                                    # 运行与排障手册
├── architecture.md                               # 架构说明
├── api-contracts.md                              # API 契约
├── development-guide.md                          # 开发指南
├── integration-guide.md                          # 集成指南
├── SETUP_GUIDE.md                                # 环境设置
├── 实时语音识别接口-企业对接说明.md              # 企业实时 ASR 对接说明
└── reports/                                      # 历史评估报告
```

## 5. 当前保留原则

保留：

- 当前运行需要的源码、模型、虚拟环境、数据库和 dist
- 会议与老记的产品文档、接口文档、运行文档
- 部署配置和启动脚本

删除：

- 历史备份和旧版整包
- 大体积测试语料和生成数据
- 临时上传音频、历史日志和输出产物
- 可重新生成的依赖缓存、构建缓存和 Python 缓存
- 与当前会议/老记运行无关的旧 App 工程

## 6. 后续新增文件规则

- 新产品文档放入 `docs/`。
- 评估报告放入 `docs/reports/`，但不要把大体积音频或语料放入仓库根目录。
- 临时音频和测试数据放到 `tmp/` 或外部数据盘，验证后及时删除。
- 不再在源码目录保留 `.bak_*` 文件；需要版本回退时使用 Git。
- 运行日志统一输出到 `logs/`，日志内容不入库。
