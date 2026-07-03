"""Tests for chunker.py — speaker-turn-based transcript splitting."""

import unittest

from chunker import (
    _find_speaker_positions,
    _split_by_paragraphs,
    count_chinese_chars,
    split_by_speaker_turns,
)


class TestCountChineseChars(unittest.TestCase):
    """count_chinese_chars() tests."""

    def test_only_chinese(self) -> None:
        self.assertEqual(count_chinese_chars("你好世界"), 4)

    def test_mixed_with_english(self) -> None:
        text = "Hello 你好 World 世界"
        self.assertEqual(count_chinese_chars(text), 4)

    def test_empty_string(self) -> None:
        self.assertEqual(count_chinese_chars(""), 0)

    def test_punctuation_not_counted(self) -> None:
        text = "你好，世界！测试。"
        self.assertEqual(count_chinese_chars(text), 6)


class TestFindSpeakerPositions(unittest.TestCase):
    """_find_speaker_positions() tests."""

    def test_detects_chinese_names(self) -> None:
        text = "王磊：大家好。\n张宇：你好。\n陈思敏：我来说一下。"
        positions = _find_speaker_positions(text)
        self.assertEqual(len(positions), 3)

    def test_detects_english_colon(self) -> None:
        text = "李娜: 前端这边。\n王强:后端这边。"
        positions = _find_speaker_positions(text)
        self.assertEqual(len(positions), 2)

    def test_no_speaker_labels(self) -> None:
        text = "这是一段没有说话人标签的文本。\n没有冒号开头的内容。"
        positions = _find_speaker_positions(text)
        self.assertEqual(len(positions), 0)

    def test_only_speaker_at_line_start(self) -> None:
        text = "中间有王磊：这样的文本不应该被匹配。\n王磊：这才是正确的行。"
        positions = _find_speaker_positions(text)
        self.assertEqual(len(positions), 1)


class TestSplitBySpeakerTurns(unittest.TestCase):
    """split_by_speaker_turns() tests."""

    def _make_transcript(self, num_turns: int) -> str:
        """Build a transcript with *num_turns* speaker segments."""
        lines: list[str] = []
        names = ["王磊", "张宇", "李娜", "刘琳", "赵敏", "王强", "周涛",
                 "陈磊", "孙浩", "郑鑫", "徐峰", "郑琪", "陈思敏", "周恒",
                 "林芳", "黄志", "陈瑶", "孙莉", "王芳", "李明"]
        for i in range(num_turns):
            name = names[i % len(names)]
            lines.append(f"{name}：这是第{i + 1}轮发言的内容。")
        return "\n".join(lines)

    def test_short_transcript_single_chunk(self) -> None:
        """Transcript under max_turns returns a single chunk."""
        text = self._make_transcript(5)
        chunks = split_by_speaker_turns(text, max_turns=15)
        self.assertEqual(len(chunks), 1)

    def test_long_transcript_multiple_chunks(self) -> None:
        """Transcript over max_turns is split into multiple chunks."""
        text = self._make_transcript(35)
        chunks = split_by_speaker_turns(text, max_turns=15, overlap_turns=2)
        self.assertGreater(len(chunks), 1)

    def test_chunks_start_at_speaker_boundary(self) -> None:
        """Each chunk starts at a speaker turn."""
        text = self._make_transcript(35)
        chunks = split_by_speaker_turns(text, max_turns=15, overlap_turns=2)
        import re
        for chunk in chunks:
            self.assertTrue(
                re.match(r"^[一-龥]{2,4}[：:]", chunk),
                f"Chunk does not start with speaker label:\n{chunk[:50]}"
            )

    def test_non_first_chunks_have_context(self) -> None:
        """Chunks beyond the first include meeting context."""
        text = self._make_transcript(35)
        chunks = split_by_speaker_turns(text, max_turns=15, overlap_turns=2)
        # At least one non-first chunk should contain "..." separator
        context_chunks = [c for c in chunks if "...\n\n" in c or "\n...\n" in c]
        self.assertGreater(len(context_chunks), 0,
                           "Non-first chunks should have context prepended.")


class TestFallbackSplit(unittest.TestCase):
    """_split_by_paragraphs() fallback tests."""

    def test_paragraph_split(self) -> None:
        text = "段落1。\n\n段落2。\n\n段落3。"
        chunks = _split_by_paragraphs(text, max_chars=10)
        self.assertGreater(len(chunks), 1)

    def test_single_paragraph(self) -> None:
        text = "只有一个段落。"
        chunks = _split_by_paragraphs(text)
        self.assertEqual(len(chunks), 1)


class TestNoSpeakerLabelsFallback(unittest.TestCase):
    """When no speaker labels exist, split_by_speaker_turns falls back."""

    def test_falls_back_to_paragraphs(self) -> None:
        text = "\n\n".join(f"段落{i}" * 50 for i in range(10))
        chunks = split_by_speaker_turns(text, max_turns=15)
        # Should produce at least some chunks via paragraph fallback
        self.assertGreater(len(chunks), 0)
