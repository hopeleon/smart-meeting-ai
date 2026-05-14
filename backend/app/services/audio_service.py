"""
音频存储服务
===========
负责音频文件的存储和预处理。
当前使用本地文件系统，预留 S3 接口。
"""

import uuid
import os
from datetime import datetime

from fastapi import UploadFile

from app.config import settings


class AudioService:
    def __init__(self):
        self.storage_path = settings.AUDIO_STORAGE_PATH
        os.makedirs(self.storage_path, exist_ok=True)

    async def save_audio(self, meeting_id: str, file: UploadFile) -> dict:
        """
        保存完整音频文件。

        TODO: 接入 S3 存储时替换此实现
        """
        audio_id = uuid.uuid4()
        ext = os.path.splitext(file.filename or "audio.wav")[1]
        filename = f"{meeting_id}/{audio_id}{ext}"
        filepath = os.path.join(self.storage_path, filename)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)

        content = await file.read()
        with open(filepath, "wb") as f:
            f.write(content)

        return {
            "audio_id": str(audio_id),
            "filename": file.filename,
            "size_bytes": len(content),
            "stored_path": filepath,
        }

    async def save_chunk(
        self, meeting_id: str, file: UploadFile, chunk_index: int, is_last: bool
    ) -> dict:
        """
        保存音频 chunk（用于流式录制）。

        TODO: 接入 S3 存储时替换此实现
        """
        chunk_dir = os.path.join(self.storage_path, str(meeting_id), "chunks")
        os.makedirs(chunk_dir, exist_ok=True)

        content = await file.read()
        filepath = os.path.join(chunk_dir, f"chunk_{chunk_index:06d}.pcm")
        with open(filepath, "wb") as f:
            f.write(content)

        return {
            "chunk_index": chunk_index,
            "size_bytes": len(content),
            "is_last": is_last,
        }

    def get_audio_path(self, meeting_id: str) -> str | None:
        """获取会议音频文件路径"""
        meeting_dir = os.path.join(self.storage_path, str(meeting_id))
        if not os.path.exists(meeting_dir):
            return None
        for f in os.listdir(meeting_dir):
            if not f.startswith("chunk"):
                return os.path.join(meeting_dir, f)
        return None
