"""Map-Reduce pipeline for long-transcript summarization.

Map phase:  split transcript → extract facts per chunk (no summarization).
Reduce phase: merge + deduplicate facts → generate structured summary JSON.

Designed for smaller (e.g. 8B) models whose effective context windows are
too limited for hour-long meeting transcripts.
"""

from difflib import SequenceMatcher
from typing import Any

from .config import OllamaConfig
from .json_parser import JSONExtractionError, extract_json
from .ollama_client import call_ollama

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SIMILARITY_THRESHOLD: float = 0.80

FACT_TYPE_LABELS: dict[str, str] = {
    "decision": "决策",
    "action_item": "待办事项",
    "data_point": "数据点",
    "risk": "风险",
    "key_info": "关键信息",
}

# ---------------------------------------------------------------------------
# Map phase
# ---------------------------------------------------------------------------


def run_map_phase(
    config: OllamaConfig,
    map_system_prompt: str,
    chunks: list[str],
    timeout: int = 0,  # 0 表示无超时限制
) -> list[dict[str, Any]]:
    """Extract facts from each chunk via independent LLM calls.

    Chunks are processed sequentially.  Individual chunk failures are logged
    and skipped — a single bad chunk does not abort the entire phase.

    Args:
        config: LLM connection configuration.
        map_system_prompt: System prompt for the Map (fact-extraction) phase.
        chunks: Transcript chunks to process.
        timeout: Per-chunk request timeout in seconds.

    Returns:
        List of parsed fact-extraction dicts, one per successful chunk.

    Raises:
        RuntimeError: If ALL chunks fail — we cannot produce a summary.
    """
    results: list[dict[str, Any]] = []
    failures = 0

    for i, chunk in enumerate(chunks):
        print(f"  Map chunk {i + 1}/{len(chunks)}...")
        try:
            raw = call_ollama(config, map_system_prompt, chunk, timeout)
            parsed = extract_json(raw)
            parsed.setdefault("chunk_id", i)
            results.append(parsed)
        except (SystemExit, JSONExtractionError, Exception) as exc:
            failures += 1
            print(f"    Warning: chunk {i + 1} failed — {exc}")

    if not results:
        raise RuntimeError(
            f"Map phase: all {len(chunks)} chunks failed. Cannot continue."
        )

    if failures:
        print(f"  Map phase: {len(results)}/{len(chunks)} chunks succeeded "
              f"({failures} failed, skipped).")

    return results


# ---------------------------------------------------------------------------
# Merge & deduplicate
# ---------------------------------------------------------------------------


def _descriptions_similar(a: str, b: str) -> bool:
    """Return True if two description strings are near-identical."""
    if a == b:
        return True
    ratio = SequenceMatcher(None, a, b).ratio()
    return ratio >= SIMILARITY_THRESHOLD


def merge_deduplicate(facts_list: list[dict[str, Any]]) -> str:
    """Merge facts from all chunks, deduplicate, and format as structured text.

    Deduplication strategy:
    1. Exact source_quote match → skip duplicate.
    2. Near-identical description (≥80% similarity) → merge into one entry.

    The output is a human-readable text block organized by fact type, suitable
    as input for the Reduce-phase LLM call.

    Args:
        facts_list: List of parsed Map-phase outputs.

    Returns:
        Formatted text ready for the Reduce summarization prompt.
    """
    seen_quotes: set[str] = set()
    merged: list[dict[str, Any]] = []

    for result in facts_list:
        for fact in result.get("facts", []):
            quote = fact.get("source_quote", "").strip()

            # Exact quote duplicate
            if quote and quote in seen_quotes:
                continue

            # Near-duplicate by description
            desc = fact.get("description", "")
            is_dup = False
            for existing in merged:
                if _descriptions_similar(desc, existing.get("description", "")):
                    is_dup = True
                    break
            if is_dup:
                if quote:
                    seen_quotes.add(quote)
                continue

            if quote:
                seen_quotes.add(quote)
            merged.append(fact)

    return _format_facts_for_reduce(merged)


def _format_facts_for_reduce(facts: list[dict[str, Any]]) -> str:
    """Organize facts by type and render as readable text for the Reduce step."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for f in facts:
        ftype = f.get("type", "key_info")
        by_type.setdefault(ftype, []).append(f)

    sections: list[str] = [
        "以下是本次会议中提取到的全部客观事实，请基于这些事实生成完整的会议摘要 JSON。",
        "这些事实已经过去重处理，每条事实均附带原文引用。",
        "请严格基于以下事实撰写摘要，不要添加事实清单中没有的信息。",
        "",
    ]

    for ftype, label in FACT_TYPE_LABELS.items():
        items = by_type.get(ftype, [])
        if not items:
            continue
        sections.append(f"## {label}")
        sections.append("")
        for item in items:
            desc = item.get("description", "")
            speaker = item.get("speaker", "")
            quote = item.get("source_quote", "")
            line = f"- {desc}"
            if speaker:
                line += f"（发言人：{speaker}）"
            sections.append(line)
            if quote:
                sections.append(f"  原文引用：{quote}")
        sections.append("")

    sections.append("请基于以上事实，输出完整的会议摘要 JSON。")
    return "\n".join(sections)


# ---------------------------------------------------------------------------
# Reduce phase
# ---------------------------------------------------------------------------


def run_reduce_phase(
    config: OllamaConfig,
    summary_system_prompt: str,
    merged_facts: str,
    timeout: int = 0,  # 0 表示无超时限制
) -> str:
    """Generate final summary JSON from merged, deduplicated facts.

    Args:
        config: LLM connection configuration.
        summary_system_prompt: The (anti-hallucination-enhanced) summary prompt.
        merged_facts: Formatted merged facts text from merge_deduplicate().
        timeout: Request timeout in seconds (default 180 — Reduce is complex).

    Returns:
        Raw LLM response string containing the JSON summary.
    """
    print("  Reduce: generating final summary from merged facts...")
    return call_ollama(config, summary_system_prompt, merged_facts, timeout)
