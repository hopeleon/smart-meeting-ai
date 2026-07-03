"""Post-hoc verification and auto-correction for meeting summaries.

Compares the generated summary against the original transcript via a dedicated
LLM call, flags factual inconsistencies, and produces a corrected summary.
"""

import copy
import json
from typing import Any

from .config import OllamaConfig
from .json_parser import JSONExtractionError, extract_json
from .ollama_client import call_ollama


def _build_verify_prompt(summary: dict[str, Any], transcript: str) -> str:
    """Build the verify user-prompt containing transcript and summary JSON."""
    summary_json = json.dumps(summary, ensure_ascii=False, indent=2)
    return (
        "## 原始会议记录\n\n"
        f"{transcript}\n\n"
        "---\n\n"
        "## 待校验的会议摘要（JSON）\n\n"
        f"```json\n{summary_json}\n```\n\n"
        "请对比以上原文与摘要，逐条校验每项声明，标记不一致之处并输出修正后的完整摘要。"
    )


def _apply_corrections(
    original: dict[str, Any],
    corrected: dict[str, Any],
) -> dict[str, Any]:
    """Merge corrected summary fields into the original dict.

    Only updates fields that are present in *corrected* and differ from the
    original.  This protects against data loss if the verify model returns an
    incomplete output.
    """
    result = copy.deepcopy(original)

    # tldr: top-level string — update only if non-empty
    if corrected.get("tldr"):
        result["tldr"] = corrected["tldr"]

    # meeting and next_meeting: dicts — shallow-merge to preserve unmodified keys
    for dict_key in ("meeting", "next_meeting"):
        if dict_key in corrected and isinstance(corrected[dict_key], dict):
            target = result.setdefault(dict_key, {})
            for k, v in corrected[dict_key].items():
                if v or k not in target:
                    target[k] = v

    # discussion_points, decisions, action_items, issues_risks: lists — replace
    for list_key in (
        "discussion_points", "decisions", "action_items", "issues_risks",
    ):
        if list_key in corrected:
            val = corrected[list_key]
            if isinstance(val, list) and val:
                result[list_key] = val

    return result


def run_verification(
    config: OllamaConfig,
    verify_system_prompt: str,
    transcript: str,
    summary: dict[str, Any],
    timeout: int = 0,  # 0 表示无超时限制
) -> dict[str, Any]:
    """Run post-hoc verification and auto-correction.

    Args:
        config: LLM connection configuration.
        verify_system_prompt: System prompt for the verification phase.
        transcript: The original meeting transcript.
        summary: The generated summary dict to verify.
        timeout: Request timeout in seconds (default 180).

    Returns:
        Dict with keys:
        - corrections: list of {field_path, original, corrected, reason}
        - corrected_summary: the full corrected summary dict
        - applied: True if corrections were successfully applied

    Raises:
        JSONExtractionError: If the verify LLM response cannot be parsed.
        RuntimeError: On LLM connection / HTTP errors.
    """
    user_prompt = _build_verify_prompt(summary, transcript)
    raw_response = call_ollama(config, verify_system_prompt, user_prompt, timeout)
    parsed = extract_json(raw_response)

    corrections = parsed.get("corrections", [])
    corrected_summary = parsed.get("corrected_summary", {})

    if not corrected_summary:
        corrected_summary = copy.deepcopy(summary)

    # Apply corrections safely
    final_summary = _apply_corrections(summary, corrected_summary)

    return {
        "corrections": corrections,
        "corrected_summary": final_summary,
        "applied": len(corrections) > 0,
    }
