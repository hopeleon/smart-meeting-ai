"""
智能会议记录平台 - FastAPI 后端入口

本地开发启动方式：
    cd backend
    ENV=local CORS_ORIGINS='["http://localhost","http://localhost:5179"]' \
        python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8020 --access-log
"""

import ast
import logging
import os
import sys
from contextlib import asynccontextmanager

# Allow nested asyncio.run() in already-running event loops
import nest_asyncio
try:
    nest_asyncio.apply()
except ValueError:
    pass  # Already patched (e.g. when running with uvloop)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from logging_config import setup_file_logging, setup_logging

# 启动 Uvicorn 时加上 --access-log 才显示访问日志（默认静默）
#   python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8020 --access-log
_access_log = "--access-log" in sys.argv

if not _access_log:
    # 静默 Uvicorn 访问日志（不打印 "INFO: GET /api/... 200 OK"）
    logging.getLogger("uvicorn.access").disabled = True

# 保留 uvicorn.error 和 uvicorn.asgi 的 INFO 日志（有用）
logging.getLogger("uvicorn.error").setLevel(logging.INFO)

# 静默第三方库的冗余 INFO 日志（WhisperLiveKit、faster-whisper、transformers 等）
for _lib in [
    "whisperlivekit",
    "whisperlivekit.simul_whisper",
    "whisperlivekit.core",
    "faster_whisper",
    "transformers",
    "onnxruntime",
]:
    _lg = logging.getLogger(_lib)
    _lg.setLevel(logging.WARNING)

# ── GPU 显存配置：禁用 PyTorch CUDA 内存池 ─────────────────────
os.environ["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:False"
# ── cuDNN 版本兼容性修复 ───────────────────────────────────────
# PyTorch 编译用 CUDA 12.8 + cuDNN 9.2，但系统驱动是 CUDA 13.2（cuDNN 9.1）。
# 强制 PyTorch 使用 venv 自带的 CUDA runtime，避免 cuDNN 版本冲突。
_vendor_cuda = os.path.join(
    os.path.dirname(os.path.dirname(__file__)), ".venv", "lib", "python3.11",
    "site-packages", "nvidia", "cuda_runtime", "lib"
)
if os.path.exists(_vendor_cuda):
    os.environ["LD_LIBRARY_PATH"] = _vendor_cuda + ":" + os.environ.get("LD_LIBRARY_PATH", "")

import torch
# ── NeMo + torch2.11 兼容补丁 ───────────────────────────────────
# torch 2.11 的 TorchScript 编译器在编译 NeMo 的 @torch.jit.script 函数时段错误。
# 把 torch.jit.script 改成 no-op（被装饰函数本就是合法 Python，按 eager 执行，仅无 JIT 加速）。
# 注意：不能用 PYTORCH_JIT=0——那会连带破坏 torch.jit.load(.jit)，导致 Silero VAD 加载失败。
_orig_jit_script = torch.jit.script
def _noop_jit_script(obj=None, *a, **k):
    return (lambda f: f) if obj is None else obj
torch.jit.script = _noop_jit_script
if torch.cuda.is_available():
    torch.cuda.empty_cache()
    torch.cuda.set_device(0)
    print(f"[GPU] {torch.cuda.get_device_name(0)}, "
          f"free: {torch.cuda.mem_get_info()[0]/1024**3:.1f} GB")


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

        # 清理残留的 processing 状态（防止上次崩溃导致的状态卡住）
        try:
            from sqlalchemy import text
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT id, title FROM meetings WHERE status = 'processing'")
                )
                stuck_meetings = result.fetchall()
                if stuck_meetings:
                    print(f"[启动] 发现 {len(stuck_meetings)} 个卡在 processing 状态的会议，正在清理...")
                    await conn.execute(
                        text("UPDATE meetings SET status = 'ended' WHERE status = 'processing'")
                    )
                    await conn.commit()
                    for m in stuck_meetings:
                        meeting_id, title = m[0], m[1]
                        print(f"  - {meeting_id[:20]}... ({title})")
                        print("    已标记为 ended；总结将在用户进入总结页或手动触发时生成", flush=True)
        except Exception as e:
            print(f"[启动] 清理残留状态失败: {e}")

    # 实时转录模型预热：Silero VAD + FunASR + CAM++ + 增强引擎
    # 在 lifespan 中同步预热，确保 WebSocket 连接时模型已就绪
    try:
        from app.asr.model_manager import get_model_manager
        mm = get_model_manager()
        if not mm.is_initialized():
            print("[启动] 正在预热实时转录模型（VAD + ASR + CAM++），首次加载可能需要 30-60 秒...", flush=True)
            await mm.initialize()
            print("[启动] 实时转录模型预热完成", flush=True)
        else:
            print("[启动] 实时转录模型已就绪", flush=True)
    except Exception as e:
        print(f"[启动] 实时转录模型预热失败（不影响离线模式）: {e}", flush=True)

    # WhisperLiveKit 模型预加载（首次加载约需 30-90 秒）。
    # 在独立线程中异步预加载，不阻塞 FastAPI startup - 这样即使下载慢，
    # 实时模式（FunASR）也能立即可用。
    def _warm_whisperlivekit():
        try:
            from whisperlivekit import TranscriptionEngine
            from app.api import whisper_ws as _whisper_ws_module
            print("[启动-async] 正在后台预加载 WhisperLiveKit Faster-Whisper 模型...", flush=True)
            import os as _os
            _whisper_ws_module._whisper_engine = TranscriptionEngine(
                model_size=_os.getenv("WHISPER_MODEL_SIZE", "large-v3"),
                lan="zh",
                min_chunk_size=float(_os.getenv("WHISPER_MIN_CHUNK_SEC", "2.0")),
                diarization=True,
                device="cuda",
                compute_type="float16",
            )
            _whisper_ws_module._whisper_ready = True
            print("[启动-async] WhisperLiveKit 模型预加载完成", flush=True)
        except Exception as e:
            print(f"[启动-async] WhisperLiveKit 模型预加载失败（Whisper 模式将延迟首次加载）: {e}", flush=True)

    import threading
    threading.Thread(target=_warm_whisperlivekit, daemon=True, name="whisper-warmup").start()

    # Pipeline3 在使用时按需加载，不在启动时预加载（避免 GPU OOM）
    print("[启动] Pipeline3 将在首次使用时自动加载", flush=True)

    yield


app = FastAPI(
    title="智能会议记录平台 API",
    description="会议音频处理与总结系统后端网关",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS
origins = ast.literal_eval(settings.CORS_ORIGINS)
if origins == ["*"]:
    # 开发环境允许所有来源（不能与 credentials 同时为 True）
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

# REST 路由（/api/*）
from app.api.router import api_router  # noqa: E402
app.include_router(api_router, prefix="/api")

# WebSocket 路由
from app.api.websocket import router as websocket_router
app.include_router(websocket_router)

# WhisperLiveKit WebSocket 路由
from app.api.whisper_ws import router as whisper_router
app.include_router(whisper_router)

from app.api.qwen_ws import router as qwen_router
app.include_router(qwen_router)

from app.api.funasr_ws import router as funasr_router
app.include_router(funasr_router)

from app.api.hybrid_ws import router as hybrid_router
app.include_router(hybrid_router)


@app.get("/")
async def root():
    return {
        "message": "智能会议记录平台 API",
        "docs": "/docs",
        "health": "/health",
        "api": "/api"
    }


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "env": settings.ENV,
    }


@app.get("/api/health")
async def api_health_check():
    from app.asr.model_manager import get_model_manager
    mm = get_model_manager()
    return {
        "status": "ok",
        "env": settings.ENV,
        "models_ready": mm.is_initialized(),
        "models": {
            "funasr": mm.get_asr_model() is not None,
            "punc": mm.get_punc_model() is not None,
            "vad": mm.get_vad_model() is not None,
            "campplus": mm.get_camp_model() is not None,
            "campplus_en": mm.get_camp_en_model() is not None,
        },
    }
