"""File I/O utilities for meeting transcripts and summary output."""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


def read_transcript(file_path: Path) -> str:
    """Read and validate the meeting transcript file.

    Args:
        file_path: Path to the transcript text file.

    Returns:
        Stripped transcript content.

    Exits with code 1 if the file does not exist or is empty.
    """
    if not file_path.exists():
        print(f"Error: Input file not found: {file_path}")
        sys.exit(1)

    content = file_path.read_text(encoding="utf-8").strip()

    if not content:
        print("Error: Input file is empty.")
        sys.exit(1)

    return content


def write_output(
    output_dir: Path,
    prefix: str,
    date_str: str,
    json_data: dict[str, Any],
    markdown_text: str,
) -> tuple[Path, Path]:
    """Write JSON and Markdown summary files to the output directory.

    Args:
        output_dir: Directory for output files.
        prefix: Filename prefix (e.g. "summary", "weekly").
        date_str: Date string in YYYYMMDD format.
        json_data: Parsed meeting summary dictionary.
        markdown_text: Rendered Markdown report.

    Returns:
        Tuple of (json_path, markdown_path).
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / f"{prefix}_{date_str}.json"
    md_path = output_dir / f"{prefix}_{date_str}.md"

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)

    md_path.write_text(markdown_text, encoding="utf-8")

    return json_path, md_path


def write_eval_report(
    output_dir: Path,
    prefix: str,
    date_str: str,
    eval_data: dict[str, Any],
) -> Path:
    """Write the factuality evaluation report as a JSON file.

    Args:
        output_dir: Directory for output files.
        prefix: Filename prefix.
        date_str: Date string in YYYYMMDD format.
        eval_data: Evaluation result dictionary.

    Returns:
        Path to the written eval report file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    eval_path = output_dir / f"{prefix}_{date_str}_eval.json"
    with open(eval_path, "w", encoding="utf-8") as f:
        json.dump(eval_data, f, ensure_ascii=False, indent=2)
    return eval_path


def write_completeness_report(
    output_dir: Path,
    prefix: str,
    date_str: str,
    completeness_data: dict[str, Any],
) -> Path:
    """Write the completeness check report as a JSON file.

    Args:
        output_dir: Directory for output files.
        prefix: Filename prefix.
        date_str: Date string in YYYYMMDD format.
        completeness_data: Completeness check result dictionary.

    Returns:
        Path to the written completeness report file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / f"{prefix}_{date_str}_completeness.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(completeness_data, f, ensure_ascii=False, indent=2)
    return report_path


def write_debug_log(output_dir: Path, date_str: str, raw_response: str) -> Path:
    """Save raw LLM response to a debug log when JSON parsing fails.

    Args:
        output_dir: Directory for output files.
        date_str: Date string in YYYYMMDD format.
        raw_response: The unparseable LLM response text.

    Returns:
        Path to the written debug log file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / f"summary_{date_str}_debug.log"
    log_path.write_text(raw_response, encoding="utf-8")
    return log_path
