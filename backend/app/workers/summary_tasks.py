"""
总结异步任务
===========
Celery 任务：调用 LLM 服务生成会议总结，并存储到数据库。
使用 ProcessPoolExecutor 隔离 asyncio 上下文，避免与 uvicorn 事件循环冲突。
子进程内使用同步 sqlite3 而非 aiosqlite，避免多进程锁冲突。
任务完成后通过 WebSocket 推送给前端。
"""

import asyncio
import json
import subprocess
import sys
import traceback
import uuid
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from multiprocessing import get_context
from pathlib import Path

from celery import Celery
from app.workers.celery_app import celery_app


def _get_db_path():
    """获取 local.db 的绝对路径（在 backend 目录下）。"""
    backend_dir = Path(__file__).resolve().parents[2]  # backend/
    return backend_dir / "local.db"


def _get_summaries_dir():
    """获取 summaries 输出目录的绝对路径（在 backend/summaries/ 下）。"""
    backend_dir = Path(__file__).resolve().parents[2]  # backend/
    return backend_dir / "summaries"


def _run_in_loop(coro):
    """在新建事件循环中执行 async 函数。"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(None)


def _do_period_summary(meeting_id: str, transcript_lines: list[dict]) -> dict:
    """
    在独立进程中执行：调用 meetingsummary CLI + 同步 SQLite 写入。
    """
    print(f"[SummaryTask-subprocess] period 开始，meeting={meeting_id}", flush=True)

    meetingsummary_dir = Path(__file__).resolve().parents[3] / "meetingsummary"
    config_path = meetingsummary_dir / "config.json"
    if not config_path.exists():
        print(f"[SummaryTask-subprocess] config.json 不存在: {config_path}", flush=True)
        return {"bullet_points": [], "summary_id": None}

    output_dir = _get_summaries_dir() / "period"
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript_text = "\n".join(
        f"{line.get('speaker', '未知')}：{line.get('text', '')}"
        for line in transcript_lines
    )
    prefix = f"period_{meeting_id[:8]}"
    input_file = output_dir / f"{prefix}_input.txt"
    input_file.write_text(transcript_text, encoding="utf-8")

    cmd = [
        sys.executable,
        str(meetingsummary_dir / "main.py"),
        "-i", str(input_file),
        "-o", str(output_dir),
        "--prefix", prefix,
        "--no-map-reduce",
        "--skip-completeness",
        "--skip-eval",
        "--skip-actions",
    ]
    print(f"[SummaryTask-subprocess] 执行: {' '.join(cmd)}", flush=True)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180,
                           cwd=str(meetingsummary_dir))
    print(f"[SummaryTask-subprocess] returncode={result.returncode}", flush=True)
    if result.stdout:
        print(f"[SummaryTask-subprocess] stdout:\n{result.stdout[:300]}", flush=True)
    if result.stderr:
        print(f"[SummaryTask-subprocess] stderr:\n{result.stderr[:300]}", flush=True)

    # 解析 bullet_points
    import datetime as dt
    json_path = output_dir / f"{prefix}_{dt.date.today().strftime('%Y%m%d')}.json"
    bullet_points = []
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if data.get("tldr"):
                bullet_points.append(data["tldr"])
            for point in data.get("discussion_points", []):
                if isinstance(point, dict):
                    title = point.get("title", "")
                    summary = point.get("summary", "")
                    bullet_points.append(f"【{title}】{summary}" if summary else title)
                elif isinstance(point, str):
                    bullet_points.append(point)
        except Exception as e:
            print(f"[SummaryTask-subprocess] 解析 JSON 失败: {e}", flush=True)

    # 同步 SQLite 写入（避免 aiosqlite 在子进程里的锁冲突）
    period_start = min((l.get("start", 0) for l in transcript_lines), default=0)
    period_end = max((l.get("end", 0) for l in transcript_lines), default=0)
    summary_id = str(uuid.uuid4())

    db_path = _get_db_path()
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        conn.execute("""
            INSERT INTO period_summaries
            (id, meeting_id, period_start, period_end, bullet_points_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            summary_id,
            meeting_id,
            period_start,
            period_end,
            json.dumps(bullet_points, ensure_ascii=False),
            datetime.utcnow().isoformat(),
        ))
        conn.commit()
        print(f"[SummaryTask-subprocess] period DB 写入成功，summary_id={summary_id}", flush=True)
    finally:
        conn.close()

    return {
        "summary_id": summary_id,
        "bullet_points": bullet_points,
        "period_start": period_start,
        "period_end": period_end,
    }


def _do_final_summary(meeting_id: str, all_transcript_lines: list[dict],
                      period_summaries: list[dict]) -> dict:
    """在独立进程中执行：调用 meetingsummary CLI + 同步 SQLite 写入。"""
    print(f"[SummaryTask-subprocess] final 开始，meeting={meeting_id}", flush=True)

    meetingsummary_dir = Path(__file__).resolve().parents[3] / "meetingsummary"
    config_path = meetingsummary_dir / "config.json"
    if not config_path.exists():
        print(f"[SummaryTask-subprocess] config.json 不存在: {config_path}", flush=True)
        return {"overview": "", "key_decisions": [], "action_items": [], "summary_id": None}

    output_dir = _get_summaries_dir() / "final"
    output_dir.mkdir(parents=True, exist_ok=True)
    transcript_text = "\n".join(
        f"{line.get('speaker', '未知')}：{line.get('text', '')}"
        for line in all_transcript_lines
    )
    prefix = f"final_{meeting_id[:8]}"
    input_file = output_dir / f"{prefix}_input.txt"
    input_file.write_text(transcript_text, encoding="utf-8")

    cmd = [
        sys.executable,
        str(meetingsummary_dir / "main.py"),
        "-i", str(input_file),
        "-o", str(output_dir),
        "--prefix", prefix,
        "--skip-completeness",
        "--skip-eval",
        "--skip-actions",
    ]
    print(f"[SummaryTask-subprocess] final 执行: {' '.join(cmd)}", flush=True)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300,
                           cwd=str(meetingsummary_dir))
    print(f"[SummaryTask-subprocess] final returncode={result.returncode}", flush=True)
    if result.stdout:
        print(f"[SummaryTask-subprocess] stdout:\n{result.stdout[:300]}", flush=True)
    if result.stderr:
        print(f"[SummaryTask-subprocess] stderr:\n{result.stderr[:300]}", flush=True)

    import datetime as dt
    json_path = output_dir / f"{prefix}_{dt.date.today().strftime('%Y%m%d')}.json"
    overview = ""
    key_decisions = []
    action_items = []

    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            overview = data.get("meeting", {}).get("summary", data.get("tldr", ""))
            for d in data.get("decisions", []):
                if isinstance(d, dict):
                    key_decisions.append(d.get("description", d.get("decision", str(d))))
                elif isinstance(d, str):
                    key_decisions.append(d)
            for a in data.get("action_items", []):
                if isinstance(a, dict):
                    action_items.append({
                        "id": str(uuid.uuid4()),
                        "content": a.get("task", a.get("description", a.get("content", ""))),
                        "assignee": a.get("assignee"),
                        "due_date": a.get("deadline", a.get("due_date")),
                        "status": "pending",
                    })
                elif isinstance(a, str):
                    action_items.append({"id": str(uuid.uuid4()), "content": a, "assignee": None, "due_date": None, "status": "pending"})
        except Exception as e:
            print(f"[SummaryTask-subprocess] final 解析 JSON 失败: {e}", flush=True)

    # 同步 SQLite 写入
    summary_id = str(uuid.uuid4())
    db_path = _get_db_path()
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=10)
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        conn.execute("""
            INSERT INTO final_summaries
            (id, meeting_id, overview, key_decisions_json, action_items_json, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            summary_id,
            meeting_id,
            overview,
            json.dumps(key_decisions, ensure_ascii=False),
            json.dumps(action_items, ensure_ascii=False),
            datetime.utcnow().isoformat(),
        ))
        conn.commit()
        print(f"[SummaryTask-subprocess] final DB 写入成功，summary_id={summary_id}", flush=True)
    finally:
        conn.close()

    return {
        "summary_id": summary_id,
        "overview": overview,
        "key_decisions": key_decisions,
        "action_items": action_items,
    }


async def _broadcast_period_summary(meeting_id: str, data: dict):
    """通过 WebSocket 广播阶段总结。"""
    from app.api.websocket import manager
    await manager.send_period_summary(meeting_id, data)


async def _broadcast_final_summary(meeting_id: str, data: dict):
    """通过 WebSocket 广播最终总结。"""
    from app.api.websocket import manager
    await manager.send_final_summary(meeting_id, data)


@celery_app.task(bind=True, name="summary.period_summary")
def period_summary_task(self, meeting_id: str, transcript_lines: list[dict]):
    """使用进程池执行 LLM 调用，任务完成后通过 WebSocket 推送。"""
    print(f"[SummaryTask] period_summary_task 被调用，meeting={meeting_id}", flush=True)
    try:
        ctx = get_context("spawn")
        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            result = pool.submit(_do_period_summary, meeting_id, transcript_lines).result()

        summary_id = result.get("summary_id")
        bullet_points = result.get("bullet_points", [])
        period_start = result.get("period_start", 0)
        period_end = result.get("period_end", 0)
        generated_at = datetime.utcnow().isoformat()
        print(f"[SummaryTask] period 完成，summary_id={summary_id}", flush=True)

        return_data = {
            "id": summary_id,
            "meeting_id": meeting_id,
            "period_start": period_start,
            "period_end": period_end,
            "bullet_points": bullet_points,
            "generated_at": generated_at,
        }

        # 通过 WebSocket 推送给前端（回到主线程，可用新 loop）
        if summary_id:
            try:
                _run_in_loop(_broadcast_period_summary(meeting_id, return_data))
                print(f"[SummaryTask] period_summary 已推送 WS", flush=True)
            except Exception as ws_err:
                print(f"[SummaryTask] WS 推送失败: {ws_err}", flush=True)

        return return_data
    except Exception as e:
        print(f"[SummaryTask] period 异常: {e}", flush=True)
        traceback.print_exc(file=sys.stdout)
        raise


@celery_app.task(bind=True, name="summary.final_summary")
def final_summary_task(self, meeting_id: str, all_transcript_lines: list[dict],
                       period_summaries: list[dict]):
    """使用进程池执行 LLM 调用，任务完成后通过 WebSocket 推送。"""
    print(f"[SummaryTask] final_summary_task 被调用，meeting={meeting_id}", flush=True)
    try:
        ctx = get_context("spawn")
        with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
            result = pool.submit(
                _do_final_summary, meeting_id, all_transcript_lines, period_summaries
            ).result()

        summary_id = result.get("summary_id")
        overview = result.get("overview", "")
        key_decisions = result.get("key_decisions", [])
        action_items = result.get("action_items", [])
        generated_at = datetime.utcnow().isoformat()
        print(f"[SummaryTask] final 完成，summary_id={summary_id}", flush=True)

        return_data = {
            "id": summary_id,
            "meeting_id": meeting_id,
            "overview": overview,
            "key_decisions": key_decisions,
            "action_items": action_items,
            "generated_at": generated_at,
        }

        # 通过 WebSocket 推送给前端
        if summary_id:
            try:
                _run_in_loop(_broadcast_final_summary(meeting_id, return_data))
                print(f"[SummaryTask] final_summary 已推送 WS", flush=True)
            except Exception as ws_err:
                print(f"[SummaryTask] final WS 推送失败: {ws_err}", flush=True)

        return return_data
    except Exception as e:
        print(f"[SummaryTask] final 异常: {e}", flush=True)
        traceback.print_exc(file=sys.stdout)
        raise
