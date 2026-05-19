"""
实时会话状态管理
为每个会议维护独立的会话状态，关联 pipeline 和声纹识别
"""

import asyncio
import time
import uuid
from threading import Lock
from typing import Any, Optional


class RealtimeSessionStore:
    """实时会话存储，管理所有活跃会议会话"""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def create(self, meeting_id: str) -> dict[str, Any]:
        """创建一个新的会议会话"""
        with self._lock:
            if meeting_id in self._sessions:
                return self._sessions[meeting_id]

            session = {
                "meeting_id": meeting_id,
                "status": "recording",
                "created_at": time.time(),
                "segments": [],
                "pipeline": None,
                "voice_mapping": {},
                "speaker_registry": {},
            }
            self._sessions[meeting_id] = session
            return session

    def get(self, meeting_id: str) -> Optional[dict[str, Any]]:
        with self._lock:
            return self._sessions.get(meeting_id)

    def set_pipeline(self, meeting_id: str, pipeline) -> None:
        with self._lock:
            session = self._sessions.get(meeting_id)
            if session:
                session["pipeline"] = pipeline

    def append_segment(self, meeting_id: str, segment: dict) -> None:
        """追加转写片段"""
        with self._lock:
            session = self._sessions.get(meeting_id)
            if session:
                session["segments"].append(segment)

    def end(self, meeting_id: str) -> None:
        with self._lock:
            session = self._sessions.get(meeting_id)
            if session:
                session["status"] = "ended"

    def close(self, meeting_id: str) -> None:
        """完全关闭并销毁会话（pipeline 的 stop 由调用方负责）"""
        with self._lock:
            session = self._sessions.pop(meeting_id, None)
            if session:
                # 大对象引用清理，避免内存泄漏
                for key in list(session.keys()):
                    session[key] = None


# 全局单例
_store: Optional[RealtimeSessionStore] = None


def get_store() -> RealtimeSessionStore:
    global _store
    if _store is None:
        _store = RealtimeSessionStore()
    return _store
