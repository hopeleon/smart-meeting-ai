import os
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # 运行环境：local / development / production
    ENV: str = "development"

    # 数据库
    DATABASE_URL: str = ""

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    # 模型服务地址
    ASR_SERVICE_URL: str = "http://localhost:8001"
    DIARIZATION_SERVICE_URL: str = "http://localhost:8002"
    LLM_SUMMARY_SERVICE_URL: str = "http://localhost:8003"

    # 本地 ASR 模型路径（Docker 容器内 /app/models/）
    FUNASR_MODEL_DIR: str = ""
    PUNC_MODEL_DIR: str = ""
    CAMPPLUS_MODEL_DIR: str = ""
    CAMPPLUS_EN_MODEL_DIR: str = ""
    LOCAL_MODEL_DIR: str = ""
    LOCAL_DEVICE: str = "cuda"

    # 声纹识别配置
    SPEAKER_SIMILARITY_THRESHOLD: float = 0.6  # 说话人识别相似度阈值 (0-1)
    SPEAKER_MIN_CONFIDENCE: float = 0.5  # 最低置信度要求
    SPEAKER_VOTE_WINDOWS: int = 3  # 多窗口投票数量

    # 文件存储
    AUDIO_STORAGE_PATH: str = "./audio_files"

    # 应用配置
    SECRET_KEY: str = "change-me-in-production"
    CORS_ORIGINS: str = '["http://localhost","http://localhost:5173"]'

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # local 模式自动切换为 SQLite
        if self.ENV == "local":
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "local.db",
            )
            self.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
            self.AUDIO_STORAGE_PATH = "./audio_files"


settings = Settings()
