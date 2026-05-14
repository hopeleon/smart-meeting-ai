"""Tests for semantic_splitter.py — LLM-based topic-boundary splitting.

Uses sentence-level numbering instead of marker matching.
"""

import unittest
from unittest.mock import MagicMock, patch

from semantic_splitter import (
    _build_numbered_text,
    _sentence_breaks,
    locate_segments,
    semantic_split,
    validate_segment_coverage,
)


class TestSentenceBreaks(unittest.TestCase):
    """_sentence_breaks() tests."""

    def test_breaks_on_period(self) -> None:
        breaks = _sentence_breaks("第一句。第二句。")
        # After first 。at index 3 → position 4
        self.assertIn(4, breaks)

    def test_breaks_on_exclamation(self) -> None:
        breaks = _sentence_breaks("注意！这是重点。")
        self.assertIn(3, breaks)

    def test_breaks_on_question(self) -> None:
        breaks = _sentence_breaks("对吗？是的。")
        self.assertIn(3, breaks)

    def test_breaks_on_newline_before_content(self) -> None:
        breaks = _sentence_breaks("A。\nB。")
        # newline at index 2, followed by 'B' (non-whitespace) → position 3
        self.assertIn(3, breaks)

    def test_no_break_on_consecutive_newlines(self) -> None:
        """Only the last \\n in a \\n\\n sequence creates a break."""
        # "A。\\n\\nB。" → indices: A=0 。=1 \\n=2 \\n=3 B=4 。=5
        breaks = _sentence_breaks("A。\n\nB。")
        # First  \\n at idx 2 → text[3]='\\n' (whitespace) → no break at 3
        self.assertNotIn(3, breaks)
        # Second \\n at idx 3 → text[4]='B' (content)  → break at 4
        self.assertIn(4, breaks)

    def test_empty_text(self) -> None:
        breaks = _sentence_breaks("")
        self.assertEqual(breaks, [])


class TestBuildNumberedText(unittest.TestCase):
    """_build_numbered_text() tests."""

    def test_simple_sentences(self) -> None:
        text = "第一句。第二句。"
        numbered, spans = _build_numbered_text(text)
        self.assertIn("【1】", numbered)
        self.assertIn("【2】", numbered)
        self.assertEqual(len(spans), 2)

    def test_spans_preserve_original_positions(self) -> None:
        text = "AAA。BBB。"
        numbered, spans = _build_numbered_text(text)
        # First span: "AAA。" from 0 to 4
        self.assertEqual(text[spans[0][0]:spans[0][1]].strip(), "AAA。")
        # Second span: "BBB。" from 4 to 8
        self.assertEqual(text[spans[1][0]:spans[1][1]].strip(), "BBB。")

    def test_whitespace_only_spans_skipped(self) -> None:
        text = "A。\n   \nB。"
        numbered, spans = _build_numbered_text(text)
        self.assertEqual(len(spans), 2)

    def test_single_sentence(self) -> None:
        text = "只有一句没有标点"
        numbered, spans = _build_numbered_text(text)
        self.assertEqual(len(spans), 1)
        self.assertIn("【1】", numbered)


class TestValidateSegmentCoverage(unittest.TestCase):
    """validate_segment_coverage() tests."""

    def test_valid_coverage(self) -> None:
        segments = [
            {"start_sentence": 1, "end_sentence": 3},
            {"start_sentence": 4, "end_sentence": 7},
            {"start_sentence": 8, "end_sentence": 10},
        ]
        validate_segment_coverage(segments, 10)  # Should not raise

    def test_start_greater_than_end_raises(self) -> None:
        segments = [{"start_sentence": 5, "end_sentence": 3}]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 10)

    def test_gap_between_segments_raises(self) -> None:
        segments = [
            {"start_sentence": 1, "end_sentence": 3},
            {"start_sentence": 5, "end_sentence": 7},  # gap: missing 4
        ]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 7)

    def test_overlap_between_segments_raises(self) -> None:
        segments = [
            {"start_sentence": 1, "end_sentence": 4},
            {"start_sentence": 4, "end_sentence": 7},  # overlap at 4
        ]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 7)

    def test_first_segment_not_1_raises(self) -> None:
        segments = [{"start_sentence": 2, "end_sentence": 5}]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 5)

    def test_last_segment_not_ending_at_total_raises(self) -> None:
        segments = [
            {"start_sentence": 1, "end_sentence": 3},
            {"start_sentence": 4, "end_sentence": 7},
        ]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 10)  # total is 10, last ends at 7

    def test_non_integer_sentence_numbers_raises(self) -> None:
        segments = [{"start_sentence": "1", "end_sentence": 5}]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 5)

    def test_non_positive_sentence_numbers_raises(self) -> None:
        segments = [{"start_sentence": 0, "end_sentence": 5}]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 5)

    def test_empty_segments_raises(self) -> None:
        with self.assertRaises(ValueError):
            validate_segment_coverage([], 10)

    def test_exceeds_total_raises(self) -> None:
        segments = [{"start_sentence": 1, "end_sentence": 15}]
        with self.assertRaises(ValueError):
            validate_segment_coverage(segments, 10)


class TestLocateSegments(unittest.TestCase):
    """locate_segments() tests with sentence numbering."""

    def setUp(self) -> None:
        self.transcript = (
            "开场：今天讨论Q2规划。\n"
            "第一部分：折叠屏适配。李娜汇报了前端进展。\n"
            "第二部分：推荐系统重构。王强汇报了延迟问题。\n"
            "结束：下次会议定在下周五。"
        )

    def test_two_segments(self) -> None:
        # Transcript has 6 sentences (newline-only spans filtered).
        # First 3 → segment 1, last 3 → segment 2.
        boundaries = [
            {
                "title": "折叠屏适配",
                "start_sentence": 1,
                "end_sentence": 3,
            },
            {
                "title": "推荐系统重构",
                "start_sentence": 4,
                "end_sentence": 6,
            },
        ]
        segments = locate_segments(self.transcript, boundaries)
        self.assertEqual(len(segments), 2)
        self.assertIn("折叠屏适配", segments[0])
        self.assertIn("推荐系统重构", segments[1])

    def test_single_segment(self) -> None:
        boundaries = [
            {
                "title": "全文",
                "start_sentence": 1,
                "end_sentence": 6,
            }
        ]
        segments = locate_segments(self.transcript, boundaries)
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0], self.transcript.strip())

    def test_empty_boundaries_returns_full(self) -> None:
        segments = locate_segments(self.transcript, [])
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0], self.transcript)


class TestSemanticSplit(unittest.TestCase):
    """semantic_split() tests (mock LLM)."""

    def _make_config(self) -> MagicMock:
        return MagicMock()

    def _make_prompt(self) -> str:
        return "You are a semantic splitter."

    def _make_transcript(self) -> str:
        return "话题一。话题二。话题三。话题四。话题五。话题六。"

    @patch("semantic_splitter.call_ollama")
    def test_successful_split(self, mock_call: MagicMock) -> None:
        mock_call.return_value = (
            '[{"title":"话题一","start_sentence":1,"end_sentence":2},'
            '{"title":"话题二","start_sentence":3,"end_sentence":4},'
            '{"title":"话题三","start_sentence":5,"end_sentence":6}]'
        )
        config = self._make_config()
        result = semantic_split(config, self._make_prompt(), self._make_transcript())
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["title"], "话题一")
        self.assertEqual(result[0]["start_sentence"], 1)

    @patch("semantic_splitter.call_ollama")
    def test_too_few_segments_raises(self, mock_call: MagicMock) -> None:
        mock_call.return_value = (
            '[{"title":"全文","start_sentence":1,"end_sentence":6}]'
        )
        config = self._make_config()
        with self.assertRaises(ValueError):
            semantic_split(config, self._make_prompt(), self._make_transcript())

    @patch("semantic_splitter.call_ollama")
    def test_too_many_segments_raises(self, mock_call: MagicMock) -> None:
        segments = []
        for i in range(15):
            segments.append(
                f'{{"title":"段{i}","start_sentence":{i+1},"end_sentence":{i+1}}}'
            )
        mock_call.return_value = "[" + ",".join(segments) + "]"
        config = self._make_config()
        with self.assertRaises(ValueError):
            semantic_split(config, self._make_prompt(), self._make_transcript())

    @patch("semantic_splitter.call_ollama")
    def test_non_list_response_raises(self, mock_call: MagicMock) -> None:
        mock_call.return_value = '{"not": "a list"}'
        config = self._make_config()
        with self.assertRaises(Exception):
            semantic_split(config, self._make_prompt(), self._make_transcript())

    @patch("semantic_splitter.call_ollama")
    def test_missing_sentence_number_raises(self, mock_call: MagicMock) -> None:
        mock_call.return_value = (
            '[{"title":"段1","start_sentence":"","end_sentence":""},'
            '{"title":"段2","start_sentence":5,"end_sentence":6}]'
        )
        config = self._make_config()
        with self.assertRaises(Exception):
            semantic_split(config, self._make_prompt(), self._make_transcript())

    @patch("semantic_splitter.call_ollama")
    def test_gap_in_coverage_raises(self, mock_call: MagicMock) -> None:
        """Coverage validation should catch gaps between segments."""
        mock_call.return_value = (
            '[{"title":"段1","start_sentence":1,"end_sentence":2},'
            '{"title":"段2","start_sentence":5,"end_sentence":6}]'
        )
        config = self._make_config()
        with self.assertRaises(ValueError):
            semantic_split(config, self._make_prompt(), self._make_transcript())

    @patch("semantic_splitter.call_ollama")
    def test_start_not_one_raises(self, mock_call: MagicMock) -> None:
        mock_call.return_value = (
            '[{"title":"段1","start_sentence":2,"end_sentence":3},'
            '{"title":"段2","start_sentence":4,"end_sentence":6}]'
        )
        config = self._make_config()
        with self.assertRaises(ValueError):
            semantic_split(config, self._make_prompt(), self._make_transcript())

    @patch("semantic_splitter.call_ollama")
    def test_validation_passes_transcript_to_numbered_text(self, mock_call: MagicMock) -> None:
        """Verify the LLM receives numbered text in the user message."""
        mock_call.return_value = (
            '[{"title":"段1","start_sentence":1,"end_sentence":2},'
            '{"title":"段2","start_sentence":3,"end_sentence":4},'
            '{"title":"段3","start_sentence":5,"end_sentence":6}]'
        )
        config = self._make_config()
        semantic_split(config, self._make_prompt(), "AAA。BBB。CCC。DDD。EEE。FFF。")
        user_message = mock_call.call_args[0][2]  # third positional arg
        self.assertIn("【1】", user_message)
        self.assertIn("【6】", user_message)
