import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import model_validator


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    ENV: str = "development"

    DATABASE_URL: str = ""

    FUNASR_MODEL_DIR: str = ""
    # 已废弃，CAM++ 模型路径已硬编码在 model_manager.py
    CAMPPLUS_MODEL_DIR: str = ""
    LOCAL_MODEL_DIR: str = ""
    LOCAL_DEVICE: str = "cuda"

    SPEAKER_SIMILARITY_THRESHOLD: float = 0.6
    SPEAKER_MIN_CONFIDENCE: float = 0.5
    SPEAKER_VOTE_WINDOWS: int = 3
    PERIOD_SUMMARY_INTERVAL_SECONDS: float = 60.0

    AUDIO_STORAGE_PATH: str = "./audio_files"

    SECRET_KEY: str = "change-me-in-production"
    CORS_ORIGINS: str = '["http://localhost","http://localhost:5179"]'

    @property
    def audio_storage_abs_path(self) -> str:
        """获取音频存储目录的绝对路径"""
        if os.path.isabs(self.AUDIO_STORAGE_PATH):
            return self.AUDIO_STORAGE_PATH
        return os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            self.AUDIO_STORAGE_PATH
        )

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
