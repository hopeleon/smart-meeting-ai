"""Independent action-item extraction, classification, and export.

Extracts action items from meeting transcripts via a dedicated LLM call,
classifies each by department (category) and urgency (priority),
and exports results as JSON and CSV files.
"""

import csv
import json
from pathlib import Path
from typing import Any

from .config import OllamaConfig
from .json_parser import JSONExtractionError, extract_json
from .ollama_client import call_ollama

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES: frozenset[str] = frozenset({
    "研发", "产品", "设计", "测试", "运营", "行政", "其他",
})

VALID_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2", "P3"})

CATEGORY_ALIASES: dict[str, str] = {
    "技术": "研发", "开发": "研发", "工程": "研发",
    "项目管理": "产品",
    "ui": "设计", "ux": "设计", "美术": "设计",
    "qa": "测试", "质量": "测试", "品质": "测试",
    "市场": "运营", "推广": "运营", "营销": "运营",
    "人事": "行政", "财务": "行政", "法务": "行政",
}

PRIORITY_ALIASES: dict[str, str] = {
    "紧急": "P0", "urgent": "P0", "critical": "P0", "立刻": "P0",
    "高": "P1", "high": "P1", "重要": "P1",
    "中": "P2", "medium": "P2", "普通": "P2", "一般": "P2",
    "低": "P3", "low": "P3",
}

CSV_HEADERS: list[str] = [
    "编号", "待办任务", "负责人", "截止日期",
    "分类", "优先级", "原文引用", "上下文",
]

CSV_FIELD_KEYS: list[str] = [
    "id", "task", "assignee", "due",
    "category", "priority", "source", "context",
]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_category(raw: str) -> str:
    """Map a raw category string to a valid enum value, falling back to '其他'."""
    cleaned = raw.strip()
    if cleaned in VALID_CATEGORIES:
        return cleaned
    lowered = cleaned.lower()
    for alias, canonical in CATEGORY_ALIASES.items():
        if alias in lowered or lowered in alias:
            return canonical
    return "其他"


def _normalize_priority(raw: str) -> str:
    """Map a raw priority string to a valid P0-P3 value, falling back to P2."""
    cleaned = raw.strip().upper()
    if cleaned in VALID_PRIORITIES:
        return cleaned
    lowered = raw.strip().lower()
    for alias, canonical in PRIORITY_ALIASES.items():
        if alias in lowered or lowered in alias:
            return canonical
    return "P2"


def _normalize_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize category and priority in every action item."""
    for item in items:
        item["category"] = _normalize_category(item.get("category", "其他"))
        item["priority"] = _normalize_priority(item.get("priority", "P2"))
    return items


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract_action_items(
    config: OllamaConfig,
    system_prompt: str,
    transcript: str,
    timeout: int = 0,  # 0 表示无超时限制
) -> list[dict[str, Any]]:
    """Run the dedicated action-item extraction pipeline.

    1. Call Ollama with the action-items system prompt.
    2. Parse the JSON response.
    3. Normalize category and priority fields.

    Returns the list of action item dicts.  Raises on LLM or parse failure;
    caller (main.py) catches these so the summary output is never blocked.
    """
    raw_response = call_ollama(config, system_prompt, transcript, timeout)
    parsed = extract_json(raw_response)
    items = parsed.get("action_items", [])
    return _normalize_items(items)


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def write_actions_csv(
    output_dir: Path,
    prefix: str,
    date_str: str,
    items: list[dict[str, Any]],
) -> Path:
    """Write action items to a CSV file with UTF-8 BOM for Excel compatibility.

    Args:
        output_dir: Directory for output files.
        prefix: Filename prefix.
        date_str: Date string in YYYYMMDD format.
        items: List of action item dicts.

    Returns:
        Path to the written CSV file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / f"{prefix}_{date_str}_actions.csv"

    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f, quoting=csv.QUOTE_NONNUMERIC)
        writer.writerow(CSV_HEADERS)
        for item in items:
            writer.writerow([item.get(key, "") for key in CSV_FIELD_KEYS])

    return csv_path


# ---------------------------------------------------------------------------
# JSON export
# ---------------------------------------------------------------------------

def write_actions_json(
    output_dir: Path,
    prefix: str,
    date_str: str,
    items: list[dict[str, Any]],
) -> Path:
    """Write action items to a dedicated JSON file with metadata.

    Args:
        output_dir: Directory for output files.
        prefix: Filename prefix.
        date_str: Date string in YYYYMMDD format.
        items: List of action item dicts.

    Returns:
        Path to the written JSON file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / f"{prefix}_{date_str}_actions.json"

    payload: dict[str, Any] = {
        "metadata": {
            "extraction_date": date_str,
            "total_items": len(items),
            "categories": sorted(set(i.get("category", "") for i in items)),
            "priorities": sorted(set(i.get("priority", "") for i in items)),
        },
        "action_items": items,
    }

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    return json_path
