"""Unit tests for evaluator.py."""

import json
import unittest
from pathlib import Path
from unittest.mock import patch

from config import OllamaConfig
from evaluator import (
    _build_user_prompt,
    _compile_result,
    _empty_eval_result,
    _skip_value,
    calculate_score,
    extract_claims,
    run_evaluation,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CONFIG = OllamaConfig(base_url="http://localhost:11434", model="qwen3:8b")
EVAL_PROMPT = "You are a fact checker."


def _load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# _skip_value
# ---------------------------------------------------------------------------

class TestSkipValue(unittest.TestCase):
    def test_skip_none(self) -> None:
        self.assertTrue(_skip_value(None))

    def test_skip_empty_string(self) -> None:
        self.assertTrue(_skip_value(""))

    def test_skip_na(self) -> None:
        self.assertTrue(_skip_value("N/A"))
        self.assertTrue(_skip_value("n/a"))

    def test_skip_tbd(self) -> None:
        self.assertTrue(_skip_value("TBD"))

    def test_skip_empty_list(self) -> None:
        self.assertTrue(_skip_value([]))

    def test_keep_valid_string(self) -> None:
        self.assertFalse(_skip_value("张伟"))

    def test_keep_nonempty_list(self) -> None:
        self.assertFalse(_skip_value(["李娜"]))


# ---------------------------------------------------------------------------
# extract_claims
# ---------------------------------------------------------------------------

class TestExtractClaims(unittest.TestCase):
    def setUp(self) -> None:
        raw = (FIXTURES / "valid_summary.json").read_text(encoding="utf-8")
        self.full_summary = json.loads(raw)

    def test_full_summary_has_claims(self) -> None:
        claims = extract_claims(self.full_summary)
        self.assertGreaterEqual(len(claims), 10)

    def test_meeting_title_claim(self) -> None:
        claims = extract_claims(self.full_summary)
        cids = {c["claim_id"] for c in claims}
        self.assertIn("meeting.title", cids)

    def test_claim_id_format(self) -> None:
        claims = extract_claims(self.full_summary)
        for c in claims:
            self.assertIn("claim_id", c)
            self.assertIn("description", c)
            self.assertTrue(c["claim_id"], "claim_id must not be empty")

    def test_chinese_in_claims(self) -> None:
        claims = extract_claims(self.full_summary)
        combined = " ".join(c["description"] for c in claims)
        self.assertTrue(any("一" <= ch <= "鿿" for ch in combined))

    def test_summary_with_nas_skipped(self) -> None:
        raw = (FIXTURES / "summary_with_nas.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        claims = extract_claims(data)
        # Only meeting.title ("Weekly Standup") should produce a claim
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0]["claim_id"], "meeting.title")

    def test_empty_summary(self) -> None:
        claims = extract_claims({})
        self.assertEqual(claims, [])

    def test_empty_discussion_points_no_claims(self) -> None:
        claims = extract_claims({"meeting": {"title": "X"}})
        self.assertEqual(len(claims), 1)


# ---------------------------------------------------------------------------
# _build_user_prompt
# ---------------------------------------------------------------------------

class TestBuildUserPrompt(unittest.TestCase):
    def setUp(self) -> None:
        self.transcript = "会议记录内容。"
        self.claims = [
            {"claim_id": "meeting.title", "description": "会议标题是\"Test\""},
            {"claim_id": "meeting.host", "description": "会议由张伟主持"},
        ]

    def test_contains_transcript(self) -> None:
        prompt = _build_user_prompt(self.claims, self.transcript)
        self.assertIn(self.transcript, prompt)

    def test_contains_all_claims(self) -> None:
        prompt = _build_user_prompt(self.claims, self.transcript)
        for c in self.claims:
            self.assertIn(c["description"], prompt)

    def test_numbered_format(self) -> None:
        prompt = _build_user_prompt(self.claims, self.transcript)
        self.assertIn("1.", prompt)
        self.assertIn("2.", prompt)

    def test_claim_id_in_prompt(self) -> None:
        prompt = _build_user_prompt(self.claims, self.transcript)
        self.assertIn("[meeting.title]", prompt)
        self.assertIn("[meeting.host]", prompt)


# ---------------------------------------------------------------------------
# calculate_score
# ---------------------------------------------------------------------------

class TestCalculateScore(unittest.TestCase):
    def test_all_consistent(self) -> None:
        verdicts = [
            {"verdict": "consistent"},
            {"verdict": "consistent"},
        ]
        self.assertAlmostEqual(calculate_score(verdicts), 100.0)

    def test_all_hallucination(self) -> None:
        verdicts = [
            {"verdict": "hallucination"},
            {"verdict": "hallucination"},
        ]
        self.assertAlmostEqual(calculate_score(verdicts), 0.0)

    def test_mixed_verdicts(self) -> None:
        verdicts = [
            {"verdict": "consistent"},
            {"verdict": "consistent"},
            {"verdict": "distorted"},
            {"verdict": "hallucination"},
            {"verdict": "consistent"},
        ]
        # 3/5 = 60.0
        self.assertAlmostEqual(calculate_score(verdicts), 60.0)

    def test_empty_verdicts(self) -> None:
        self.assertAlmostEqual(calculate_score([]), 100.0)

    def test_error_not_counted_as_consistent(self) -> None:
        verdicts = [
            {"verdict": "consistent"},
            {"verdict": "error"},
        ]
        self.assertAlmostEqual(calculate_score(verdicts), 50.0)

    def test_invalid_verdict_value_ignored(self) -> None:
        verdicts = [
            {"verdict": "consistent"},
            {"verdict": "correct"},
        ]
        self.assertAlmostEqual(calculate_score(verdicts), 50.0)


# ---------------------------------------------------------------------------
# _compile_result
# ---------------------------------------------------------------------------

class TestCompileResult(unittest.TestCase):
    def setUp(self) -> None:
        self.claims = [
            {"claim_id": "meeting.title", "description": "标题是\"Q2\""},
            {"claim_id": "meeting.host", "description": "主持人张伟"},
        ]

    def test_all_matched(self) -> None:
        verdicts = [
            {"claim_id": "meeting.title", "verdict": "consistent", "reasoning": "OK"},
            {"claim_id": "meeting.host", "verdict": "consistent", "reasoning": "OK"},
        ]
        result = _compile_result(self.claims, verdicts)
        self.assertEqual(result["total_claims"], 2)
        self.assertEqual(result["consistent"], 2)
        self.assertAlmostEqual(result["factuality_score"], 100.0)

    def test_missing_verdict_marked_error(self) -> None:
        verdicts = [
            {"claim_id": "meeting.title", "verdict": "consistent", "reasoning": "OK"},
        ]
        result = _compile_result(self.claims, verdicts)
        self.assertEqual(result["error"], 1)
        self.assertEqual(result["verdicts"][1]["verdict"], "error")

    def test_extra_verdict_ignored(self) -> None:
        verdicts = [
            {"claim_id": "meeting.title", "verdict": "consistent", "reasoning": "OK"},
            {"claim_id": "meeting.host", "verdict": "consistent", "reasoning": "OK"},
            {"claim_id": "extra.field", "verdict": "hallucination", "reasoning": "X"},
        ]
        result = _compile_result(self.claims, verdicts)
        self.assertEqual(result["total_claims"], 2)

    def test_passed_when_score_above_threshold(self) -> None:
        verdicts = [
            {"claim_id": "meeting.title", "verdict": "consistent", "reasoning": "OK"},
            {"claim_id": "meeting.host", "verdict": "consistent", "reasoning": "OK"},
        ]
        result = _compile_result(self.claims, verdicts)
        self.assertTrue(result["passed"])

    def test_passed_false_when_below_threshold(self) -> None:
        verdicts = [
            {"claim_id": "meeting.title", "verdict": "hallucination", "reasoning": "X"},
            {"claim_id": "meeting.host", "verdict": "hallucination", "reasoning": "X"},
        ]
        result = _compile_result(self.claims, verdicts)
        self.assertFalse(result["passed"])

    def test_positional_fallback_when_ids_dont_match(self) -> None:
        """When model returns sequential IDs (1,2,3), fall back to position."""
        verdicts = [
            {"claim_id": "1", "verdict": "consistent", "reasoning": "OK"},
            {"claim_id": "2", "verdict": "hallucination", "reasoning": "X"},
        ]
        result = _compile_result(self.claims, verdicts)
        # Positional fallback should work: first claim consistent, second hallucination
        self.assertEqual(result["consistent"], 1)
        self.assertEqual(result["hallucination"], 1)
        self.assertEqual(result["verdicts"][0]["verdict"], "consistent")
        self.assertEqual(result["verdicts"][1]["verdict"], "hallucination")
        # The original claim_ids should be preserved in output
        self.assertEqual(result["verdicts"][0]["claim_id"], "meeting.title")

    def test_positional_fallback_with_missing_verdict(self) -> None:
        """Positional fallback: fewer verdicts than claims → error for extras."""
        verdicts = [
            {"claim_id": "1", "verdict": "consistent", "reasoning": "OK"},
        ]
        result = _compile_result(self.claims, verdicts)
        self.assertEqual(result["verdicts"][0]["verdict"], "consistent")
        self.assertEqual(result["verdicts"][1]["verdict"], "error")

    def test_invalid_verdict_value_treated_as_error(self) -> None:
        verdicts = [
            {"claim_id": "meeting.title", "verdict": "correct", "reasoning": "OK"},
            {"claim_id": "meeting.host", "verdict": "consistent", "reasoning": "OK"},
        ]
        result = _compile_result(self.claims, verdicts)
        self.assertEqual(result["error"], 1)
        self.assertAlmostEqual(result["factuality_score"], 50.0)


# ---------------------------------------------------------------------------
# _empty_eval_result
# ---------------------------------------------------------------------------

class TestEmptyEvalResult(unittest.TestCase):
    def test_empty_result_structure(self) -> None:
        r = _empty_eval_result()
        self.assertEqual(r["total_claims"], 0)
        self.assertAlmostEqual(r["factuality_score"], 100.0)
        self.assertTrue(r["passed"])
        self.assertEqual(r["verdicts"], [])


# ---------------------------------------------------------------------------
# run_evaluation (integration with mocks)
# ---------------------------------------------------------------------------

class TestRunEvaluation(unittest.TestCase):
    def setUp(self) -> None:
        raw = (FIXTURES / "valid_summary.json").read_text(encoding="utf-8")
        self.summary = json.loads(raw)
        self.transcript = "会议记录：张伟主持，讨论Q2计划。"
        self.eval_resp = _load_fixture("eval_response_valid.json")

    def test_success_flow(self) -> None:
        with patch(
            "evaluator.call_ollama", return_value=self.eval_resp
        ) as mock_call:
            result = run_evaluation(
                CONFIG, EVAL_PROMPT, self.transcript, self.summary
            )
        mock_call.assert_called_once()
        self.assertIn("factuality_score", result)
        self.assertIn("verdicts", result)
        self.assertGreater(result["total_claims"], 0)

    def test_no_claims_short_circuit(self) -> None:
        with patch("evaluator.call_ollama") as mock_call:
            result = run_evaluation(CONFIG, EVAL_PROMPT, self.transcript, {})
        mock_call.assert_not_called()
        self.assertEqual(result["total_claims"], 0)
        self.assertAlmostEqual(result["factuality_score"], 100.0)

    def test_eval_parse_failure(self) -> None:
        malformed = _load_fixture("eval_response_malformed.txt")
        with patch("evaluator.call_ollama", return_value=malformed):
            from json_parser import JSONExtractionError
            with self.assertRaises(JSONExtractionError):
                run_evaluation(CONFIG, EVAL_PROMPT, self.transcript, self.summary)

    def test_eval_score_uses_valid_verdict(self) -> None:
        with patch(
            "evaluator.call_ollama", return_value=self.eval_resp
        ):
            result = run_evaluation(
                CONFIG, EVAL_PROMPT, self.transcript, self.summary
            )
        # valid_summary has 13 claims; eval fixture has 7 verdicts (6 match by
        # claim_id → match rate 6/13 < 50%, so positional fallback activates.
        # First 7 claims get zipped with the 7 verdicts; remaining 6 are error.
        # Verdicts by position: 0=distorted, 1=consistent, 2=consistent,
        # 3=consistent, 4=consistent, 5=distorted, 6=hallucination
        # → 4 consistent, 2 distorted, 1 hallucination, 6 error = 4/13 ≈ 30.8%
        self.assertAlmostEqual(result["factuality_score"], 30.8, delta=1.0)
        self.assertEqual(result["consistent"], 4)
        self.assertEqual(result["distorted"], 2)
        self.assertEqual(result["hallucination"], 1)
        self.assertEqual(result["error"], 6)


if __name__ == "__main__":
    unittest.main()
