"""Completeness checking for meeting summaries.

Evaluates whether a generated summary fully covers the original meeting
transcript by checking agenda coverage, speaker coverage, and section
completeness through a second-pass LLM call.
"""

from typing import Any

from config import OllamaConfig
from json_parser import JSONExtractionError, extract_json
from ollama_client import call_ollama

COVERAGE_THRESHOLD: float = 80.0


# ---------------------------------------------------------------------------
# Checkpoint extraction
# ---------------------------------------------------------------------------

def extract_checkpoints(summary: dict[str, Any]) -> dict[str, list[str]]:
    """Extract completeness checkpoints from the summary for LLM prompting.

    Returns a dict with three keys:
    - agenda_items: list of agenda descriptions
    - speakers: list of unique speaker names from discussion_points
    - sections: list of expected top-level section names
    """
    meeting = summary.get("meeting", {})
    agenda_items = list(meeting.get("agenda", []))

    speakers: list[str] = []
    for dp in summary.get("discussion_points", []):
        speaker = dp.get("speaker", "")
        if speaker and speaker not in speakers and speaker != "Unknown":
            speakers.append(speaker)

    sections = [
        "tldr", "meeting", "discussion_points", "decisions",
        "action_items", "issues_risks", "next_meeting",
    ]

    return {
        "agenda_items": agenda_items,
        "speakers": speakers,
        "sections": sections,
    }


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_checkpoint_text(checkpoints: dict[str, list[str]]) -> str:
    """Render checkpoints as a human-readable text block for the LLM prompt."""
    lines: list[str] = []

    lines.append("### 已提取的议程项")
    agenda = checkpoints.get("agenda_items", [])
    if agenda:
        for i, item in enumerate(agenda, 1):
            lines.append(f"  {i}. {item}")
    else:
        lines.append("  （无）")

    lines.append("### 已提取的发言人")
    spk = checkpoints.get("speakers", [])
    if spk:
        for i, s in enumerate(spk, 1):
            lines.append(f"  {i}. {s}")
    else:
        lines.append("  （无）")

    return "\n".join(lines)


def _build_user_prompt(summary: dict[str, Any], transcript: str) -> str:
    """Build the completeness auditor user-prompt."""
    import json
    checkpoints = extract_checkpoints(summary)
    checkpoint_text = _build_checkpoint_text(checkpoints)
    summary_json = json.dumps(summary, ensure_ascii=False, indent=2)

    return (
        "## 原始会议记录\n\n"
        f"{transcript}\n\n"
        "---\n\n"
        "## 生成的会议摘要（JSON）\n\n"
        f"```json\n{summary_json}\n```\n\n"
        "---\n\n"
        "## 参考检查点\n\n"
        f"{checkpoint_text}\n\n"
        "请根据以上信息，评估摘要对原始会议的覆盖完整性。"
    )


# ---------------------------------------------------------------------------
# Coverage calculation
# ---------------------------------------------------------------------------

def calculate_coverage(data: dict[str, Any]) -> float:
    """Calculate composite coverage score from agenda, speaker, section checks.

    Formula: mean of available coverage percentages.
    """
    ratios: list[float] = []

    agenda = data.get("agenda_check", {})
    if isinstance(agenda, dict):
        total = agenda.get("total", 0)
        if total > 0:
            ratios.append(agenda.get("covered", 0) / total)

    speaker = data.get("speaker_check", {})
    if isinstance(speaker, dict):
        total = speaker.get("total", 0)
        if total > 0:
            ratios.append(speaker.get("covered", 0) / total)

    section = data.get("section_check", {})
    if isinstance(section, dict):
        present = len(section.get("sections_present", []))
        empty = len(section.get("sections_empty", []))
        missing = len(section.get("sections_missing", []))
        total = present + empty + missing
        if total > 0:
            ratios.append(present / total)

    if not ratios:
        return 100.0
    return sum(ratios) / len(ratios) * 100


# ---------------------------------------------------------------------------
# Result compilation
# ---------------------------------------------------------------------------

def _compile_result(raw_data: dict[str, Any]) -> dict[str, Any]:
    """Merge calculated coverage score and threshold into the auditor result."""
    score = round(calculate_coverage(raw_data), 1)
    return {
        "coverage_score": score,
        "threshold": COVERAGE_THRESHOLD,
        "passed": score >= COVERAGE_THRESHOLD,
        "agenda_check": raw_data.get("agenda_check", {}),
        "speaker_check": raw_data.get("speaker_check", {}),
        "section_check": raw_data.get("section_check", {}),
        "overall_assessment": raw_data.get("overall_assessment", ""),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_completeness_check(
    config: OllamaConfig,
    system_prompt: str,
    transcript: str,
    summary: dict[str, Any],
    timeout: int = 120,
) -> dict[str, Any]:
    """Run the full completeness check pipeline.

    1. Build auditor prompt
    2. Call Ollama as completeness auditor
    3. Parse & compile results

    Returns the completeness_data dict. Raises on LLM or parse failure —
    caller (main.py) catches these so the summary output is never blocked.
    """
    user_prompt = _build_user_prompt(summary, transcript)
    raw_response = call_ollama(config, system_prompt, user_prompt, timeout)
    parsed = extract_json(raw_response)
    return _compile_result(parsed)
