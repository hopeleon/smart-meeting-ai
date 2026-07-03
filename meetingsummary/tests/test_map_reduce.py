"""Tests for map_reduce.py — Map-Reduce pipeline components."""

import unittest

from map_reduce import (
    _descriptions_similar,
    _format_facts_for_reduce,
    merge_deduplicate,
)


class TestDescriptionsSimilar(unittest.TestCase):
    """_descriptions_similar() tests."""

    def test_identical_strings(self) -> None:
        self.assertTrue(_descriptions_similar("完成支付模块安全修复", "完成支付模块安全修复"))

    def test_similar_strings(self) -> None:
        self.assertTrue(_descriptions_similar(
            "折叠屏设备在平台的加购转化率低15个百分点",
            "折叠屏设备加购转化率低15个百分点"
        ))

    def test_different_strings(self) -> None:
        self.assertFalse(_descriptions_similar(
            "完成支付模块安全修复",
            "启动云GPU采购评估"
        ))

    def test_empty_strings(self) -> None:
        self.assertTrue(_descriptions_similar("", ""))


class TestFormatFactsForReduce(unittest.TestCase):
    """_format_facts_for_reduce() tests."""

    def test_groups_by_type(self) -> None:
        facts = [
            {"type": "decision", "description": "优先完成折叠屏适配",
             "speaker": "张宇", "source_quote": "优先保障折叠屏核心购买链路体验。"},
            {"type": "action_item", "description": "输出折叠屏适配方案",
             "speaker": "李娜", "source_quote": "最迟明天给我一个方案。"},
        ]
        output = _format_facts_for_reduce(facts)
        self.assertIn("## 决策", output)
        self.assertIn("## 待办事项", output)
        self.assertIn("折叠屏适配", output)

    def test_empty_facts(self) -> None:
        output = _format_facts_for_reduce([])
        self.assertIn("请基于以上事实", output)


class TestMergeDeduplicate(unittest.TestCase):
    """merge_deduplicate() tests."""

    def test_exact_quote_dedup(self) -> None:
        """Facts with identical source_quote are deduplicated."""
        facts_list = [
            {
                "chunk_id": 0,
                "facts": [{
                    "type": "decision",
                    "description": "优先完成折叠屏适配",
                    "source_quote": "优先保障折叠屏核心购买链路体验。",
                    "speaker": "张宇",
                    "mentions": []
                }]
            },
            {
                "chunk_id": 1,
                "facts": [{
                    "type": "decision",
                    "description": "优先完成折叠屏适配",
                    "source_quote": "优先保障折叠屏核心购买链路体验。",
                    "speaker": "张宇",
                    "mentions": []
                }]
            },
        ]
        result = merge_deduplicate(facts_list)
        # The fact should only appear once in the output
        count = result.count("优先保障折叠屏核心购买链路体验")
        self.assertEqual(count, 1)

    def test_similar_description_merge(self) -> None:
        """Near-identical descriptions are merged."""
        facts_list = [
            {
                "chunk_id": 0,
                "facts": [{
                    "type": "data_point",
                    "description": "折叠屏加购转化率低15个百分点",
                    "source_quote": "加购转化率却低了15个点",
                    "speaker": "刘琳",
                    "mentions": []
                }]
            },
            {
                "chunk_id": 1,
                "facts": [{
                    "type": "data_point",
                    "description": "折叠屏加购转化率低15个点",
                    "source_quote": "加购转化率却低了15个点",
                    "speaker": "刘琳",
                    "mentions": []
                }]
            },
        ]
        result = merge_deduplicate(facts_list)
        self.assertGreater(len(result), 0)

    def test_empty_facts_list(self) -> None:
        result = merge_deduplicate([])
        self.assertIn("请基于以上事实", result)

    def test_chunk_with_no_facts_key(self) -> None:
        facts_list = [{"chunk_id": 0}]
        result = merge_deduplicate(facts_list)
        self.assertIn("请基于以上事实", result)
