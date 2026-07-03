"""Semantic topic-boundary splitting for meeting transcripts.

Uses an LLM to identify natural topic transitions in a long transcript,
returning logically coherent segments.  Falls back gracefully — callers
should try semantic_split() first, then degrade to rule-based chunking.

Uses sentence-level numbering: the transcript is pre-split into numbered
sentences, and the LLM returns start/end sentence numbers instead of
text markers that are hard for models to reproduce exactly.
"""

from __future__ import annotations

from typing import Any

from .config import OllamaConfig
from .json_parser import JSONExtractionError, extract_json
from .ollama_client import call_ollama

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MIN_SEGMENTS: int = 3
MAX_SEGMENTS: int = 10

_SENTENCE_END: frozenset[str] = frozenset({"。", "！", "？"})


# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------


def _sentence_breaks(text: str) -> list[int]:
    """Return sorted positions immediately after sentence-ending characters.

    A sentence boundary occurs after each 。！？ and after each newline that
    is followed by non-whitespace content (catches speaker labels, section
    headers, etc.).
    """
    breaks: list[int] = []
    n = len(text)
    for i, ch in enumerate(text):
        if ch in _SENTENCE_END:
            breaks.append(i + 1)
        elif ch == "\n" and i + 1 < n and text[i + 1] not in ("\n", " ", "\t", "\r"):
            breaks.append(i + 1)
    return sorted(set(breaks))


def _build_sentences(text: str) -> list[tuple[int, int]]:
    """Return list of (start, end) character spans for each sentence."""
    breaks = _sentence_breaks(text)
    if not breaks:
        return [(0, len(text))]

    all_breaks = [0] + sorted(breaks)
    if all_breaks[-1] < len(text):
        all_breaks.append(len(text))

    spans: list[tuple[int, int]] = []
    for i in range(len(all_breaks) - 1):
        start = all_breaks[i]
        end = all_breaks[i + 1]
        if text[start:end].strip():
            spans.append((start, end))
    return spans


def _build_numbered_text(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Build a sentence-numbered version of *text* for LLM consumption.

    Returns:
        numbered_text:  ``【1】第一句。【2】第二句。...``
        spans:          List of ``(start, end)`` character positions in the
                        original transcript, one per sentence.
    """
    spans = _build_sentences(text)
    lines: list[str] = []
    for i, (start, end) in enumerate(spans, 1):
        sentence_text = text[start:end].strip()
        lines.append(f"【{i}】{sentence_text}")
    return "\n".join(lines), spans


# ---------------------------------------------------------------------------
# Coverage validation
# ---------------------------------------------------------------------------


def validate_segment_coverage(
    segments: list[dict[str, Any]],
    total_sentences: int,
) -> None:
    """Validate that *segments* cover all sentences exactly once.

    Checks:
        - ``start_sentence <= end_sentence`` for every segment.
        - Sentence numbers are positive integers.
        - First segment starts at sentence 1.
        - Segments are contiguous (no gaps, no overlaps).
        - Last segment ends at *total_sentences*.

    Raises:
        ValueError: If any validation check fails.
    """
    if not segments:
        raise ValueError("No segments provided (need at least one segment).")

    prev_end = 0
    for i, seg in enumerate(segments):
        start = seg.get("start_sentence", 0)
        end = seg.get("end_sentence", 0)

        if not isinstance(start, int) or not isinstance(end, int):
            raise ValueError(
                f"Segment {i}: start_sentence and end_sentence must be "
                f"integers, got {type(start).__name__}/{type(end).__name__}."
            )
        if start <= 0 or end <= 0:
            raise ValueError(
                f"Segment {i}: sentence numbers must be positive "
                f"(got start={start}, end={end})."
            )
        if start > end:
            raise ValueError(
                f"Segment {i}: start_sentence ({start}) > "
                f"end_sentence ({end})."
            )
        if start != prev_end + 1:
            raise ValueError(
                f"Segment {i}: expected start_sentence={prev_end + 1}, "
                f"got {start} (gap or overlap detected)."
            )
        if end > total_sentences:
            raise ValueError(
                f"Segment {i}: end_sentence ({end}) exceeds total "
                f"sentences ({total_sentences})."
            )
        prev_end = end

    if prev_end != total_sentences:
        raise ValueError(
            f"Last segment ends at sentence {prev_end}, but there are "
            f"{total_sentences} total sentences."
        )


# ---------------------------------------------------------------------------
# Semantic split
# ---------------------------------------------------------------------------


def semantic_split(
    config: OllamaConfig,
    system_prompt: str,
    transcript: str,
    timeout: int = 0,  # 0 表示无超时限制
) -> list[dict[str, Any]]:
    """Call the LLM to identify topic boundaries using sentence numbering.

    Args:
        config: LLM connection configuration.
        system_prompt: System prompt for semantic splitting.
        transcript: The full meeting transcript.
        timeout: Request timeout in seconds.

    Returns:
        List of segment dicts:
        ``[{title, start_sentence, end_sentence, summary}, ...]``.

    Raises:
        RuntimeError: If the LLM call fails.
        JSONExtractionError: If the response cannot be parsed.
        ValueError: If segments are out of range [3, 10] or fail coverage
            validation.
    """
    numbered_text, spans = _build_numbered_text(transcript)
    total_sentences = len(spans)

    # Build user message with numbered transcript
    user_message = (
        f"以下是会议转录原文及其句子编号（共 {total_sentences} 句）：\n\n"
        f"{numbered_text}"
    )

    raw = call_ollama(config, system_prompt, user_message, timeout)
    parsed = extract_json(raw)

    if not isinstance(parsed, list):
        raise JSONExtractionError(
            f"Semantic split: expected a JSON array, got "
            f"{type(parsed).__name__}.",
            raw,
        )

    if len(parsed) < MIN_SEGMENTS or len(parsed) > MAX_SEGMENTS:
        raise ValueError(
            f"Semantic split returned {len(parsed)} segments "
            f"(allowed: {MIN_SEGMENTS}-{MAX_SEGMENTS})."
        )

    for seg in parsed:
        if not isinstance(seg, dict):
            raise JSONExtractionError(
                f"Semantic split: expected dict elements, got "
                f"{type(seg).__name__}.",
                raw,
            )
        start = seg.get("start_sentence")
        end = seg.get("end_sentence")
        if not isinstance(start, int) or not isinstance(end, int):
            raise JSONExtractionError(
                "Semantic split: segment missing valid start_sentence "
                "or end_sentence (must be integers).",
                raw,
            )

    validate_segment_coverage(parsed, total_sentences)

    return parsed


# ---------------------------------------------------------------------------
# Segment text extraction
# ---------------------------------------------------------------------------


def locate_segments(
    transcript: str,
    boundaries: list[dict[str, Any]],
) -> list[str]:
    """Extract segment texts from the transcript using sentence numbers.

    Each boundary dict must have ``start_sentence`` and ``end_sentence``
    (1-indexed).  Segments are contiguous.

    Args:
        transcript: The full meeting transcript.
        boundaries: List of segment boundary dicts from semantic_split().

    Returns:
        List of segment text strings.
    """
    if not boundaries:
        return [transcript]

    spans = _build_sentences(transcript)
    segments: list[str] = []

    for seg in boundaries:
        start_idx = seg["start_sentence"] - 1
        end_idx = seg["end_sentence"] - 1

        start_pos = spans[start_idx][0]
        end_pos = spans[end_idx][1]

        segment_text = transcript[start_pos:end_pos].strip()
        segments.append(segment_text)

    return segments
