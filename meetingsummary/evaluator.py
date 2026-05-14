"""Factuality evaluation for meeting summaries.

Evaluates each factual claim in a generated summary against the original
transcript by calling an Ollama model as a second-pass fact-checker.
"""

from typing import Any

from config import OllamaConfig
from json_parser import JSONExtractionError, extract_json
from ollama_client import call_ollama

FACTUALITY_THRESHOLD: float = 95.0
VALID_VERDICTS: frozenset[str] = frozenset({"consistent", "distorted", "hallucination"})


# ---------------------------------------------------------------------------
# Claim extraction
# ---------------------------------------------------------------------------

def _skip_value(value: Any) -> bool:
    """Return True if the value is a placeholder that carries no factual claim."""
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == "" or value.strip().upper() in {"N/A", "TBD", "NA"}
    if isinstance(value, list):
        return len(value) == 0
    return False


def _extract_meeting_claims(meeting: dict[str, Any]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    field_templates: list[tuple[str, str]] = [
        ("title", '会议标题是"{value}"'),
        ("date", "会议日期为{value}"),
        ("time", "会议时间为{value}"),
        ("location", "会议地点在{value}"),
        ("host", "会议由{value}主持"),
    ]
    for field, template in field_templates:
        val = meeting.get(field)
        if not _skip_value(val):
            claims.append({
                "claim_id": f"meeting.{field}",
                "description": template.format(value=val),
            })

    attendees = meeting.get("attendees")
    if isinstance(attendees, list) and attendees:
        claims.append({
            "claim_id": "meeting.attendees",
            "description": "参会人员有：" + "、".join(attendees),
        })

    agenda = meeting.get("agenda")
    if isinstance(agenda, list) and agenda:
        claims.append({
            "claim_id": "meeting.agenda",
            "description": "会议议程包括：" + "、".join(agenda),
        })
    return claims


def _extract_discussion_claims(points: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for i, p in enumerate(points):
        topic = p.get("topic", "")
        summary = p.get("summary", "")
        speaker = p.get("speaker", "")
        parts = [f"[讨论] {topic}：{summary}"]
        if not _skip_value(speaker):
            parts.append(f"发言人：{speaker}")
        claims.append({
            "claim_id": f"dp.{i}",
            "description": "".join(parts),
        })
    return claims


def _extract_decision_claims(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for i, d in enumerate(decisions):
        desc = d.get("description", "")
        responsible = d.get("responsible", "")
        deadline = d.get("deadline", "")
        parts = [f"[决策] {desc}"]
        if not _skip_value(responsible):
            parts.append(f"，负责人{responsible}")
        if not _skip_value(deadline):
            parts.append(f"，截止{deadline}")
        claims.append({
            "claim_id": f"decision.{i}",
            "description": "".join(parts),
        })
    return claims


def _extract_action_claims(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    claims: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        task = item.get("task", "")
        assignee = item.get("assignee", "")
        due = item.get("due", "")
        parts = [f"[行动项] {task}"]
        if not _skip_value(assignee):
            parts.append(f"，由{assignee}执行")
        if not _skip_value(due):
            parts.append(f"，截止{due}")
        claims.append({
            "claim_id": f"action.{i}",
            "description": "".join(parts),
        })
    return claims


def _extract_next_meeting_claims(next_mtg: dict[str, Any]) -> list[dict[str, Any]]:
    date_v = next_mtg.get("date", "")
    time_v = next_mtg.get("time", "")
    location_v = next_mtg.get("location", "")
    if not _skip_value(date_v) or not _skip_value(time_v) or not _skip_value(location_v):
        desc = f"下次会议定于{date_v} {time_v}在{location_v}举行"
        yield {
            "claim_id": "next.datetime_location",
            "description": desc,
        }
    agenda = next_mtg.get("tentative_agenda", "")
    if not _skip_value(agenda):
        yield {
            "claim_id": "next.agenda",
            "description": f"下次会议暂定议程：{agenda}",
        }


def extract_claims(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract all verifiable factual claims from the summary dict.

    Skips empty, N/A, TBD, and empty-array fields — these carry no assertion.
    """
    claims: list[dict[str, Any]] = []
    claims.extend(_extract_meeting_claims(summary.get("meeting", {})))
    claims.extend(_extract_discussion_claims(summary.get("discussion_points", [])))
    claims.extend(_extract_decision_claims(summary.get("decisions", [])))
    claims.extend(_extract_action_claims(summary.get("action_items", [])))
    for risk in summary.get("issues_risks", []):
        if not _skip_value(risk):
            claims.append({
                "claim_id": f"issues_risks.{summary['issues_risks'].index(risk)}",
                "description": f"[问题/风险] {risk}",
            })
    claims.extend(_extract_next_meeting_claims(summary.get("next_meeting", {})))
    return claims


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

def _build_user_prompt(claims: list[dict[str, Any]], transcript: str) -> str:
    """Build the evaluator user-prompt containing the transcript and claim list."""
    claim_lines: list[str] = []
    for i, c in enumerate(claims, 1):
        claim_lines.append(f"{i}. [{c['claim_id']}] {c['description']}")

    return (
        "## 原始会议记录\n\n"
        f"{transcript}\n\n"
        "---\n\n"
        "## 待检查的声明\n\n"
        f"请对以下 {len(claims)} 条声明逐一判断其与原始会议记录的事实一致性：\n\n"
        + "\n".join(claim_lines)
    )


# ---------------------------------------------------------------------------
# Score calculation
# ---------------------------------------------------------------------------

def calculate_score(verdicts: list[dict[str, Any]]) -> float:
    """Calculate factuality_score = consistent / total * 100."""
    total = len(verdicts)
    if total == 0:
        return 100.0
    consistent_count = sum(
        1 for v in verdicts if v.get("verdict") == "consistent"
    )
    return consistent_count / total * 100


# ---------------------------------------------------------------------------
# Result compilation
# ---------------------------------------------------------------------------

def _empty_eval_result() -> dict[str, Any]:
    return {
        "factuality_score": 100.0,
        "total_claims": 0,
        "consistent": 0,
        "distorted": 0,
        "hallucination": 0,
        "error": 0,
        "threshold": FACTUALITY_THRESHOLD,
        "passed": True,
        "verdicts": [],
    }


def _compile_result(
    claims: list[dict[str, Any]],
    raw_verdicts: list[dict[str, Any]],
) -> dict[str, Any]:
    """Align model verdicts with claims, falling back to positional matching.

    First attempts claim_id matching.  If fewer than half the claims find a
    matching verdict, falls back to positional alignment (zip by index), which
    is robust against models that return sequential IDs like "1", "2", "3"
    instead of the provided bracket-format claim_ids.
    """
    # Build claim_id lookup
    verdict_map: dict[str, dict[str, Any]] = {}
    for v in raw_verdicts:
        cid = v.get("claim_id", "")
        verdict_map[cid] = v

    # Check match rate
    match_count = sum(1 for c in claims if c["claim_id"] in verdict_map)
    use_positional = len(claims) > 0 and match_count / len(claims) < 0.5

    aligned: list[dict[str, Any]] = []
    stats = {"consistent": 0, "distorted": 0, "hallucination": 0, "error": 0}

    for i, claim in enumerate(claims):
        cid = claim["claim_id"]

        if use_positional and i < len(raw_verdicts):
            matched = raw_verdicts[i]
        elif use_positional:
            matched = None
        else:
            matched = verdict_map.get(cid)

        if matched is None:
            verdict = "error"
            reasoning = "评估模型未返回该声明的判定"
        else:
            verdict = matched.get("verdict", "error")
            reasoning = matched.get("reasoning", "")
            if verdict not in VALID_VERDICTS:
                reasoning = f"评估模型返回了非法判定值: {matched.get('verdict')}"
                verdict = "error"

        stats[verdict] = stats.get(verdict, 0) + 1
        aligned.append({
            "claim_id": cid,
            "claim": claim["description"],
            "verdict": verdict,
            "reasoning": reasoning,
        })

    total = len(aligned)
    score = (stats["consistent"] / total * 100) if total > 0 else 100.0

    return {
        "factuality_score": round(score, 1),
        "total_claims": total,
        "consistent": stats["consistent"],
        "distorted": stats["distorted"],
        "hallucination": stats["hallucination"],
        "error": stats["error"],
        "threshold": FACTUALITY_THRESHOLD,
        "passed": score >= FACTUALITY_THRESHOLD,
        "verdicts": aligned,
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_evaluation(
    config: OllamaConfig,
    system_prompt: str,
    transcript: str,
    summary: dict[str, Any],
    timeout: int = 120,
) -> dict[str, Any]:
    """Run the full factuality evaluation pipeline.

    1. Extract claims from summary
    2. Build evaluator prompt
    3. Call Ollama as fact-checker
    4. Parse & compile results

    Returns the eval_data dict.  Raises on LLM or parse failure — caller
    (main.py) catches these so the summary output is never blocked.
    """
    claims = extract_claims(summary)
    if not claims:
        return _empty_eval_result()

    user_prompt = _build_user_prompt(claims, transcript)
    raw_response = call_ollama(config, system_prompt, user_prompt, timeout)
    parsed = extract_json(raw_response)
    return _compile_result(claims, parsed.get("verdicts", []))
