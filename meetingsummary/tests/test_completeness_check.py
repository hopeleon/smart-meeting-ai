"""Unit tests for completeness_check.py."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from completeness_check import (
    COVERAGE_THRESHOLD,
    _build_checkpoint_text,
    _build_user_prompt,
    _compile_result,
    calculate_coverage,
    extract_checkpoints,
    run_completeness_check,
)
from config import OllamaConfig

CONFIG = OllamaConfig(base_url="http://localhost:11434", model="qwen3:8b")
PROMPT = "You are a completeness auditor."
TRANSCRIPT = "会议记录：张伟主持，李娜汇报Q2 roadmap，王强负责开发。"

FIXTURES = Path(__file__).resolve().parent / "fixtures"


# ---------------------------------------------------------------------------
# extract_checkpoints
# ---------------------------------------------------------------------------

class TestExtractCheckpoints(unittest.TestCase):
    def setUp(self) -> None:
        raw = (FIXTURES / "summary_with_new_schema.json").read_text(encoding="utf-8")
        self.summary = json.loads(raw)

    def test_extracts_agenda_items(self) -> None:
        cp = extract_checkpoints(self.summary)
        self.assertEqual(cp["agenda_items"], ["Sprint review", "Q2 roadmap", "Risk assessment"])

    def test_extracts_speakers(self) -> None:
        cp = extract_checkpoints(self.summary)
        self.assertIn("Zhang Wei", cp["speakers"])
        self.assertIn("Li Na", cp["speakers"])

    def test_excludes_unknown_speaker(self) -> None:
        summary = {
            "meeting": {"agenda": []},
            "discussion_points": [
                {"topic": "X", "summary": "Y", "speaker": "Unknown"}
            ],
        }
        cp = extract_checkpoints(summary)
        self.assertNotIn("Unknown", cp["speakers"])

    def test_excludes_duplicate_speakers(self) -> None:
        summary = {
            "meeting": {"agenda": []},
            "discussion_points": [
                {"topic": "A", "summary": "...", "speaker": "张伟"},
                {"topic": "B", "summary": "...", "speaker": "张伟"},
            ],
        }
        cp = extract_checkpoints(summary)
        self.assertEqual(cp["speakers"], ["张伟"])

    def test_sections_list(self) -> None:
        cp = extract_checkpoints(self.summary)
        self.assertIn("tldr", cp["sections"])
        self.assertIn("action_items", cp["sections"])
        self.assertEqual(len(cp["sections"]), 7)

    def test_empty_summary(self) -> None:
        cp = extract_checkpoints({})
        self.assertEqual(cp["agenda_items"], [])
        self.assertEqual(cp["speakers"], [])


# ---------------------------------------------------------------------------
# _build_checkpoint_text
# ---------------------------------------------------------------------------

class TestBuildCheckpointText(unittest.TestCase):
    def test_renders_agenda_and_speakers(self) -> None:
        cp = {
            "agenda_items": ["Sprint review", "Roadmap"],
            "speakers": ["张伟", "李娜"],
        }
        text = _build_checkpoint_text(cp)
        self.assertIn("Sprint review", text)
        self.assertIn("Roadmap", text)
        self.assertIn("张伟", text)
        self.assertIn("李娜", text)

    def test_empty_checkpoints(self) -> None:
        cp = {"agenda_items": [], "speakers": []}
        text = _build_checkpoint_text(cp)
        self.assertIn("（无）", text)


# ---------------------------------------------------------------------------
# _build_user_prompt
# ---------------------------------------------------------------------------

class TestBuildUserPrompt(unittest.TestCase):
    def setUp(self) -> None:
        raw = (FIXTURES / "summary_with_new_schema.json").read_text(encoding="utf-8")
        self.summary = json.loads(raw)

    def test_includes_transcript(self) -> None:
        prompt = _build_user_prompt(self.summary, TRANSCRIPT)
        self.assertIn(TRANSCRIPT, prompt)

    def test_includes_summary_json(self) -> None:
        prompt = _build_user_prompt(self.summary, TRANSCRIPT)
        self.assertIn("Q2 Planning Sync", prompt)

    def test_includes_checkpoints(self) -> None:
        prompt = _build_user_prompt(self.summary, TRANSCRIPT)
        self.assertIn("已提取的议程项", prompt)
        self.assertIn("已提取的发言人", prompt)


# ---------------------------------------------------------------------------
# calculate_coverage
# ---------------------------------------------------------------------------

class TestCalculateCoverage(unittest.TestCase):
    def test_full_coverage(self) -> None:
        data = {
            "agenda_check": {"total": 3, "covered": 3, "missing": []},
            "speaker_check": {"total": 3, "covered": 3, "missing": []},
            "section_check": {
                "sections_present": ["a", "b", "c"],
                "sections_empty": [],
                "sections_missing": [],
            },
        }
        self.assertAlmostEqual(calculate_coverage(data), 100.0)

    def test_partial_coverage(self) -> None:
        data = {
            "agenda_check": {"total": 4, "covered": 2, "missing": ["A", "B"]},
            "speaker_check": {"total": 2, "covered": 2, "missing": []},
            "section_check": {
                "sections_present": ["a", "b"],
                "sections_empty": ["c"],
                "sections_missing": ["d"],
            },
        }
        # agenda: 2/4 = 0.5, speaker: 2/2 = 1.0, section: 2/4 = 0.5
        # mean = (0.5 + 1.0 + 0.5) / 3 = 0.666... * 100 = ~66.67
        self.assertAlmostEqual(calculate_coverage(data), 66.7, delta=0.1)

    def test_empty_data_returns_100(self) -> None:
        self.assertEqual(calculate_coverage({}), 100.0)

    def test_zero_totals_skipped(self) -> None:
        data = {
            "agenda_check": {"total": 0, "covered": 0, "missing": []},
            "speaker_check": {"total": 0, "covered": 0, "missing": []},
            "section_check": {
                "sections_present": [],
                "sections_empty": [],
                "sections_missing": [],
            },
        }
        self.assertEqual(calculate_coverage(data), 100.0)


# ---------------------------------------------------------------------------
# _compile_result
# ---------------------------------------------------------------------------

class TestCompileResult(unittest.TestCase):
    def setUp(self) -> None:
        raw = (FIXTURES / "completeness_valid.json").read_text(encoding="utf-8")
        self.raw_data = json.loads(raw)

    def test_adds_coverage_score(self) -> None:
        result = _compile_result(self.raw_data)
        self.assertIn("coverage_score", result)
        self.assertGreater(result["coverage_score"], 0)

    def test_adds_threshold_and_passed(self) -> None:
        result = _compile_result(self.raw_data)
        self.assertEqual(result["threshold"], COVERAGE_THRESHOLD)
        self.assertIn("passed", result)

    def test_preserves_input_fields(self) -> None:
        result = _compile_result(self.raw_data)
        self.assertIn("agenda_check", result)
        self.assertIn("speaker_check", result)
        self.assertIn("section_check", result)
        self.assertEqual(result["overall_assessment"], self.raw_data["overall_assessment"])


# ---------------------------------------------------------------------------
# run_completeness_check
# ---------------------------------------------------------------------------

class TestRunCompletenessCheck(unittest.TestCase):
    def setUp(self) -> None:
        raw = (FIXTURES / "summary_with_new_schema.json").read_text(encoding="utf-8")
        self.summary = json.loads(raw)
        self.completeness_raw = (FIXTURES / "completeness_valid.json").read_text(encoding="utf-8")

    def test_successful_run(self) -> None:
        mock_resp = self.completeness_raw
        with patch("completeness_check.call_ollama", return_value=mock_resp):
            result = run_completeness_check(CONFIG, PROMPT, TRANSCRIPT, self.summary)
        self.assertIn("coverage_score", result)
        self.assertIn("passed", result)

    def test_parse_failure_raises(self) -> None:
        with patch("completeness_check.call_ollama", return_value="not json"):
            from json_parser import JSONExtractionError
            with self.assertRaises(JSONExtractionError):
                run_completeness_check(CONFIG, PROMPT, TRANSCRIPT, self.summary)


if __name__ == "__main__":
    unittest.main()
