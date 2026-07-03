"""
文件存储封装
===========
当前使用本地文件系统，预留 S3 接口。
"""

import os
from app.config import settings


class FileStorage:
    """文件存储抽象层，当前实现为本地文件系统，预留 S3 接口。"""

    def __init__(self):
        self.base_path = settings.AUDIO_STORAGE_PATH
        os.makedirs(self.base_path, exist_ok=True)

    def save(self, key: str, data: bytes) -> str:
        """
        保存文件。

        TODO: 接入 S3 时替换此方法

        Args:
            key: 文件路径（相对于 base_path）
            data: 文件内容

        Returns:
            文件完整路径
        """
        filepath = os.path.join(self.base_path, key)
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, "wb") as f:
            f.write(data)
        return filepath

    def read(self, key: str) -> bytes:
        """读取文件"""
        filepath = os.path.join(self.base_path, key)
        with open(filepath, "rb") as f:
            return f.read()

    def exists(self, key: str) -> bool:
        """检查文件是否存在"""
        return os.path.exists(os.path.join(self.base_path, key))

    def delete(self, key: str) -> None:
        """删除文件"""
        filepath = os.path.join(self.base_path, key)
        if os.path.exists(filepath):
            os.remove(filepath)

    def get_url(self, key: str) -> str:
        """
        获取文件访问 URL。

        TODO: 接入 S3 时返回预签名 URL
        """
        return f"/files/{key}"
