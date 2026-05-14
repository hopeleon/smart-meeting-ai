"""Unit tests for json_parser.py."""

import unittest
from pathlib import Path

from json_parser import JSONExtractionError, extract_json, validate_schema

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestExtractJson(unittest.TestCase):
    """Tests for extract_json()."""

    def test_direct_valid_json(self) -> None:
        raw = '{"meeting": {"title": "Test"}}'
        result = extract_json(raw)
        self.assertEqual(result["meeting"]["title"], "Test")

    def test_json_with_markdown_fences(self) -> None:
        raw = (FIXTURES / "json_with_fences.txt").read_text(encoding="utf-8")
        result = extract_json(raw)
        self.assertEqual(result["meeting"]["title"], "Q2 Planning")

    def test_json_with_prefix_text(self) -> None:
        raw = (FIXTURES / "json_with_comment.txt").read_text(encoding="utf-8")
        result = extract_json(raw)
        self.assertEqual(result["meeting"]["title"], "Weekly Standup")

    def test_nested_braces(self) -> None:
        raw = '{"meeting": {"title": "Test", "attendees": ["A", "B"]}}'
        result = extract_json(raw)
        self.assertEqual(result["meeting"]["attendees"], ["A", "B"])

    def test_invalid_json_raises(self) -> None:
        raw = (FIXTURES / "invalid_response.txt").read_text(encoding="utf-8")
        with self.assertRaises(JSONExtractionError) as ctx:
            extract_json(raw)
        self.assertIn("Cannot extract valid JSON", str(ctx.exception))

    def test_empty_response_raises(self) -> None:
        with self.assertRaises(JSONExtractionError):
            extract_json("")

    def test_raw_response_preserved_in_exception(self) -> None:
        raw = "not json at all"
        with self.assertRaises(JSONExtractionError) as ctx:
            extract_json(raw)
        self.assertEqual(ctx.exception.raw_response, raw)

    def test_load_full_fixture(self) -> None:
        raw = (FIXTURES / "valid_summary.json").read_text(encoding="utf-8")
        result = extract_json(raw)
        self.assertIn("meeting", result)
        self.assertIn("discussion_points", result)
        self.assertIn("decisions", result)
        self.assertIn("action_items", result)
        self.assertIn("issues_risks", result)
        self.assertIn("next_meeting", result)


class TestValidateSchema(unittest.TestCase):
    """Tests for validate_schema()."""

    def test_all_keys_present(self) -> None:
        data = {
            "tldr": "test",
            "meeting": {},
            "discussion_points": [],
            "decisions": [],
            "action_items": [],
            "issues_risks": [],
            "next_meeting": {},
        }
        self.assertEqual(validate_schema(data), [])

    def test_missing_keys_reported(self) -> None:
        data = {"meeting": {}}
        missing = validate_schema(data)
        self.assertIn("action_items", missing)
        self.assertIn("discussion_points", missing)

    def test_extra_keys_ignored(self) -> None:
        data = {
            "tldr": "test",
            "meeting": {},
            "discussion_points": [],
            "decisions": [],
            "action_items": [],
            "issues_risks": [],
            "next_meeting": {},
            "extra_field": "should be ignored",
        }
        self.assertEqual(validate_schema(data), [])


if __name__ == "__main__":
    unittest.main()
