import ast
import os
from contextlib import asynccontextmanager

# 允许在已有事件循环中嵌套执行 asyncio.run()
import nest_asyncio
nest_asyncio.apply()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from logging_config import setup_file_logging, setup_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 初始化文件日志系统（对齐 InsightEye）
    log_path = setup_file_logging()
    setup_logging()
    print(f"[启动] 日志同步写入: {log_path}", flush=True)
    print(f"[启动] 所有运行日志请查看 backend/logs 文件夹", flush=True)

    # 本地模式自动建表
    if settings.ENV == "local":
        from app.database import engine
        from app.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        os.makedirs(settings.AUDIO_STORAGE_PATH, exist_ok=True)
        print("✓ 本地模式：SQLite 数据库已就绪，表已自动创建")

    # 同步阻塞加载所有 ASR 模型（FunASR + Silero VAD + CAM++）
    # 等待完成后才接受请求，确保实时转写可用
    try:
        from app.asr.model_manager import get_model_manager
        import asyncio
        mm = get_model_manager()
        print("[LocalRealtimeServer] 正在初始化模型...", flush=True)
        await mm.initialize()
        print("[LocalRealtimeServer] 模型初始化完成", flush=True)
        print("[LocalRealtimeServer] ✓ FunASR / Silero VAD / CAM++ 已就绪", flush=True)
    except Exception as e:
        print(f"[LocalRealtimeServer] ⚠ ASR 模型加载失败: {e}", flush=True)
        print("[LocalRealtimeServer]   服务将继续启动，实时转写功能可能不可用", flush=True)

    yield


app = FastAPI(
    title="智能会议记录平台 API",
    description="会议音频处理与总结系统后端网关",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
origins = ast.literal_eval(settings.CORS_ORIGINS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# REST 路由（/api/*）
from app.api.router import api_router  # noqa: E402
app.include_router(api_router, prefix="/api")

# WebSocket 路由（/ws/*，不经过 /api 前缀）
from app.api.websocket import router as ws_router  # noqa: E402
app.include_router(ws_router, prefix="/ws")


@app.get("/health")
async def health_check():
    from app.asr.model_manager import get_model_manager
    mm = get_model_manager()
    return {
        "status": "ok",
        "env": settings.ENV,
        "models": {
            "funasr": mm.funasr_model is not None,
            "vad": mm.vad_model is not None,
            "campplus": mm.camp_model is not None,
            "campplus_en": mm.camp_en_model is not None,
        }
    }


@app.get("/api/health")
async def api_health_check():
    """API 前缀下的健康检查端点"""
    from app.asr.model_manager import get_model_manager
    mm = get_model_manager()
    return {
        "status": "ok",
        "env": settings.ENV,
        "models": {
            "funasr": mm.funasr_model is not None,
            "vad": mm.vad_model is not None,
            "campplus": mm.camp_model is not None,
            "campplus_en": mm.camp_en_model is not None,
        }
    }
