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

    # 文件存储
    AUDIO_STORAGE_PATH: str = "./audio_files"

    # 应用配置
    APP_ENV: str = "development"
    SECRET_KEY: str = "change-me-in-production"
    CORS_ORIGINS: str = '["http://localhost","http://localhost:5173"]'

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    def model_post_init(self, __context) -> None:
        # local 模式自动切换为 SQLite
        if self.ENV == "local":
            db_path = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "local.db",
            )
            self.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
            self.AUDIO_STORAGE_PATH = "./audio_files"


settings = Settings()
