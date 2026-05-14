# 系统架构

## 整体架构图

```mermaid
graph TB
    subgraph Frontend["前端 (React + Vite)"]
        UI[Web UI]
        WS_Client[WebSocket Client]
    end

    subgraph Nginx["Nginx 反向代理"]
        Proxy[nginx:80]
    end

    subgraph Backend["后端网关 (FastAPI)"]
        API[REST API :8000]
        WS_Server[WebSocket Server]
        Celery[Celery Worker]
    end

    subgraph Infra["基础设施"]
        PG[(PostgreSQL)]
        Redis[(Redis)]
    end

    subgraph ModelServices["模型服务"]
        ASR[ASR Service :8001]
        Diar[Diarization :8002]
        LLM[LLM Summary :8003]
    end

    subgraph Storage["文件存储"]
        LocalFS[本地文件系统]
    end

    UI --> Proxy
    WS_Client --> Proxy
    Proxy -->|/api/*| API
    Proxy -->|/ws/*| WS_Server
    Proxy -->|/| UI

    API --> PG
    API --> Redis
    API --> Celery
    WS_Server --> Redis

    Celery -->|HTTP| ASR
    Celery -->|HTTP| Diar
    Celery -->|HTTP| LLM
    Celery --> PG
    Celery --> LocalFS

    API --> LocalFS
```

## 数据流

### 实时转写流程

```mermaid
sequenceDiagram
    participant FE as 前端
    participant API as 后端 API
    participant WS as WebSocket
    participant Celery as Celery Worker
    participant ASR as ASR 服务
    participant Diar as 说话人分离

    FE->>API: 音频 chunk 上传
    API->>API: 存储音频文件
    API->>Celery: 触发异步转写任务
    Celery->>ASR: 发送音频 chunk
    ASR-->>Celery: 返回转写结果
    Celery->>Diar: 请求说话人识别
    Diar-->>Celery: 返回说话人标签
    Celery->>WS: 推送转写结果
    WS-->>FE: 实时显示转写
```

### 会议总结流程

```mermaid
sequenceDiagram
    participant Celery as Celery Worker
    participant LLM as LLM 总结服务
    participant DB as PostgreSQL
    participant WS as WebSocket

    Note over Celery: 每 2 分钟触发周期总结
    Celery->>LLM: 发送近期转写文本
    LLM-->>Celery: 返回阶段总结
    Celery->>DB: 存储阶段总结
    Celery->>WS: 推送阶段总结

    Note over Celery: 会议结束触发最终总结
    Celery->>LLM: 发送全部转写文本
    LLM-->>Celery: 返回最终总结 + Action Items
    Celery->>DB: 存储最终总结
    Celery->>WS: 推送最终总结
```

## 部署架构

### 开发环境

全部服务通过 Docker Compose 启动，单机运行，stub 模式。

### 生产环境

建议部署方案：
- Kubernetes 集群部署
- PostgreSQL 主从 + 连接池
- Redis Sentinel / Cluster
- Nginx Ingress Controller
- 模型服务 GPU 节点独立部署
