import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    ENV: str = "development"

    DATABASE_URL: str = ""

    REDIS_URL: str = "redis://localhost:6379/0"
    CELERY_BROKER_URL: str = "redis://localhost:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/2"

    ASR_SERVICE_URL: str = "http://localhost:8001"
    DIARIZATION_SERVICE_URL: str = "http://localhost:8002"
    LLM_SUMMARY_SERVICE_URL: str = "http://localhost:8003"

    FUNASR_MODEL_DIR: str = ""
    PUNC_MODEL_DIR: str = ""
    CAMPPLUS_MODEL_DIR: str = ""
    CAMPPLUS_EN_MODEL_DIR: str = ""
    LOCAL_MODEL_DIR: str = ""
    LOCAL_DEVICE: str = "cuda"

    SPEAKER_SIMILARITY_THRESHOLD: float = 0.6
    SPEAKER_MIN_CONFIDENCE: float = 0.5
    SPEAKER_VOTE_WINDOWS: int = 3

    AUDIO_STORAGE_PATH: str = "./audio_files"
    PERIOD_SUMMARY_INTERVAL_SECONDS: int = 60

    SECRET_KEY: str = "change-me-in-production"
    CORS_ORIGINS: str = '["http://localhost","http://localhost:5173"]'

    @model_validator(mode="after")
    def apply_local_defaults(self):
        if self.ENV == "local":
            if not self.DATABASE_URL:
                db_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "local.db",
                )
                self.DATABASE_URL = f"sqlite+aiosqlite:///{db_path}"
            if not self.AUDIO_STORAGE_PATH or self.AUDIO_STORAGE_PATH == "./audio_files":
                self.AUDIO_STORAGE_PATH = "./audio_files"
        return self


settings = Settings()
