"""Tests for verify.py — post-hoc verification and auto-correction."""

import unittest

from verify import _apply_corrections, _build_verify_prompt


class TestBuildVerifyPrompt(unittest.TestCase):
    """_build_verify_prompt() tests."""

    def test_includes_transcript(self) -> None:
        summary = {"tldr": "test", "meeting": {"title": "Test"}}
        prompt = _build_verify_prompt(summary, "原始会议记录内容")
        self.assertIn("原始会议记录内容", prompt)

    def test_includes_summary_json(self) -> None:
        summary = {"tldr": "测试摘要", "meeting": {"title": "测试会议"}}
        prompt = _build_verify_prompt(summary, "原文")
        self.assertIn("测试摘要", prompt)
        self.assertIn("测试会议", prompt)
        self.assertIn("```json", prompt)


class TestApplyCorrections(unittest.TestCase):
    """_apply_corrections() tests."""

    def test_unchanged_when_corrected_empty(self) -> None:
        original = {
            "tldr": "原始TLDR",
            "meeting": {"title": "原始标题"},
            "discussion_points": [{"topic": "话题1"}],
        }
        result = _apply_corrections(original, {})
        self.assertEqual(result["tldr"], "原始TLDR")
        self.assertEqual(result["meeting"]["title"], "原始标题")

    def test_updates_tldr(self) -> None:
        original = {"tldr": "旧的摘要"}
        corrected = {"tldr": "新的摘要"}
        result = _apply_corrections(original, corrected)
        self.assertEqual(result["tldr"], "新的摘要")

    def test_updates_meeting_fields(self) -> None:
        original = {"meeting": {"title": "旧标题", "date": "2026-05-12"}}
        corrected = {"meeting": {"title": "新标题"}}
        result = _apply_corrections(original, corrected)
        self.assertEqual(result["meeting"]["title"], "新标题")
        # Unchanged field preserved
        self.assertEqual(result["meeting"]["date"], "2026-05-12")

    def test_updates_discussion_points(self) -> None:
        original = {
            "discussion_points": [
                {"topic": "话题1", "summary": "旧摘要", "speaker": "张三"}
            ]
        }
        corrected = {
            "discussion_points": [
                {"topic": "话题1", "summary": "新摘要", "speaker": "张三"}
            ]
        }
        result = _apply_corrections(original, corrected)
        self.assertEqual(result["discussion_points"][0]["summary"], "新摘要")

    def test_empty_tldr_not_overwritten(self) -> None:
        original = {"tldr": "有效摘要"}
        corrected = {"tldr": ""}
        result = _apply_corrections(original, corrected)
        # Empty tldr should not overwrite
        self.assertEqual(result["tldr"], "有效摘要")

    def test_preserves_keys_not_in_corrected(self) -> None:
        original = {
            "tldr": "test",
            "meeting": {"title": "Test"},
            "action_items": [{"task": "任务1"}],
            "next_meeting": {"date": "TBD"},
        }
        corrected = {"meeting": {"title": "Updated"}}
        result = _apply_corrections(original, corrected)
        self.assertEqual(result["action_items"], [{"task": "任务1"}])
        self.assertEqual(result["next_meeting"], {"date": "TBD"})
