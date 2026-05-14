import json
import uuid
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


class ConnectionManager:
    """管理 WebSocket 连接，支持多客户端连接同一会议"""

    def __init__(self):
        self._connections: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, meeting_id: str, websocket: WebSocket):
        await websocket.accept()
        self._connections[meeting_id].append(websocket)

    def disconnect(self, meeting_id: str, websocket: WebSocket):
        if meeting_id in self._connections:
            self._connections[meeting_id].remove(websocket)
            if not self._connections[meeting_id]:
                del self._connections[meeting_id]

    async def broadcast(self, meeting_id: str, message: dict):
        """向同一会议的所有客户端广播消息"""
        if meeting_id not in self._connections:
            return
        dead = []
        for ws in self._connections[meeting_id]:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self._connections[meeting_id].remove(ws)

    async def send_transcript(self, meeting_id: str, data: dict):
        await self.broadcast(meeting_id, {"type": "transcript", "data": data})

    async def send_period_summary(self, meeting_id: str, data: dict):
        await self.broadcast(meeting_id, {"type": "period_summary", "data": data})

    async def send_meeting_status(self, meeting_id: str, status: str):
        await self.broadcast(meeting_id, {"type": "meeting_status", "data": {"status": status}})


manager = ConnectionManager()


@router.websocket("/meeting/{meeting_id}")
async def websocket_endpoint(websocket: WebSocket, meeting_id: str):
    """
    WebSocket 端点：ws://host/ws/meeting/{meeting_id}

    推送消息格式：
    - {"type": "transcript", "data": TranscriptSegment}
    - {"type": "period_summary", "data": PeriodSummary}
    - {"type": "meeting_status", "data": {"status": "recording"|"paused"|"ended"}}
    """
    await manager.connect(meeting_id, websocket)
    try:
        while True:
            # 保持连接，接收客户端消息（如心跳）
            data = await websocket.receive_text()
            # 客户端可发送 ping 消息
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        manager.disconnect(meeting_id, websocket)
