"""Unit tests for file_handler.py."""

import json
import tempfile
import unittest
from pathlib import Path

from file_handler import read_transcript, write_debug_log, write_output

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestReadTranscript(unittest.TestCase):
    """Tests for read_transcript()."""

    def test_read_valid_file(self) -> None:
        content = read_transcript(FIXTURES / "sample_config.json")
        self.assertIn("ollama", content)

    def test_read_missing_file(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            read_transcript(Path("/nonexistent/file.txt"))
        self.assertEqual(cm.exception.code, 1)

    def test_read_empty_file(self) -> None:
        with self.assertRaises(SystemExit) as cm:
            read_transcript(FIXTURES / "empty_transcript.txt")
        self.assertEqual(cm.exception.code, 1)


class TestWriteOutput(unittest.TestCase):
    """Tests for write_output()."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_write_output_creates_dir(self) -> None:
        out_dir = self.tmpdir / "nested" / "output"
        json_data = {"meeting": {"title": "Test"}}
        md_text = "# Test Meeting"
        jp, mp = write_output(out_dir, "test", "20260511", json_data, md_text)
        self.assertTrue(jp.exists())
        self.assertTrue(mp.exists())
        self.assertIn("test_20260511.json", str(jp))
        self.assertIn("test_20260511.md", str(mp))

    def test_write_output_correct_filenames(self) -> None:
        json_data = {"meeting": {"title": "Q2 Planning"}}
        md_text = "# Q2 Planning"
        jp, mp = write_output(self.tmpdir, "weekly", "20260511", json_data, md_text)
        self.assertEqual(jp.name, "weekly_20260511.json")
        self.assertEqual(mp.name, "weekly_20260511.md")

    def test_write_json_ensure_ascii_false(self) -> None:
        json_data = {"meeting": {"title": "中文测试"}}
        jp, _ = write_output(self.tmpdir, "s", "20260511", json_data, "# ok")
        raw = jp.read_text(encoding="utf-8")
        self.assertIn("中文测试", raw)
        self.assertNotIn("\\u", raw)

    def test_write_output_json_content(self) -> None:
        json_data = {"key": "value"}
        jp, _ = write_output(self.tmpdir, "s", "20260511", json_data, "# ok")
        parsed = json.loads(jp.read_text(encoding="utf-8"))
        self.assertEqual(parsed, json_data)


class TestWriteDebugLog(unittest.TestCase):
    """Tests for write_debug_log()."""

    def setUp(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_write_debug_log(self) -> None:
        raw = "some raw llm response { broken json"
        log_path = write_debug_log(self.tmpdir, "20260511", raw)
        self.assertTrue(log_path.exists())
        self.assertEqual(log_path.name, "summary_20260511_debug.log")
        self.assertEqual(log_path.read_text(encoding="utf-8"), raw)


if __name__ == "__main__":
    unittest.main()
