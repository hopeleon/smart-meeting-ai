"""
InsightEye config shim for smart-meeting-ai

StreamingPipeline reads this module for local model paths.
Paths are injected via environment variables (set from .env).
"""

import os

# 所有路径直接从环境变量读取，避免循环导入
_LOCAL = os.getenv("LOCAL_MODEL_DIR", "")
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = _BASE
LOCAL_MODEL_DIR = _LOCAL or os.path.join(_BASE, "models")
LOCAL_DEVICE = os.getenv("LOCAL_DEVICE", "cuda")

# FunASR：优先环境变量，其次 /app/models/funasr（Docker）或 ./models/funasr（本地）
FUNASR_MODEL_DIR = os.getenv("FUNASR_MODEL_DIR", "") or os.path.join(LOCAL_MODEL_DIR, "funasr")
CAMPPLUS_MODEL_DIR = os.getenv("CAMPPLUS_MODEL_DIR", "") or os.path.join(LOCAL_MODEL_DIR, "campplus", "zh-cn")
CAMPPLUS_EN_MODEL_DIR = os.getenv("CAMPPLUS_EN_MODEL_DIR", "") or os.path.join(LOCAL_MODEL_DIR, "campplus", "en")

USE_LOCAL_ASR = True
ENABLE_MACBERT_CORRECTION = False
DENOISE_ENABLED = False
DENOISE_BACKEND = "rnnoise"
DENOISER_DEVICE = LOCAL_DEVICE
