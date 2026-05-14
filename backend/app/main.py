import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 本地模式自动建表
    if settings.ENV == "local":
        from app.database import engine
        from app.models import Base
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        os.makedirs(settings.AUDIO_STORAGE_PATH, exist_ok=True)
        print("✓ 本地模式：SQLite 数据库已就绪，表已自动创建")
    yield


app = FastAPI(
    title="智能会议记录平台 API",
    description="会议音频处理与总结系统后端网关",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
origins = json.loads(settings.CORS_ORIGINS)
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
    return {"status": "ok", "env": settings.ENV}
