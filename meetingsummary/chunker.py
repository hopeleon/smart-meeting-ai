"""Transcript chunking by speaker-turn boundaries for Map-Reduce summarization.

Splits long meeting transcripts into overlapping chunks aligned to speaker
turn boundaries so each chunk stays within the effective context window of
smaller (e.g. 8B) models.

Provides a unified split_transcript() entry point that tries semantic
(topic-boundary) splitting first, falling back to speaker-turn and then
paragraph-based chunking.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import OllamaConfig

SPEAKER_PATTERN: re.Pattern[str] = re.compile(
    r"^[一-龥]{2,4}[：:]", re.MULTILINE
)


def count_chinese_chars(text: str) -> int:
    """Count Chinese characters (ignoring whitespace and punctuation).

    Args:
        text: Input text.

    Returns:
        Number of Chinese characters.
    """
    count = 0
    for ch in text:
        if "一" <= ch <= "鿿":
            count += 1
    return count


def _find_speaker_positions(transcript: str) -> list[int]:
    """Return start positions of each speaker-turn line."""
    return [m.start() for m in SPEAKER_PATTERN.finditer(transcript)]


def _extract_context(transcript: str, positions: list[int], num_turns: int = 3) -> str:
    """Extract the first *num_turns* speaker segments as meeting context."""
    if not positions:
        return ""
    if len(positions) <= num_turns:
        return transcript[: positions[-1]].strip()
    return transcript[: positions[num_turns]].strip()


def split_by_speaker_turns(
    transcript: str,
    max_turns: int = 15,
    overlap_turns: int = 2,
) -> list[str]:
    """Split a transcript into chunks aligned to speaker-turn boundaries.

    Each chunk starts at a speaker line and spans up to *max_turns* speaker
    segments.  Adjacent chunks overlap by *overlap_turns* segments so that
    facts near boundaries are not lost.

    Non-first chunks are prefixed with meeting-context text (the first 3
    speaker turns of the transcript) so the LLM understands the meeting
    purpose even when processing a middle chunk.

    If no speaker labels are detected, falls back to splitting by blank-line
    paragraphs.

    Args:
        transcript: The full meeting transcript text.
        max_turns: Maximum speaker turns per chunk (default 15).
        overlap_turns: Number of speaker turns to overlap (default 2).

    Returns:
        List of chunk strings.  Returns a single-element list if the
        transcript is shorter than *max_turns*.
    """
    positions = _find_speaker_positions(transcript)

    if not positions:
        # Fallback: split by blank-line paragraphs
        return _split_by_paragraphs(transcript, max_chars=3000)

    if len(positions) <= max_turns:
        return [transcript.strip()]

    context = _extract_context(transcript, positions, num_turns=3)

    chunks: list[str] = []
    step = max_turns - overlap_turns
    idx = 0

    while idx < len(positions):
        start = positions[idx]
        end_idx = idx + max_turns
        if end_idx < len(positions):
            end = positions[end_idx]
        else:
            end = len(transcript)

        chunk_text = transcript[start:end].strip()

        # Prepend context to non-first chunks
        if idx > 0 and context:
            chunk_text = context + "\n\n...\n\n" + chunk_text

        chunks.append(chunk_text)
        idx += step

    return chunks


def _split_by_paragraphs(transcript: str, max_chars: int = 3000) -> list[str]:
    """Fallback split by blank-line paragraphs when no speaker labels exist."""
    paragraphs = re.split(r"\n\s*\n", transcript.strip())
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)
        if current and current_len + para_len > max_chars:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = para_len
        else:
            current.append(para)
            current_len += para_len

    if current:
        chunks.append("\n\n".join(current))

    return chunks or [transcript]


# ---------------------------------------------------------------------------
# Unified splitting entry point
# ---------------------------------------------------------------------------


def split_transcript(
    transcript: str,
    max_turns: int = 15,
    *,
    config: OllamaConfig | None = None,
    use_semantic: bool = True,
) -> tuple[list[str], str]:
    """Split a transcript into chunks for Map-Reduce, with fallback chain.

    Tries semantic (topic-boundary) splitting first, then speaker-turn
    chunking, and finally paragraph-based splitting as the last resort.

    Args:
        transcript: The full meeting transcript text.
        max_turns: Maximum speaker turns per chunk (only used by fallback).
        config: LLM configuration (needed for semantic split; if ``None``
            or ``use_semantic=False``, semantic split is skipped).
        use_semantic: If ``False``, skip directly to speaker-turn chunking.

    Returns:
        Tuple of ``(chunks, method_name)`` where *method_name* is one of
        ``"semantic"``, ``"speaker_turns"``, or ``"paragraphs"``.
    """
    # 1. Semantic split (requires config)
    if use_semantic and config is not None:
        try:
            chunks = _try_semantic_split(config, transcript)
            if chunks:
                return chunks, "semantic"
        except Exception as exc:
            print(f"  Semantic split: failed — {exc}")
            print("  Falling back to speaker-turn chunking...")

    # 2. Speaker-turn chunking
    positions = _find_speaker_positions(transcript)
    if positions and len(positions) > max_turns:
        chunks = split_by_speaker_turns(transcript, max_turns=max_turns)
        return chunks, "speaker_turns"

    if positions:
        # Short enough to fit in one chunk
        return [transcript.strip()], "speaker_turns"

    # 3. Paragraph fallback (no speaker labels)
    chunks = _split_by_paragraphs(transcript)
    return chunks, "paragraphs"


def _try_semantic_split(
    config: OllamaConfig,
    transcript: str,
) -> list[str]:
    """Attempt semantic topic-boundary splitting; returns [] on failure."""
    from pathlib import Path

    from json_parser import JSONExtractionError
    from semantic_splitter import locate_segments, semantic_split

    prompt_path = Path("prompts/semantic_split_system.txt")
    if not prompt_path.exists():
        print("  Semantic split: prompt file not found, skipping.")
        return []

    system_prompt = prompt_path.read_text(encoding="utf-8")
    boundaries = semantic_split(config, system_prompt, transcript)
    segments = locate_segments(transcript, boundaries)

    if not segments:
        print("  Semantic split: locate_segments returned empty, skipping.")
        return []

    return segments
