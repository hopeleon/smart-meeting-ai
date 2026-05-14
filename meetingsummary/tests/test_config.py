"""Unit tests for config.py."""

import json
import os
import unittest
from pathlib import Path

from config import OllamaConfig, load_config

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class TestLoadConfig(unittest.TestCase):
    """Tests for load_config()."""

    def setUp(self) -> None:
        self.config_path = FIXTURES / "sample_config.json"

    def test_load_valid_config(self) -> None:
        config = load_config(self.config_path)
        self.assertIsInstance(config, OllamaConfig)
        self.assertEqual(config.base_url, "http://localhost:11434")
        self.assertEqual(config.model, "qwen3:8b")
        self.assertEqual(config.provider, "ollama")
        self.assertEqual(config.api_key, "")

    def test_env_override_model(self) -> None:
        os.environ["OLLAMA_MODEL"] = "llama3:8b"
        try:
            config = load_config(self.config_path)
            self.assertEqual(config.model, "llama3:8b")
            self.assertEqual(config.base_url, "http://localhost:11434")
        finally:
            del os.environ["OLLAMA_MODEL"]

    def test_env_override_base_url(self) -> None:
        os.environ["OLLAMA_BASE_URL"] = "http://192.168.1.100:11434"
        try:
            config = load_config(self.config_path)
            self.assertEqual(config.base_url, "http://192.168.1.100:11434")
            self.assertEqual(config.model, "qwen3:8b")
        finally:
            del os.environ["OLLAMA_BASE_URL"]

    def test_missing_config_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            load_config(Path("/nonexistent/config.json"))

    def test_invalid_json_config(self) -> None:
        bad_path = FIXTURES / "invalid_config.json"
        bad_path.write_text("not json", encoding="utf-8")
        try:
            with self.assertRaises(json.JSONDecodeError):
                load_config(bad_path)
        finally:
            bad_path.unlink()

    def test_ollamaconfig_is_immutable(self) -> None:
        config = OllamaConfig(base_url="http://x", model="m")
        with self.assertRaises(AttributeError):
            config.base_url = "new"  # type: ignore[misc]

    def test_env_override_provider(self) -> None:
        os.environ["OLLAMA_PROVIDER"] = "openai_compatible"
        try:
            config = load_config(self.config_path)
            self.assertEqual(config.provider, "openai_compatible")
        finally:
            del os.environ["OLLAMA_PROVIDER"]

    def test_env_override_api_key(self) -> None:
        os.environ["OLLAMA_API_KEY"] = "sk-test123"
        try:
            config = load_config(self.config_path)
            self.assertEqual(config.api_key, "sk-test123")
        finally:
            del os.environ["OLLAMA_API_KEY"]

    def test_defaults_when_fields_missing(self) -> None:
        old_style_path = FIXTURES / "old_style_config.json"
        old_style = {"ollama": {"base_url": "http://localhost:11434", "model": "qwen3:8b"}}
        import json as _json
        old_style_path.write_text(_json.dumps(old_style), encoding="utf-8")
        try:
            config = load_config(old_style_path)
            self.assertEqual(config.provider, "ollama")
            self.assertEqual(config.api_key, "")
        finally:
            old_style_path.unlink()


if __name__ == "__main__":
    unittest.main()
