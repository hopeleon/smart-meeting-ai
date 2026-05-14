"""Unit tests for action_items.py."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from action_items import (
    _normalize_category,
    _normalize_items,
    _normalize_priority,
    extract_action_items,
    write_actions_csv,
    write_actions_json,
)
from config import OllamaConfig

CONFIG = OllamaConfig(base_url="http://localhost:11434", model="qwen3:8b")
PROMPT = "You are an action-item extractor."
TRANSCRIPT = "会议记录：王强负责权限系统5月25日提测。"


# ---------------------------------------------------------------------------
# _normalize_category
# ---------------------------------------------------------------------------

class TestNormalizeCategory(unittest.TestCase):
    def test_exact_match(self) -> None:
        for cat in ["研发", "产品", "设计", "测试", "运营", "行政", "其他"]:
            with self.subTest(cat=cat):
                self.assertEqual(_normalize_category(cat), cat)

    def test_alias_tech_to_rd(self) -> None:
        self.assertEqual(_normalize_category("技术"), "研发")
        self.assertEqual(_normalize_category("开发"), "研发")

    def test_alias_qa_to_test(self) -> None:
        self.assertEqual(_normalize_category("qa"), "测试")

    def test_alias_ui_to_design(self) -> None:
        self.assertEqual(_normalize_category("UI"), "设计")

    def test_unknown_fallback(self) -> None:
        self.assertEqual(_normalize_category("外星人部门"), "其他")

    def test_whitespace_stripped(self) -> None:
        self.assertEqual(_normalize_category("  研发  "), "研发")


# ---------------------------------------------------------------------------
# _normalize_priority
# ---------------------------------------------------------------------------

class TestNormalizePriority(unittest.TestCase):
    def test_valid_values(self) -> None:
        for p in ["P0", "P1", "P2", "P3"]:
            with self.subTest(p=p):
                self.assertEqual(_normalize_priority(p), p)

    def test_lowercase_valid(self) -> None:
        self.assertEqual(_normalize_priority("p0"), "P0")

    def test_alias_urgent_to_p0(self) -> None:
        self.assertEqual(_normalize_priority("紧急"), "P0")

    def test_alias_high_to_p1(self) -> None:
        self.assertEqual(_normalize_priority("重要"), "P1")

    def test_alias_medium_to_p2(self) -> None:
        self.assertEqual(_normalize_priority("一般"), "P2")

    def test_alias_low_to_p3(self) -> None:
        self.assertEqual(_normalize_priority("low"), "P3")

    def test_invalid_fallback(self) -> None:
        self.assertEqual(_normalize_priority("xyz123"), "P2")


# ---------------------------------------------------------------------------
# _normalize_items
# ---------------------------------------------------------------------------

class TestNormalizeItems(unittest.TestCase):
    def test_batch_normalization(self) -> None:
        items = [
            {"category": "技术", "priority": "紧急", "task": "A"},
            {"category": " 研发 ", "priority": "P1", "task": "B"},
            {"category": "外星", "priority": "super", "task": "C"},
        ]
        result = _normalize_items(items)
        self.assertEqual(result[0]["category"], "研发")
        self.assertEqual(result[0]["priority"], "P0")
        self.assertEqual(result[1]["category"], "研发")
        self.assertEqual(result[1]["priority"], "P1")
        self.assertEqual(result[2]["category"], "其他")
        self.assertEqual(result[2]["priority"], "P2")

    def test_missing_fields_defaulted(self) -> None:
        items: list[dict] = [{"task": "X"}]
        result = _normalize_items(items)
        self.assertEqual(result[0]["category"], "其他")
        self.assertEqual(result[0]["priority"], "P2")


# ---------------------------------------------------------------------------
# write_actions_csv
# ---------------------------------------------------------------------------

class TestWriteActionsCSV(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.items = [{
            "id": "A001", "task": "权限系统提测", "assignee": "王强",
            "due": "2026-05-25", "category": "研发", "priority": "P0",
            "source": "预计5月25号提测", "context": "王强汇报",
        }]

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_csv_headers(self) -> None:
        path = write_actions_csv(self.tmpdir, "test", "20260511", self.items)
        content = path.read_text(encoding="utf-8-sig")
        self.assertIn("编号", content)
        self.assertIn("待办任务", content)
        self.assertIn("原文引用", content)

    def test_csv_bom(self) -> None:
        path = write_actions_csv(self.tmpdir, "test", "20260511", self.items)
        raw = path.read_bytes()
        self.assertEqual(raw[:3], b"\xef\xbb\xbf")  # UTF-8 BOM

    def test_csv_data_row(self) -> None:
        path = write_actions_csv(self.tmpdir, "test", "20260511", self.items)
        content = path.read_text(encoding="utf-8-sig")
        self.assertIn("A001", content)
        self.assertIn("权限系统提测", content)
        self.assertIn("王强汇报", content)

    def test_csv_empty_items(self) -> None:
        path = write_actions_csv(self.tmpdir, "test", "20260511", [])
        content = path.read_text(encoding="utf-8-sig")
        lines = content.strip().split("\n")
        self.assertEqual(len(lines), 1)  # header only

    def test_csv_filename(self) -> None:
        path = write_actions_csv(self.tmpdir, "weekly", "20260511", self.items)
        self.assertEqual(path.name, "weekly_20260511_actions.csv")


# ---------------------------------------------------------------------------
# write_actions_json
# ---------------------------------------------------------------------------

class TestWriteActionsJSON(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())
        self.items = [{
            "id": "A001", "task": "更新roadmap", "assignee": "李娜",
            "due": "2026-05-16", "category": "产品", "priority": "P1",
            "source": "李娜你更新一下roadmap", "context": "张伟要求",
        }]

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_json_has_metadata_and_items(self) -> None:
        path = write_actions_json(self.tmpdir, "test", "20260511", self.items)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertIn("metadata", data)
        self.assertIn("action_items", data)
        self.assertEqual(data["metadata"]["total_items"], 1)
        self.assertEqual(data["action_items"][0]["task"], "更新roadmap")

    def test_json_metadata_categories(self) -> None:
        items = [
            {"id": "A001", "category": "研发", "priority": "P1",
             "task": "A", "assignee": "X", "due": "N/A",
             "source": ".", "context": "."},
            {"id": "A002", "category": "设计", "priority": "P2",
             "task": "B", "assignee": "Y", "due": "N/A",
             "source": ".", "context": "."},
        ]
        path = write_actions_json(self.tmpdir, "test", "20260511", items)
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(set(data["metadata"]["categories"]), {"设计", "研发"})

    def test_json_empty_items(self) -> None:
        path = write_actions_json(self.tmpdir, "test", "20260511", [])
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["metadata"]["total_items"], 0)
        self.assertEqual(data["action_items"], [])

    def test_json_filename(self) -> None:
        path = write_actions_json(self.tmpdir, "weekly", "20260511", self.items)
        self.assertEqual(path.name, "weekly_20260511_actions.json")


# ---------------------------------------------------------------------------
# extract_action_items
# ---------------------------------------------------------------------------

class TestExtractActionItems(unittest.TestCase):
    def test_successful_extraction(self) -> None:
        mock_resp = json.dumps({
            "action_items": [
                {
                    "id": "A001", "task": "提测", "assignee": "王强",
                    "due": "2026-05-25", "category": "研发", "priority": "P0",
                    "source": "原文引用", "context": "背景",
                }
            ]
        }, ensure_ascii=False)
        with patch("action_items.call_ollama", return_value=mock_resp):
            result = extract_action_items(CONFIG, PROMPT, TRANSCRIPT)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["task"], "提测")

    def test_normalization_applied(self) -> None:
        mock_resp = json.dumps({
            "action_items": [
                {
                    "id": "A001", "task": "X", "assignee": "Y",
                    "due": "N/A", "category": "技术", "priority": "紧急",
                    "source": ".", "context": ".",
                }
            ]
        }, ensure_ascii=False)
        with patch("action_items.call_ollama", return_value=mock_resp):
            result = extract_action_items(CONFIG, PROMPT, TRANSCRIPT)
        self.assertEqual(result[0]["category"], "研发")
        self.assertEqual(result[0]["priority"], "P0")

    def test_parse_failure_raises(self) -> None:
        with patch("action_items.call_ollama", return_value="not json"):
            from json_parser import JSONExtractionError
            with self.assertRaises(JSONExtractionError):
                extract_action_items(CONFIG, PROMPT, TRANSCRIPT)

    def test_empty_action_items_in_response(self) -> None:
        mock_resp = json.dumps({"action_items": []})
        with patch("action_items.call_ollama", return_value=mock_resp):
            result = extract_action_items(CONFIG, PROMPT, TRANSCRIPT)
        self.assertEqual(result, [])

    def test_missing_action_items_key(self) -> None:
        mock_resp = json.dumps({"other_key": "value"})
        with patch("action_items.call_ollama", return_value=mock_resp):
            result = extract_action_items(CONFIG, PROMPT, TRANSCRIPT)
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
