"""Unit tests for markdown_generator.py."""

import json
import unittest
from pathlib import Path

from markdown_generator import generate_markdown

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestGenerateMarkdown(unittest.TestCase):
    """Tests for generate_markdown() with the original schema."""

    def setUp(self) -> None:
        raw = (FIXTURES / "valid_summary.json").read_text(encoding="utf-8")
        self.full_data = json.loads(raw)

    def test_full_data_contains_sections(self) -> None:
        md = generate_markdown(self.full_data)
        self.assertIn("# Q2 Planning Sync", md)
        self.assertIn("## 会议信息", md)
        self.assertIn("## 讨论要点", md)
        self.assertIn("## 决策事项", md)
        self.assertIn("## 行动项", md)
        self.assertIn("## 问题与风险", md)
        self.assertIn("## 下次会议", md)

    def test_full_data_contains_chinese_labels(self) -> None:
        md = generate_markdown(self.full_data)
        self.assertIn("日期", md)
        self.assertIn("参会人员", md)
        self.assertIn("发言人", md)
        self.assertIn("负责人", md)

    def test_minimal_data_does_not_crash(self) -> None:
        minimal = {
            "meeting": {"title": "Minimal"},
            "discussion_points": [],
            "decisions": [],
            "action_items": [],
            "issues_risks": [],
            "next_meeting": {},
        }
        md = generate_markdown(minimal)
        self.assertIn("# Minimal", md)
        self.assertIn("N/A", md)
        self.assertIn("无", md)
        self.assertIn("TBD", md)

    def test_missing_sections_fallback(self) -> None:
        bare = {"meeting": {"title": "Bare"}}
        md = generate_markdown(bare)
        self.assertIn("# Bare", md)
        self.assertIn("无", md)

    def test_chinese_rendering(self) -> None:
        data = {
            "meeting": {
                "title": "产品评审会",
                "date": "2026-05-11",
                "time": "10:00",
                "location": "线上",
                "host": "张伟",
                "attendees": ["李娜", "王强"],
                "agenda": ["功能演示", "Q&A"],
            },
            "discussion_points": [
                {"topic": "功能演示", "summary": "演示了新功能", "speaker": "李娜"}
            ],
            "decisions": [
                {"description": "通过评审", "responsible": "张伟", "deadline": "N/A"}
            ],
            "action_items": [
                {"task": "修复bug", "assignee": "王强", "due": "2026-05-15"}
            ],
            "issues_risks": ["性能问题"],
            "next_meeting": {
                "date": "TBD", "time": "TBD",
                "location": "TBD", "tentative_agenda": "TBD",
            },
        }
        md = generate_markdown(data)
        self.assertIn("产品评审会", md)
        self.assertIn("张伟", md)
        self.assertIn("李娜", md)
        self.assertIn("功能演示", md)
        self.assertIn("修复bug", md)
        self.assertIn("性能问题", md)


class TestNewSchemaFields(unittest.TestCase):
    """Tests for P2 enhanced schema fields."""

    def setUp(self) -> None:
        raw = (FIXTURES / "summary_with_new_schema.json").read_text(encoding="utf-8")
        self.data = json.loads(raw)

    # ---- TL;DR ----

    def test_tldr_rendered(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("> **TL;DR**：", md)
        self.assertIn("Q2规划同步会", md)

    def test_tldr_omitted_when_empty(self) -> None:
        data = {"meeting": {"title": "Test"}}
        md = generate_markdown(data)
        self.assertNotIn("TL;DR", md)

    # ---- meeting_type ----

    def test_meeting_type_rendered(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("**会议类型**：规划会", md)

    def test_meeting_type_omitted_when_empty(self) -> None:
        data = {"meeting": {"title": "Test"}}
        md = generate_markdown(data)
        self.assertNotIn("会议类型", md)

    # ---- key_conclusion ----

    def test_key_conclusion_rendered(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("- **结论**:", md)
        self.assertIn("Sprint目标", md)

    def test_key_conclusion_omitted_when_empty(self) -> None:
        data = {
            "meeting": {"title": "T"},
            "discussion_points": [
                {"topic": "X", "summary": "Y", "speaker": "Z"}
            ],
        }
        md = generate_markdown(data)
        self.assertNotIn("结论", md)

    # ---- rationale ----

    def test_rationale_rendered(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("决策依据", md)
        self.assertIn("测试资源集中", md)

    def test_rationale_omitted_when_empty(self) -> None:
        data = {
            "meeting": {"title": "T"},
            "decisions": [
                {"description": "D", "responsible": "R", "deadline": "N/A"}
            ],
        }
        md = generate_markdown(data)
        self.assertNotIn("决策依据", md)

    # ---- priority labels ----

    def test_priority_labels_rendered(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("P0", md)
        self.assertIn("P1", md)
        self.assertIn("P2", md)

    def test_priority_omitted_when_empty(self) -> None:
        data = {
            "meeting": {"title": "T"},
            "action_items": [
                {"task": "X", "assignee": "Y", "due": "N/A"}
            ],
        }
        md = generate_markdown(data)
        # No priority label emoji should appear
        self.assertNotIn("P0", md)
        self.assertNotIn("P1", md)

    # ---- issues_risks dict format ----

    def test_issues_risks_dict_format(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("严重程度", md)
        self.assertIn("缓解措施", md)
        self.assertIn("测试资源紧张", md)

    def test_issues_risks_backward_compat_strings(self) -> None:
        data = {
            "meeting": {"title": "T"},
            "issues_risks": ["风险一", "风险二"],
        }
        md = generate_markdown(data)
        self.assertIn("风险一", md)
        self.assertIn("风险二", md)
        self.assertNotIn("严重程度", md)

    # ---- source_quote ----

    def test_source_quote_in_discussion_point(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("原文引用", md)

    def test_source_quote_omitted_when_null(self) -> None:
        data = {
            "meeting": {"title": "T"},
            "discussion_points": [
                {"topic": "X", "summary": "Y", "speaker": "Z", "source_quote": None}
            ],
        }
        md = generate_markdown(data)
        self.assertNotIn("原文引用", md)

    def test_source_quote_omitted_when_empty_string(self) -> None:
        data = {
            "meeting": {"title": "T"},
            "discussion_points": [
                {"topic": "X", "summary": "Y", "speaker": "Z", "source_quote": "  "}
            ],
        }
        md = generate_markdown(data)
        self.assertNotIn("原文引用", md)

    def test_source_quote_in_decision(self) -> None:
        md = generate_markdown(self.data)
        self.assertIn("原文引用", md)

    def test_source_quote_in_action_item(self) -> None:
        md = generate_markdown(self.data)
        quotes = [line for line in md.split("\n") if "原文引用" in line]
        self.assertGreater(len(quotes), 0)

    def test_source_quote_in_issue_risk(self) -> None:
        data = {
            "meeting": {"title": "T"},
            "issues_risks": [
                {
                    "description": "测试风险",
                    "severity": "高",
                    "mitigation": "暂无",
                    "source_quote": "测试设备不足影响回归进度。",
                }
            ],
        }
        md = generate_markdown(data)
        self.assertIn("原文引用", md)
        self.assertIn("测试设备不足影响回归进度", md)


class TestCompletenessFooter(unittest.TestCase):
    """Tests for completeness footer rendering."""

    def test_completeness_footer_rendered(self) -> None:
        data = {"meeting": {"title": "Test"}}
        completeness = {
            "coverage_score": 85.5,
            "threshold": 80.0,
            "passed": True,
            "agenda_check": {"total": 3, "covered": 3, "missing": []},
            "speaker_check": {"total": 5, "covered": 4, "missing": ["赵敏"]},
            "section_check": {
                "sections_present": ["tldr", "meeting"],
                "sections_empty": [],
                "sections_missing": [],
            },
            "overall_assessment": "覆盖率良好，议程和章节完整。",
        }
        md = generate_markdown(data, completeness_data=completeness)
        self.assertIn("## 完整性检查", md)
        self.assertIn("85.5%", md)
        self.assertIn("通过", md)
        self.assertIn("赵敏", md)
        self.assertIn("覆盖率良好", md)

    def test_completeness_failed_threshold(self) -> None:
        data = {"meeting": {"title": "Test"}}
        completeness = {
            "coverage_score": 65.0,
            "threshold": 80.0,
            "passed": False,
            "agenda_check": {"total": 4, "covered": 2, "missing": ["议题A", "议题B"]},
            "speaker_check": {"total": 5, "covered": 5, "missing": []},
            "section_check": {
                "sections_present": [],
                "sections_empty": ["action_items"],
                "sections_missing": ["issues_risks"],
            },
            "overall_assessment": "议程覆盖不足，缺少问题与风险章节。",
        }
        md = generate_markdown(data, completeness_data=completeness)
        self.assertIn("未通过", md)
        self.assertIn("2/4", md)
        self.assertIn("议题A, 议题B", md)
        self.assertIn("action_items", md)
        self.assertIn("issues_risks", md)

    def test_no_completeness_footer_when_none(self) -> None:
        data = {"meeting": {"title": "Test"}}
        md = generate_markdown(data)
        self.assertNotIn("完整性检查", md)

    def test_completeness_minimal_data(self) -> None:
        data = {"meeting": {"title": "Test"}}
        completeness: dict = {}
        md = generate_markdown(data, completeness_data=completeness)
        self.assertIn("## 完整性检查", md)
        self.assertIn("0.0%", md)


if __name__ == "__main__":
    unittest.main()
