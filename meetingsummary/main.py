"""Meeting Summary — main entry point.

Uses a local Ollama LLM to summarize meeting transcripts into structured
JSON and human-readable Markdown reports.  Long transcripts (>5000 Chinese
characters) are automatically processed through a Map-Reduce pipeline to
reduce hallucination on smaller (e.g. 8B) models.
"""

import argparse
import atexit
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

from .action_items import extract_action_items, write_actions_csv, write_actions_json
from .chunker import count_chinese_chars, split_transcript
from .completeness_check import run_completeness_check
from .config import load_config
from .evaluator import run_evaluation
from .file_handler import (
    read_transcript,
    write_completeness_report,
    write_debug_log,
    write_eval_report,
    write_output,
)
from .json_parser import JSONExtractionError, extract_json, validate_schema
from .map_reduce import merge_deduplicate, run_map_phase, run_reduce_phase
from .markdown_generator import generate_markdown
from .ollama_client import call_ollama
from .verify import run_verification

PROMPT_PATH = Path("prompts/summary_system.txt")
EVAL_PROMPT_PATH = Path("prompts/fact_check_system.txt")
ACTION_PROMPT_PATH = Path("prompts/action_items_system.txt")
COMPLETENESS_PROMPT_PATH = Path("prompts/completeness_system.txt")
MAP_PROMPT_PATH = Path("prompts/map_extract_system.txt")
VERIFY_PROMPT_PATH = Path("prompts/verify_system.txt")
CONFIG_PATH = Path("config.json")

# 供 atexit cleanup 使用
_loaded_model_name: str | None = None


def _cleanup_ollama(model: str | None) -> None:
    """释放 Ollama 模型显存。在任何方式退出进程前都会执行。"""
    if not model:
        return
    print(f"[Cleanup] 释放 Ollama 模型显存: ollama stop {model}", flush=True)
    subprocess.run(["ollama", "stop", model], capture_output=True)
    time.sleep(1)
    result = subprocess.run(
        ["nvidia-smi", "--query-gpu=memory.used,memory.free",
         "--format=csv,noheader,nounits"],
        capture_output=True, text=True
    )
    print(f"[Cleanup] GPU 显存已释放，当前: {result.stdout.strip()}", flush=True)

DEFAULT_CHAR_THRESHOLD: int = 5000
DEFAULT_CHUNK_SIZE: int = 15


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Generate structured meeting summaries via local LLM (Ollama)."
    )
    parser.add_argument(
        "-i", "--input",
        required=True,
        type=Path,
        help="Path to the meeting transcript text file.",
    )
    parser.add_argument(
        "-o", "--output-dir",
        type=Path,
        default=Path("output"),
        help="Directory for output files (default: output/).",
    )
    parser.add_argument(
        "--prefix",
        type=str,
        default="summary",
        help="Filename prefix (default: summary).",
    )
    parser.add_argument(
        "--skip-eval",
        action="store_true",
        help="Skip the factuality evaluation step.",
    )
    parser.add_argument(
        "--action-items",
        action="store_true",
        help="Enable independent action-item extraction with classification.",
    )
    parser.add_argument(
        "--skip-actions",
        action="store_true",
        help="Explicitly skip action-item extraction (future default override).",
    )
    parser.add_argument(
        "--skip-completeness",
        action="store_true",
        help="Skip the completeness check step.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Run post-hoc verification and auto-correction pass.",
    )
    parser.add_argument(
        "--no-map-reduce",
        action="store_true",
        help="Force skip Map-Reduce even for long transcripts.",
    )
    parser.add_argument(
        "--no-semantic-split",
        action="store_true",
        help="Force fallback to speaker-turn chunking (disable semantic split).",
    )
    parser.add_argument(
        "--no-stop",
        action="store_true",
        help="Skip 'ollama stop' after all LLM calls (useful for debugging).",
    )
    parser.add_argument(
        "--map-reduce-threshold",
        type=int,
        default=DEFAULT_CHAR_THRESHOLD,
        help=(
            "Chinese-character count threshold for auto Map-Reduce "
            f"(default: {DEFAULT_CHAR_THRESHOLD})."
        ),
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Speaker turns per chunk (default: {DEFAULT_CHUNK_SIZE}).",
    )
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Direct summarization (short texts — existing behaviour)
# ---------------------------------------------------------------------------


def _run_direct_pipeline(
    config, system_prompt, transcript
) -> dict:
    """Single-pass summarization for short transcripts."""
    print("Sending request to Ollama...")
    raw_response = call_ollama(config, system_prompt, transcript)
    return extract_json(raw_response)


# ---------------------------------------------------------------------------
# Map-Reduce summarization (long texts)
# ---------------------------------------------------------------------------


def _run_map_reduce_pipeline(
    config, summary_prompt, transcript, args
) -> dict:
    """Map-Reduce pipeline for long transcripts."""
    map_prompt = MAP_PROMPT_PATH.read_text(encoding="utf-8")

    # 1. Chunk
    use_semantic = not args.no_semantic_split
    print(f"Transcript too long ({count_chinese_chars(transcript)} chars), "
          f"splitting into chunks...")
    chunks, method = split_transcript(
        transcript,
        max_turns=args.chunk_size,
        config=config,
        use_semantic=use_semantic,
    )
    print(f"  Split into {len(chunks)} chunks (method: {method}).")

    # 2. Map
    print("Map phase: extracting facts from each chunk...")
    facts_list = run_map_phase(config, map_prompt, chunks)
    print(f"  Extracted facts from {len(facts_list)} chunks.")

    # 3. Merge
    print("Merging and deduplicating facts...")
    merged_facts = merge_deduplicate(facts_list)
    print(f"  Merged facts: {len(merged_facts)} chars.")

    # 4. Reduce
    raw_response = run_reduce_phase(config, summary_prompt, merged_facts)
    return extract_json(raw_response)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    global _loaded_model_name

    args = parse_args()

    # 1. Load configuration
    config = load_config(CONFIG_PATH)
    print(f"Using model: {config.model} @ {config.base_url}")

    # 注册 atexit，确保任何退出方式都能释放 Ollama 模型
    if not args.no_stop:
        _loaded_model_name = config.model
        atexit.register(_cleanup_ollama, _loaded_model_name)

    # 2. Load system prompts
    summary_prompt = PROMPT_PATH.read_text(encoding="utf-8")

    # 3. Read and validate transcript
    transcript = read_transcript(args.input)

    # 4. Summarization — auto-select direct vs. Map-Reduce
    char_count = count_chinese_chars(transcript)
    use_map_reduce = (
        char_count > args.map_reduce_threshold and not args.no_map_reduce
    )

    print("Parsing LLM response...")
    try:
        if use_map_reduce:
            summary_dict = _run_map_reduce_pipeline(
                config, summary_prompt, transcript, args
            )
        else:
            summary_dict = _run_direct_pipeline(
                config, summary_prompt, transcript
            )
    except JSONExtractionError:
        date_str = date.today().strftime("%Y%m%d")
        debug_path = write_debug_log(args.output_dir, date_str, "")
        print(
            "Warning: Could not parse JSON from LLM response. "
            f"Debug info saved to: {debug_path}"
        )
        print("Please check the debug log and try again.")
        # 不使用 sys.exit(0)，让 atexit 清理函数正常执行
        return

    # 5. Validate schema
    missing = validate_schema(summary_dict)
    if missing:
        print(f"Warning: Some fields are missing from the response: "
              f"{', '.join(missing)}")

    # 6. Post-hoc verification (optional, non-blocking)
    if args.verify:
        print("Running post-hoc verification...")
        verify_prompt = VERIFY_PROMPT_PATH.read_text(encoding="utf-8")
        try:
            verify_result = run_verification(
                config, verify_prompt, transcript, summary_dict
            )
        except (RuntimeError, JSONExtractionError, Exception) as exc:
            if isinstance(exc, RuntimeError):
                print(f"  Warning: Verification skipped — {exc}")
            elif isinstance(exc, JSONExtractionError):
                print("  Warning: Verification skipped — "
                      "verifier returned unparseable response.")
            else:
                print(f"  Warning: Verification skipped: {exc}")
        else:
            n_corrections = len(verify_result["corrections"])
            if n_corrections > 0:
                summary_dict = verify_result["corrected_summary"]
                print(f"  Verification: {n_corrections} correction(s) applied.")
            else:
                print("  Verification: no corrections needed.")

    # 7. Factuality evaluation (skippable, non-blocking)
    eval_data: dict | None = None
    if not args.skip_eval:
        print("Running factuality evaluation...")
        eval_system_prompt = EVAL_PROMPT_PATH.read_text(encoding="utf-8")
        try:
            eval_data = run_evaluation(
                config, eval_system_prompt, transcript, summary_dict
            )
        except (RuntimeError, JSONExtractionError, Exception) as exc:
            if isinstance(exc, RuntimeError):
                print(f"  Warning: Factuality evaluation skipped — {exc}")
            elif isinstance(exc, JSONExtractionError):
                print("  Warning: Factuality evaluation skipped — "
                      "evaluator returned unparseable response.")
            else:
                print(f"  Warning: Factuality evaluation skipped due to error: {exc}")
        else:
            score = eval_data["factuality_score"]
            threshold = eval_data["threshold"]
            if score >= threshold:
                print(f"  Factuality score: {score:.1f}%  (PASS >= {threshold}%)")
            else:
                print(f"  Factuality score: {score:.1f}%  (BELOW threshold {threshold}%)")
                print("  Warning: Summary may contain factual errors. "
                      "Review the evaluation report.")

    # 8. Independent action-item extraction (opt-in, non-blocking)
    action_items: list[dict] | None = None
    if args.action_items and not args.skip_actions:
        print("Extracting action items independently...")
        action_system_prompt = ACTION_PROMPT_PATH.read_text(encoding="utf-8")
        try:
            action_items = extract_action_items(
                config, action_system_prompt, transcript
            )
        except (RuntimeError, JSONExtractionError, Exception) as exc:
            if isinstance(exc, RuntimeError):
                print(f"  Warning: Action-item extraction skipped — {exc}")
            elif isinstance(exc, JSONExtractionError):
                print("  Warning: Action-item extraction skipped — "
                      "unparseable response.")
            else:
                print(f"  Warning: Action-item extraction skipped: {exc}")
        else:
            print(f"  Extracted {len(action_items)} action items "
                  f"({sum(1 for a in action_items if a.get('priority') in ('P0','P1'))} high-priority).")

    # 9. Completeness check (skippable, non-blocking)
    completeness_data: dict | None = None
    if not args.skip_completeness:
        print("Running completeness check...")
        completeness_system_prompt = COMPLETENESS_PROMPT_PATH.read_text(encoding="utf-8")
        try:
            completeness_data = run_completeness_check(
                config, completeness_system_prompt, transcript, summary_dict
            )
        except (RuntimeError, JSONExtractionError, Exception) as exc:
            if isinstance(exc, RuntimeError):
                print(f"  Warning: Completeness check skipped — {exc}")
            elif isinstance(exc, JSONExtractionError):
                print("  Warning: Completeness check skipped — "
                      "auditor returned unparseable response.")
            else:
                print(f"  Warning: Completeness check skipped due to error: {exc}")
        else:
            score = completeness_data["coverage_score"]
            threshold = completeness_data["threshold"]
            if completeness_data["passed"]:
                print(f"  Coverage score: {score:.1f}%  (PASS >= {threshold:.0f}%)")
            else:
                print(f"  Coverage score: {score:.1f}%  (BELOW threshold {threshold:.0f}%)")

    # 10. Generate Markdown (after completeness check so it can embed the footer)
    markdown_text = generate_markdown(summary_dict, completeness_data)

    # 11. Write output files
    date_str = date.today().strftime("%Y%m%d")
    json_path, md_path = write_output(
        args.output_dir, args.prefix, date_str, summary_dict, markdown_text
    )

    print("Summary generated successfully:")
    print(f"  JSON:    {json_path}")
    print(f"  Markdown: {md_path}")

    if eval_data is not None:
        eval_path = write_eval_report(args.output_dir, args.prefix, date_str, eval_data)
        print(f"  Eval:     {eval_path}")

    if action_items is not None:
        csv_path = write_actions_csv(args.output_dir, args.prefix, date_str, action_items)
        actions_json_path = write_actions_json(args.output_dir, args.prefix, date_str, action_items)
        print(f"  Actions CSV:  {csv_path}")
        print(f"  Actions JSON: {actions_json_path}")

    if completeness_data is not None:
        completeness_path = write_completeness_report(
            args.output_dir, args.prefix, date_str, completeness_data
        )
        print(f"  Completeness:  {completeness_path}")

    # atexit 会在进程退出时自动释放 Ollama 模型显存，无需手动调用
    print("[Cleanup] 所有 LLM 调用完成，进程退出时将自动释放 Ollama 模型显存", flush=True)


if __name__ == "__main__":
    main()
