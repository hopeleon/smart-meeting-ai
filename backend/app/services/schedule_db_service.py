"""
日程数据库服务 - 存储和管理日程事件
=====================================
使用独立 SQLite 文件，支持一次性、每日、每周、每月、每年重复事件。
查询时动态展开重复事件。
"""

import json
import os
import sqlite3
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional, List, Dict, Any


# ==================== 路径配置 ====================

def _get_default_db_path() -> str:
    backend_dir = Path(__file__).resolve().parent.parent.parent
    db_dir = backend_dir / "data"
    db_dir.mkdir(exist_ok=True)
    return str(db_dir / "schedule.db")


# ==================== 连接管理 ====================

_db_path: Optional[str] = None
_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(
            _get_db_path(),
            check_same_thread=False,
            timeout=10.0,
        )
        _local.conn.row_factory = sqlite3.Row
    return _local.conn


def _get_db_path() -> str:
    global _db_path
    return _db_path or _get_default_db_path()


def set_db_path(path: str):
    global _db_path
    _db_path = path


# ==================== 数据库初始化 ====================

def _init_db():
    conn = _get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schedule_events (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            title            TEXT    NOT NULL,
            event_type       TEXT    NOT NULL DEFAULT 'once',
            start_date       TEXT    NOT NULL,
            start_time       TEXT,
            end_time         TEXT,
            is_all_day       INTEGER NOT NULL DEFAULT 0,
            description      TEXT,
            raw_text         TEXT,
            created_at       TEXT    NOT NULL,
            updated_at       TEXT    NOT NULL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_schedule_start_date
        ON schedule_events(start_date)
    """)
    conn.commit()


# ==================== CRUD ====================

def create_event(
    title: str,
    start_date: str,
    event_type: str = "once",
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    is_all_day: bool = False,
    description: Optional[str] = None,
    raw_text: Optional[str] = None,
) -> Dict[str, Any]:
    _init_db()
    now = datetime.now().isoformat()
    conn = _get_conn()
    cur = conn.execute(
        """
        INSERT INTO schedule_events
            (title, event_type, start_date, start_time, end_time,
             is_all_day, description, raw_text, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (title, event_type, start_date, start_time, end_time,
         int(is_all_day), description, raw_text, now, now),
    )
    conn.commit()
    event_id = cur.lastrowid
    return get_event(event_id)


def get_event(event_id: int) -> Optional[Dict[str, Any]]:
    _init_db()
    conn = _get_conn()
    row = conn.execute(
        "SELECT * FROM schedule_events WHERE id = ?",
        (event_id,),
    ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def update_event(
    event_id: int,
    title: Optional[str] = None,
    event_type: Optional[str] = None,
    start_date: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    is_all_day: Optional[bool] = None,
    description: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    _init_db()
    conn = _get_conn()
    existing = get_event(event_id)
    if existing is None:
        return None

    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE schedule_events SET
            title       = ?,
            event_type  = ?,
            start_date  = ?,
            start_time  = ?,
            end_time    = ?,
            is_all_day  = ?,
            description = ?,
            updated_at  = ?
        WHERE id = ?
        """,
        (
            title if title is not None else existing["title"],
            event_type if event_type is not None else existing["event_type"],
            start_date if start_date is not None else existing["start_date"],
            start_time if start_time is not None else existing["start_time"],
            end_time if end_time is not None else existing["end_time"],
            int(is_all_day) if is_all_day is not None else existing["is_all_day"],
            description if description is not None else existing["description"],
            now,
            event_id,
        ),
    )
    conn.commit()
    return get_event(event_id)


def delete_event(event_id: int) -> bool:
    _init_db()
    conn = _get_conn()
    cur = conn.execute(
        "DELETE FROM schedule_events WHERE id = ?",
        (event_id,),
    )
    conn.commit()
    return cur.rowcount > 0


def list_events(
    year: Optional[int] = None,
    month: Optional[int] = None,
    day: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    按年月查询日程，monthly/weekly/daily 事件会在查询时动态展开。
    - year + month: 返回该月所有事件（含重复展开）
    - day: 返回该日所有事件
    """
    _init_db()
    conn = _get_conn()

    # 精确事件
    if year and month:
        month_str = f"{year}-{month:02d}"
        pattern = f"{month_str}%"
        rows = conn.execute(
            "SELECT * FROM schedule_events WHERE start_date LIKE ? ORDER BY start_time, id",
            (pattern,),
        ).fetchall()
        events = [_row_to_dict(r) for r in rows]
    elif day:
        day_str = day if isinstance(day, str) else str(day)
        rows = conn.execute(
            "SELECT * FROM schedule_events WHERE start_date = ? ORDER BY start_time, id",
            (day_str,),
        ).fetchall()
        events = [_row_to_dict(r) for r in rows]
    else:
        rows = conn.execute(
            "SELECT * FROM schedule_events ORDER BY start_date, start_time, id"
        ).fetchall()
        events = [_row_to_dict(r) for r in rows]

    # 展开重复事件
    if year and month:
        events = _expand_recurring_events(events, year, month)

    # 按日期+时间排序
    events.sort(key=lambda e: (
        e["start_date"],
        e["start_time"] or "",
        e["id"],
    ))
    return events


def _expand_recurring_events(
    events: List[Dict[str, Any]],
    year: int,
    month: int,
) -> List[Dict[str, Any]]:
    """将 monthly/weekly/daily/yearly 事件展开到指定月份"""
    from calendar import monthrange
    _, days_in_month = monthrange(year, month)
    month_str = f"{year}-{month:02d}"

    expanded: List[Dict[str, Any]] = []
    for ev in events:
        et = ev.get("event_type", "once")
        sd = ev.get("start_date", "")

        if et == "once":
            if sd.startswith(month_str):
                expanded.append(ev.copy())
        elif et == "daily":
            for d in range(1, days_in_month + 1):
                day_str = f"{month_str}-{d:02d}"
                e = ev.copy()
                e["start_date"] = day_str
                e["_is_expanded"] = True
                expanded.append(e)
        elif et == "weekly":
            day_of_week = _date_day_of_week(sd)
            for d in range(1, days_in_month + 1):
                if _date_day_of_week(f"{month_str}-{d:02d}") == day_of_week:
                    day_str = f"{month_str}-{d:02d}"
                    e = ev.copy()
                    e["start_date"] = day_str
                    e["_is_expanded"] = True
                    expanded.append(e)
        elif et == "monthly":
            md = _extract_md(sd)
            if md:
                d = md[0]
                if 1 <= d <= days_in_month:
                    day_str = f"{month_str}-{d:02d}"
                    e = ev.copy()
                    e["start_date"] = day_str
                    e["_is_expanded"] = True
                    expanded.append(e)
        elif et == "yearly":
            md = _extract_md(sd)
            if md:
                m, d = md
                if m == month and 1 <= d <= days_in_month:
                    day_str = f"{month_str}-{d:02d}"
                    e = ev.copy()
                    e["start_date"] = day_str
                    e["_is_expanded"] = True
                    expanded.append(e)

    return expanded


def _date_day_of_week(date_str: str) -> int:
    """返回日期的星期几（1=周一，7=周日）"""
    try:
        if "-" in date_str:
            parts = date_str.split("-")
            if len(parts) == 3:
                return datetime.strptime(date_str, "%Y-%m-%d").weekday() + 1
            elif len(parts) == 2:
                today = date.today()
                year = today.year
                dt = datetime.strptime(f"{year}-{parts[0]}-{parts[1]}", "%m-%d")
                return dt.weekday() + 1
    except Exception:
        pass
    return 1


def _extract_md(date_str: str) -> Optional[tuple]:
    """从日期字符串提取 (month, day)"""
    try:
        if "-" in date_str:
            parts = date_str.split("-")
            if len(parts) == 3:
                return int(parts[1]), int(parts[2])
            elif len(parts) == 2:
                return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return None


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    d = dict(row)
    d["is_all_day"] = bool(d.get("is_all_day"))
    return d
