"""
InsightEye config shim for smart-meeting-ai

StreamingPipeline reads this module for local model paths.
Paths are injected via environment variables (set from .env).
"""

import os

# 始终相对于 backend/ 目录解析，忽略 CWD
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOCAL_MODEL_DIR = os.getenv("LOCAL_MODEL_DIR", "") or os.path.join(_BACKEND_DIR, "models")
LOCAL_DEVICE = os.getenv("LOCAL_DEVICE", "cuda")

# FunASR：优先环境变量，其次 backend/models/funasr
funasr_default = os.path.join(LOCAL_MODEL_DIR, "funasr")
FUNASR_MODEL_DIR = os.getenv("FUNASR_MODEL_DIR", "") or funasr_default

# CAM++：优先环境变量，其次 backend/models/campplus/zh-cn
camp_default = os.path.join(LOCAL_MODEL_DIR, "campplus", "zh-cn")
CAMPPLUS_MODEL_DIR = os.getenv("CAMPPLUS_MODEL_DIR", "") or camp_default

# CAM++ 英文：优先环境变量，其次 backend/models/campplus/en
camp_en_default = os.path.join(LOCAL_MODEL_DIR, "campplus", "en")
CAMPPLUS_EN_MODEL_DIR = os.getenv("CAMPPLUS_EN_MODEL_DIR", "") or camp_en_default

USE_LOCAL_ASR = True
ENABLE_MACBERT_CORRECTION = False
DENOISE_ENABLED = False
DENOISE_BACKEND = "rnnoise"
DENOISER_DEVICE = LOCAL_DEVICE
