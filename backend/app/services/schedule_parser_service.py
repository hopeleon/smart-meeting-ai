"""
日程解析服务 - 将自然语言文本解析为结构化日程
=============================================
调用 Ollama LLM，从用户语音转写文本中提取日程结构：
  - 标题、时间（具体日期/时间段/每月/每年）
  - 是否全天
  - 备注

复用 meetingsummary 模块的 ollama_client 统一调用 Ollama。
"""

import calendar
import json
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from meetingsummary.ollama_client import call_ollama
from meetingsummary.config import load_config


# ==================== Prompt 模板 ====================

_SYSTEM_PROMPT_TEMPLATE = """你是一个日程结构化提取助手。你的任务是从用户输入中提取日程信息。

当前日期：__TODAY__，星期__WEEKDAY__。所有相对时间都必须基于这个日期换算。

请严格按以下 JSON 格式返回（不要输出任何其他内容）：
{
  "title": "事件标题（简洁，20字以内）",
  "event_type": "once",
  "start_date": "YYYY-MM-DD（具体某一天）或 MM-DD（每月/每年重复时）",
  "start_time": "HH:MM 或 null（表示全天事件）",
  "end_time": "HH:MM 或 null",
  "is_all_day": true 或 false,
  "description": "备注说明或 null",
  "confidence": 0.0 到 1.0,
  "needs_clarification": true 或 false,
  "clarification_question": "需要用户确认的问题，或 null"
}

事件类型说明：
- once: 具体某一天的事件（如"下周三开会"）
- daily: 每天重复
- weekly: 每周重复（如"每周一开会"）
- monthly: 每月重复（如"每月15号发工资"）
- yearly: 每年重复（如"每年6月1号生日"）

重要规则：
1. title 必须是简洁的事件名称，不要包含时间信息
2. start_date 为具体日期时必须是 YYYY-MM-DD 格式
3. 如果用户只说了月份没具体日期，取该月第一天或用户提到的具体日期
4. 如果用户提到"下午"、"早上"等模糊时间但没说具体几点，设为 null
5. description 只提取额外备注，不重复 title
6. 如果缺少具体日期但有明确事项，不要编造很远的日期；可以用当前日期作为候选并设置 needs_clarification=true
7. 如果缺少具体时间但有明确日期，可以作为全天事项并设置 needs_clarification=true
8. 如果无法从文本中提取到有效日程，返回 null（而不是编造信息）

用户输入："""


# ==================== LLM 调用 ====================

def _load_ollama_config() -> "OllamaConfig":
    """加载 meetingsummary 的 Ollama 配置"""
    _ensure_local_no_proxy()
    config_path = Path(__file__).resolve().parents[3] / "meetingsummary" / "config.json"
    config = load_config(config_path)
    schedule_model = os.getenv("SCHEDULE_OLLAMA_MODEL", "qwen3:8b").strip()
    config = config._replace(model=schedule_model or "qwen3:8b")
    if config.provider == "ollama" and config.base_url.startswith("http://localhost:"):
        return config._replace(base_url=config.base_url.replace("http://localhost:", "http://127.0.0.1:", 1))
    return config


def _ensure_local_no_proxy() -> None:
    for key in ("NO_PROXY", "no_proxy"):
        current = os.getenv(key, "")
        entries = [item.strip() for item in current.split(",") if item.strip()]
        for host in ("127.0.0.1", "localhost"):
            if host not in entries:
                entries.append(host)
        os.environ[key] = ",".join(entries)


def _schedule_llm_timeout() -> int:
    try:
        return max(3, int(os.getenv("SCHEDULE_LLM_TIMEOUT", "12")))
    except ValueError:
        return 12


def _build_system_prompt() -> str:
    weekdays = ["一", "二", "三", "四", "五", "六", "日"]
    today = date.today()
    return (
        _SYSTEM_PROMPT_TEMPLATE
        .replace("__TODAY__", today.strftime("%Y-%m-%d"))
        .replace("__WEEKDAY__", weekdays[today.weekday()])
    )


async def parse_schedule_text(text: str) -> Optional[dict]:
    """
    将自然语言文本解析为日程结构。
    在异步上下文中调用（供 FastAPI 路由使用）。

    Args:
        text: 用户语音转写文本

    Returns:
        解析后的日程字典，或 None（解析失败时）
    """
    import asyncio

    quick = _parse_schedule_text_quick(text)
    if quick:
        return quick

    def _call():
        config = _load_ollama_config()
        try:
            raw = call_ollama(
                config,
                system_prompt=_build_system_prompt(),
                transcript=text,
                timeout=_schedule_llm_timeout(),
            )
            return _parse_llm_response(raw, text)
        except Exception as e:
            print(f"[ScheduleParser] LLM 调用失败: {e}", flush=True)
            return None

    return await asyncio.to_thread(_call)


def parse_schedule_text_sync(text: str) -> Optional[dict]:
    """同步版本的解析（供线程池调用）"""
    quick = _parse_schedule_text_quick(text)
    if quick:
        return quick

    config = _load_ollama_config()
    try:
        raw = call_ollama(
            config,
            system_prompt=_build_system_prompt(),
            transcript=text,
            timeout=_schedule_llm_timeout(),
        )
        return _parse_llm_response(raw, text)
    except Exception as e:
        print(f"[ScheduleParser] LLM 调用失败: {e}", flush=True)
        return None


def apply_schedule_clarification(current: dict, answer: str) -> Optional[dict]:
    """把用户对追问的补充应用到当前日程草稿上。"""
    normalized = _normalize_schedule_text(answer)
    if not normalized:
        return None

    updated = dict(current)
    parsed_date = _parse_explicit_or_relative_date(normalized)
    parsed_time = _parse_time(normalized)
    wants_all_day = bool(re.search(r"(全天|整天|一天|当天|就这样|按当前|按这个|直接保存|确认保存)", normalized))

    if parsed_date is not None:
        updated["start_date"] = parsed_date.strftime("%Y-%m-%d")
    if parsed_time is not None:
        updated["start_time"] = parsed_time
        updated["end_time"] = _default_end_time(parsed_time)
        updated["is_all_day"] = False
    elif wants_all_day:
        updated["start_time"] = None
        updated["end_time"] = None
        updated["is_all_day"] = True

    if parsed_date is None and parsed_time is None and not wants_all_day:
        followup = parse_schedule_text_sync(f"{current.get('title', '日程')}，{answer}")
        if followup:
            for key in ("event_type", "start_date", "start_time", "end_time", "is_all_day"):
                updated[key] = followup.get(key, updated.get(key))
        else:
            return None

    raw_text = str(current.get("raw_text") or "").strip()
    updated["raw_text"] = f"{raw_text}；补充：{answer.strip()}" if raw_text else answer.strip()
    updated["parse_source"] = "clarified"
    updated["confidence"] = max(float(updated.get("confidence") or 0), 0.92)
    updated["needs_clarification"] = False
    updated["clarification_question"] = None
    return updated


def _parse_llm_response(raw: str, raw_text: str) -> Optional[dict]:
    """从 LLM 输出中提取 JSON，尝试多种解析策略"""
    # 策略1：直接解析完整 JSON
    try:
        return _normalize_llm_result(json.loads(raw), raw_text)
    except json.JSONDecodeError:
        pass

    # 策略2：提取 markdown 代码块
    code_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if code_match:
        try:
            return _normalize_llm_result(json.loads(code_match.group(1)), raw_text)
        except json.JSONDecodeError:
            pass

    # 策略3：找到第一个 { ... } JSON 对象
    brace_start = raw.find("{")
    brace_end = raw.rfind("}")
    if brace_start != -1 and brace_end != -1 and brace_end > brace_start:
        candidate = raw[brace_start:brace_end + 1]
        try:
            result = json.loads(candidate)
            normalized = _normalize_llm_result(result, raw_text)
            if normalized:
                return normalized
        except json.JSONDecodeError:
            pass

    # 策略4：逐行扫描找有效 JSON
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                result = json.loads(line)
                normalized = _normalize_llm_result(result, raw_text)
                if normalized:
                    return normalized
            except json.JSONDecodeError:
                continue

    print(f"[ScheduleParser] 无法从 LLM 输出解析 JSON: {raw[:200]}", flush=True)
    return None


def _normalize_llm_result(result: object, raw_text: str) -> Optional[dict]:
    """规整并校验本地大模型输出，避免自由发挥直接进入日程。"""
    if result is None:
        return None
    if not isinstance(result, dict):
        return None

    title = str(result.get("title") or "").strip()
    event_type = str(result.get("event_type") or "once").strip()
    start_date = str(result.get("start_date") or "").strip()
    start_time = _clean_time(result.get("start_time"))
    end_time = _clean_time(result.get("end_time"))
    description = result.get("description")
    description = str(description).strip() if description not in (None, "") else None

    if event_type not in {"once", "daily", "weekly", "monthly", "yearly"}:
        event_type = "once"
    if not title or not start_date:
        return None
    if not _is_valid_date_like(start_date):
        return None

    is_all_day = bool(result.get("is_all_day", start_time is None))
    if is_all_day:
        start_time = None
        end_time = None
    elif start_time and not end_time:
        end_time = _default_end_time(start_time)

    confidence = _clamp_float(result.get("confidence"), default=0.72)
    needs_clarification = bool(result.get("needs_clarification", confidence < 0.7))
    clarification_question = result.get("clarification_question")
    clarification_question = (
        str(clarification_question).strip()
        if clarification_question not in (None, "")
        else None
    )
    inferred_question = _infer_clarification_question(
        raw_text=raw_text,
        is_all_day=is_all_day,
        start_time=start_time,
    )
    if inferred_question:
        needs_clarification = True
        clarification_question = inferred_question

    return {
        "title": title[:100],
        "event_type": event_type,
        "start_date": start_date,
        "start_time": start_time,
        "end_time": end_time,
        "is_all_day": is_all_day,
        "description": description[:500] if description else None,
        "raw_text": raw_text,
        "parse_source": "local_llm",
        "confidence": confidence,
        "needs_clarification": needs_clarification,
        "clarification_question": clarification_question,
    }


def _infer_clarification_question(
    *,
    raw_text: str,
    is_all_day: bool,
    start_time: Optional[str],
) -> Optional[str]:
    """对大模型容易过度自信的模糊时间做硬性追问。"""
    normalized = _normalize_schedule_text(raw_text)
    fuzzy_deadline = re.search(
        r"(月底前|月末前|年底前|年末前|本周内|这周内|下周内|本月内|这个月内|"
        r"之前|截止|截至|尽快|抽空|有空|找个时间|找时间|大概|左右|早些时候|晚些时候)",
        normalized,
    )
    if fuzzy_deadline:
        return "这个表达更像截止时间或模糊时间，需要我按当前日期保存，还是换成具体某一天/某个时间？"
    if is_all_day and start_time is None and re.search(r"(上午|下午|晚上|早上|中午|点|半)", normalized) is None:
        return "没有听到具体时间，需要作为全天事项保存，还是补一个开始时间？"
    return None


def _clean_time(value: object) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.lower() == "null":
        return None
    match = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", text)
    if not match:
        return None
    return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"


def _is_valid_date_like(value: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            year, month, day = [int(part) for part in value.split("-")]
            date(year, month, day)
            return True
        except ValueError:
            return False
    if re.fullmatch(r"\d{2}-\d{2}", value):
        month, day = [int(part) for part in value.split("-")]
        return 1 <= month <= 12 and 1 <= day <= 31
    return False


def _clamp_float(value: object, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(0.0, min(1.0, number))


# ==================== 快速规则解析 ====================

_CN_NUM_MAP = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_WEEKDAY_MAP = {
    "一": 0,
    "二": 1,
    "三": 2,
    "四": 3,
    "五": 4,
    "六": 5,
    "日": 6,
    "天": 6,
    "1": 0,
    "2": 1,
    "3": 2,
    "4": 3,
    "5": 4,
    "6": 5,
    "7": 6,
}


def _parse_schedule_text_quick(text: str) -> Optional[dict]:
    """解析高频中文日程口令，避免简单任务也等待大模型。"""
    normalized = _normalize_schedule_text(text)
    if not normalized:
        return None

    event_type = "once"
    parsed_date = _parse_explicit_or_relative_date(normalized)
    if "每天" in normalized or "每日" in normalized:
        event_type = "daily"
        parsed_date = date.today()
    elif match := re.search(r"每(?:周|星期|礼拜)([一二三四五六日天1-7])", normalized):
        event_type = "weekly"
        parsed_date = _next_weekday(_WEEKDAY_MAP[match.group(1)], include_today=True)
    elif match := re.search(r"每月([0-9一二两三四五六七八九十]{1,3})(?:号|日)", normalized):
        event_type = "monthly"
        day = max(1, min(31, _cn_to_int(match.group(1))))
        today = date.today()
        parsed_date = date(today.year, today.month, min(day, calendar.monthrange(today.year, today.month)[1]))
    elif match := re.search(r"每年([0-9一二两三四五六七八九十]{1,2})月([0-9一二两三四五六七八九十]{1,2})(?:号|日)", normalized):
        event_type = "yearly"
        month = max(1, min(12, _cn_to_int(match.group(1))))
        day = max(1, min(31, _cn_to_int(match.group(2))))
        today = date.today()
        parsed_date = date(today.year, month, min(day, calendar.monthrange(today.year, month)[1]))

    parsed_time = _parse_time(normalized)
    has_date = parsed_date is not None
    has_time = parsed_time is not None
    if not has_date and not has_time:
        return None

    title, description = _extract_title_and_description(normalized)
    if not title:
        title = "待办"

    start_time = parsed_time
    end_time = _default_end_time(start_time)
    return {
        "title": title[:100],
        "event_type": event_type,
        "start_date": (parsed_date or date.today()).strftime("%Y-%m-%d"),
        "start_time": start_time,
        "end_time": end_time,
        "is_all_day": start_time is None,
        "description": description[:500] if description else None,
        "raw_text": text,
        "parse_source": "rules",
        "confidence": _quick_confidence(
            has_date=has_date,
            has_time=has_time,
            title=title,
            event_type=event_type,
        ),
        "needs_clarification": not has_date or not has_time,
        "clarification_question": _quick_clarification_question(
            has_date=has_date,
            has_time=has_time,
        ),
    }


def _quick_confidence(*, has_date: bool, has_time: bool, title: str, event_type: str) -> float:
    confidence = 0.82
    if has_date:
        confidence += 0.08
    if has_time:
        confidence += 0.06
    if title and title != "待办":
        confidence += 0.04
    if event_type != "once":
        confidence += 0.03
    return min(0.97, confidence)


def _quick_clarification_question(*, has_date: bool, has_time: bool) -> Optional[str]:
    if not has_date and not has_time:
        return "需要补充日期和时间。"
    if not has_date:
        return "没有听到具体日期，是否安排在今天？"
    if not has_time:
        return "没有听到具体时间，是否作为全天事项保存？"
    return None


_LAOJI_WAKE_WORD = r"(?:老记|老纪|老计|老季|牢记|小记)"


def normalize_laoji_transcript(text: str) -> str:
    """纠正老记唤醒词的常见同音 ASR 误写，只处理句首连续唤醒词。"""
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    pattern = rf"^((?:{_LAOJI_WAKE_WORD}[，,。\s]*){{1,2}})"
    match = re.match(pattern, cleaned)
    if not match:
        return cleaned
    normalized_prefix = re.sub(_LAOJI_WAKE_WORD, "老记", match.group(1))
    return normalized_prefix + cleaned[match.end():]


def _normalize_schedule_text(text: str) -> str:
    text = normalize_laoji_transcript(text)
    cleaned = text.strip()
    cleaned = re.sub(rf"^{_LAOJI_WAKE_WORD}[，,。\s]*(?:{_LAOJI_WAKE_WORD})?[，,。\s]*", "", cleaned)
    cleaned = re.sub(r"\s+", "", cleaned)
    return cleaned.strip("，,。.!！?？")


def _parse_explicit_or_relative_date(text: str) -> Optional[date]:
    today = date.today()
    if "下月底" in text or "下月末" in text:
        year = today.year + int(today.month == 12)
        month = 1 if today.month == 12 else today.month + 1
        return date(year, month, calendar.monthrange(year, month)[1])
    if "月底" in text or "月末" in text:
        return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    if "大后天" in text:
        return today + timedelta(days=3)
    if "后天" in text:
        return today + timedelta(days=2)
    if "明天" in text:
        return today + timedelta(days=1)
    if "今天" in text or "今日" in text:
        return today

    if match := re.search(r"([0-9]{4})年([0-9一二两三四五六七八九十]{1,2})月([0-9一二两三四五六七八九十]{1,2})(?:号|日)", text):
        year = int(match.group(1))
        month = max(1, min(12, _cn_to_int(match.group(2))))
        day = max(1, min(calendar.monthrange(year, month)[1], _cn_to_int(match.group(3))))
        return date(year, month, day)

    if match := re.search(r"([0-9一二两三四五六七八九十]{1,2})月([0-9一二两三四五六七八九十]{1,2})(?:号|日)", text):
        month = max(1, min(12, _cn_to_int(match.group(1))))
        day = max(1, min(calendar.monthrange(today.year, month)[1], _cn_to_int(match.group(2))))
        candidate = date(today.year, month, day)
        if candidate < today:
            candidate = date(today.year + 1, month, day)
        return candidate

    if match := re.search(r"(?:(下下|下|本|这)?(?:周|星期|礼拜))([一二三四五六日天1-7])", text):
        prefix = match.group(1) or ""
        weekday = _WEEKDAY_MAP[match.group(2)]
        if prefix == "下下":
            return _weekday_in_week(weekday, weeks_from_this=2)
        if prefix == "下":
            return _weekday_in_week(weekday, weeks_from_this=1)
        if prefix in {"本", "这"}:
            return _weekday_in_week(weekday, weeks_from_this=0)
        return _next_weekday(weekday)

    if match := re.search(r"([0-9一二两三四五六七八九十]{1,2})(?:号|日)", text):
        day = _cn_to_int(match.group(1))
        _, days_in_month = calendar.monthrange(today.year, today.month)
        if 1 <= day <= days_in_month:
            candidate = date(today.year, today.month, day)
            if candidate < today:
                year = today.year + int(today.month == 12)
                month = 1 if today.month == 12 else today.month + 1
                candidate = date(year, month, min(day, calendar.monthrange(year, month)[1]))
            return candidate

    return None


def _parse_time(text: str) -> Optional[str]:
    if match := re.search(r"([01]?\d|2[0-3])[:：]([0-5]\d)", text):
        return f"{int(match.group(1)):02d}:{int(match.group(2)):02d}"

    match = re.search(
        r"(凌晨|早上|上午|中午|下午|晚上|晚间)?([0-9一二两三四五六七八九十]{1,3})点(?:(半)|([0-9一二两三四五六七八九十]{1,2})分?)?",
        text,
    )
    if not match:
        return None

    period = match.group(1) or ""
    hour = _cn_to_int(match.group(2))
    minute = 30 if match.group(3) else (_cn_to_int(match.group(4)) if match.group(4) else 0)
    if period in {"下午", "晚上", "晚间"} and hour < 12:
        hour += 12
    elif period == "中午" and hour < 11:
        hour += 12
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _extract_title_and_description(text: str) -> tuple[str, Optional[str]]:
    description = None
    desc_match = re.search(r"(?:要)?(?:讨论|聊|沟通|确认|备注|内容是|主题是|关于)(.+)$", text)
    title_text = text
    if desc_match:
        description = desc_match.group(1).strip("，,。.!！?？")
        title_text = text[:desc_match.start()].strip("，,。.!！?？")

    title_text = _normalize_title(_strip_schedule_markers(title_text))
    if not title_text and description:
        title_text = description
    return title_text[:20], description


def _strip_schedule_markers(text: str) -> str:
    patterns = [
        r"每(?:周|星期|礼拜)[一二三四五六日天1-7]",
        r"每(?:天|日)",
        r"每月[0-9一二两三四五六七八九十]{1,3}(?:号|日)?",
        r"每年[0-9一二两三四五六七八九十]{1,2}月[0-9一二两三四五六七八九十]{1,2}(?:号|日)?",
        r"(大后天|后天|明天|今天|今日)",
        r"(?:下月)?(?:月底|月末)前?",
        r"[0-9]{4}年[0-9一二两三四五六七八九十]{1,2}月[0-9一二两三四五六七八九十]{1,2}(?:号|日)",
        r"[0-9一二两三四五六七八九十]{1,2}月[0-9一二两三四五六七八九十]{1,2}(?:号|日)",
        r"(?:(?:下下|下|本|这)?(?:周|星期|礼拜))[一二三四五六日天1-7]",
        r"(凌晨|早上|上午|中午|下午|晚上|晚间)?[0-9一二两三四五六七八九十]{1,3}点(?:半|[0-9一二两三四五六七八九十]{1,2}分?)?",
        r"([01]?\d|2[0-3])[:：]([0-5]\d)",
        r"(提醒我|记一下|安排|日程|待办|要|需要)",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned)
    return cleaned.strip("，,。.!！?？的")


def _normalize_title(title: str) -> str:
    title = re.sub(r"^[，,。.!！?？\s]+", "", title)
    title = re.sub(r"^(开|召开|举行|进行)(?=.{2,})", "", title)
    title = re.sub(r"^(和|跟|与)(?=.{2,})", "", title)
    return title.strip("，,。.!！?？的")


def _default_end_time(start_time: Optional[str]) -> Optional[str]:
    if not start_time:
        return None
    hour, minute = [int(part) for part in start_time.split(":")]
    hour += 1
    if hour >= 24:
        return None
    return f"{hour:02d}:{minute:02d}"


def _weekday_in_week(weekday: int, weeks_from_this: int) -> date:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday + timedelta(days=weeks_from_this * 7 + weekday)


def _next_weekday(weekday: int, include_today: bool = False) -> date:
    today = date.today()
    delta = weekday - today.weekday()
    if delta < 0 or (delta == 0 and not include_today):
        delta += 7
    return today + timedelta(days=delta)


def _cn_to_int(value: Optional[str]) -> int:
    if not value:
        return 0
    if value.isdigit():
        return int(value)
    if value == "十":
        return 10
    if value.startswith("十"):
        return 10 + _CN_NUM_MAP.get(value[1:], 0)
    if "十" in value:
        left, right = value.split("十", 1)
        return _CN_NUM_MAP.get(left, 0) * 10 + (_CN_NUM_MAP.get(right, 0) if right else 0)
    total = 0
    for char in value:
        total = total * 10 + _CN_NUM_MAP.get(char, 0)
    return total


async def parse_schedule_audio(audio_base64: str, filename: str) -> Optional[dict]:
    """
    从音频 base64 解析日程：先 ASR 转写，再 LLM 解析。

    Args:
        audio_base64: 音频文件的 base64 编码
        filename: 文件名（如 "recording.wav"）

    Returns:
        解析后的日程字典，或 None
    """
    import asyncio
    import base64
    import io
    import soundfile as sf
    import numpy as np
    from app.services.offline_pipeline import OfflinePipeline

    def _call():
        try:
            # 解码音频
            audio_bytes = base64.b64decode(audio_base64)
            audio_io = io.BytesIO(audio_bytes)
            audio_data, sr = sf.read(audio_io, dtype="float32")
            if audio_data.ndim > 1:
                audio_data = audio_data.mean(axis=1)

            # VibeVoice-ASR 转写
            pipeline = OfflinePipeline(
                meeting_id="schedule_audio_parse",
                sample_rate=sr,
                on_progress=lambda s, p: print(f"[ScheduleParser-ASR] {s}: {p:.0%}", flush=True),
            )
            import asyncio
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                result = loop.run_until_complete(pipeline.process(audio_data))
            finally:
                loop.close()

            if not result.turns:
                print("[ScheduleParser] ASR 无转写结果", flush=True)
                return None

            # 拼接转写文本
            transcript_text = " ".join(t.text for t in result.turns)
            print(f"[ScheduleParser] ASR 结果: {transcript_text[:100]}", flush=True)

            # LLM 解析
            parsed = parse_schedule_text_sync(transcript_text)
            if parsed:
                parsed["raw_text"] = transcript_text
            return parsed

        except Exception as e:
            print(f"[ScheduleParser] 音频解析失败: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return None

    return await asyncio.to_thread(_call)


async def transcribe_schedule_audio(audio_base64: str, filename: str = "recording.wav") -> Optional[dict]:
    """轻量一句话 ASR：只做音频转文字，不触发会议离线管道和日程解析。"""
    import asyncio
    import base64
    import io
    import soundfile as sf
    import numpy as np

    from app.asr.model_manager import get_model_manager
    from app.asr.streaming_pipeline import _clean_asr_text

    def _resample_linear(audio: np.ndarray, source_sr: int, target_sr: int = 16000) -> np.ndarray:
        if source_sr == target_sr:
            return audio.astype(np.float32)
        if len(audio) == 0:
            return audio.astype(np.float32)
        duration = len(audio) / float(source_sr)
        target_len = max(1, int(duration * target_sr))
        x_old = np.linspace(0, len(audio) - 1, num=len(audio), dtype=np.float32)
        x_new = np.linspace(0, len(audio) - 1, num=target_len, dtype=np.float32)
        return np.interp(x_new, x_old, audio).astype(np.float32)

    def _call():
        try:
            audio_bytes = base64.b64decode(audio_base64)
            audio_io = io.BytesIO(audio_bytes)
            audio_data, sr = sf.read(audio_io, dtype="float32")
            if audio_data.ndim > 1:
                audio_data = audio_data.mean(axis=1)
            audio_data = np.asarray(audio_data, dtype=np.float32)
            audio_data = _resample_linear(audio_data, int(sr), 16000)
            if len(audio_data) < 1600:
                return None

            mm = get_model_manager()
            if not mm.is_initialized():
                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(mm.initialize())
                finally:
                    loop.close()

            asr_model = mm.get_asr_model()
            if asr_model is None:
                return None

            result = asr_model.generate(
                input=audio_data,
                batch_size_s=300,
                is_streaming=False,
                language="zh",
            )
            texts = []
            for item in result or []:
                if isinstance(item, dict):
                    text = item.get("text", "")
                else:
                    text = str(item or "")
                text = _clean_asr_text(text.strip())
                if text:
                    texts.append(text)
            transcript = normalize_laoji_transcript(_clean_asr_text("".join(texts).strip()))
            print(
                f"[Schedule-ASR] {filename}: {len(audio_data) / 16000:.2f}s -> {transcript[:120]}",
                flush=True,
            )
            if not transcript:
                return None
            return {
                "text": transcript,
                "duration_sec": round(len(audio_data) / 16000, 3),
                "provider": "funasr",
            }
        except Exception as e:
            print(f"[Schedule-ASR] 转写失败: {e}", flush=True)
            import traceback
            traceback.print_exc()
            return None

    return await asyncio.to_thread(_call)
