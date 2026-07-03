"""
老记独立 FastAPI 入口。

启动示例：
    cd backend
    ENV=local CORS_ORIGINS='["*"]' python3 -m uvicorn app.laoji.main:app --host 0.0.0.0 --port 8035

这个入口不会预热会议实时转录、声纹或 WhisperLiveKit 模型，只挂载老记接口。
"""

import ast
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from app.config import settings
from app.laoji.router import router as laoji_router


app = FastAPI(
    title="老记 API",
    description="一句话待办、轻量 ASR 与智能日程解析服务",
    version="0.1.0",
)

origins = ast.literal_eval(settings.CORS_ORIGINS)
if origins == ["*"]:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(laoji_router, prefix="/api/laoji", tags=["laoji"])


@app.get("/")
async def root():
    return {
        "message": "老记 API",
        "docs": "/docs",
        "health": "/health",
        "api": "/api/laoji",
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "env": settings.ENV,
        "service": "laoji",
    }


@app.get("/api/health")
async def api_health_check():
    return {
        "status": "ok",
        "env": settings.ENV,
        "service": "laoji",
    }
