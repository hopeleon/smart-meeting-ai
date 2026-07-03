"""Unit tests for ollama_client.py."""

import unittest
from unittest.mock import patch

import requests

from config import OllamaConfig
from ollama_client import call_ollama

OLLAMA_CONFIG = OllamaConfig(base_url="http://localhost:11434", model="qwen3:8b")
OPENAI_CONFIG = OllamaConfig(
    base_url="https://api.deepseek.com",
    model="deepseek-chat",
    provider="openai_compatible",
    api_key="sk-test",
)
SYSTEM_PROMPT = "You are a summarizer."
TRANSCRIPT = "Meeting text here."


class TestCallOllama(unittest.TestCase):
    """Tests for call_ollama() with Ollama provider."""

    def test_successful_call(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.json.return_value = {
            "message": {"content": '{"meeting": {"title": "Test"}}'}
        }
        with patch("ollama_client.requests.post", return_value=mock_resp) as mock_post:
            result = call_ollama(OLLAMA_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        self.assertIn("Test", result)
        mock_post.assert_called_once()
        call_args = mock_post.call_args
        self.assertEqual(call_args[1]["json"]["stream"], False)
        self.assertAlmostEqual(
            call_args[1]["json"]["options"]["temperature"], 0.1
        )

    def test_correct_payload_structure(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.json.return_value = {"message": {"content": "{}"}}
        with patch("ollama_client.requests.post", return_value=mock_resp) as mock_post:
            call_ollama(OLLAMA_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["model"], "qwen3:8b")
        self.assertEqual(payload["messages"][0]["role"], "system")
        self.assertEqual(payload["messages"][0]["content"], SYSTEM_PROMPT)
        self.assertEqual(payload["messages"][1]["role"], "user")
        self.assertEqual(payload["messages"][1]["content"], TRANSCRIPT)
        self.assertIn("timeout", mock_post.call_args[1])

    def test_connection_refused(self) -> None:
        with patch(
            "ollama_client.requests.post",
            side_effect=requests.ConnectionError,
        ):
            with self.assertRaises(RuntimeError) as cm:
                call_ollama(OLLAMA_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        self.assertIn("Cannot reach Ollama", str(cm.exception))

    def test_timeout(self) -> None:
        with patch(
            "ollama_client.requests.post",
            side_effect=requests.Timeout,
        ):
            with self.assertRaises(RuntimeError) as cm:
                call_ollama(OLLAMA_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        self.assertIn("timed out", str(cm.exception))

    def test_http_error(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_resp.raise_for_status.side_effect = requests.HTTPError(
            "500 Server Error"
        )
        with patch("ollama_client.requests.post", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as cm:
                call_ollama(OLLAMA_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        self.assertIn("HTTP", str(cm.exception))


class TestCallOpenAICompatible(unittest.TestCase):
    """Tests for call_ollama() with openai_compatible provider."""

    def test_successful_call(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": '{"meeting": {"title": "Test"}}'}}]
        }
        with patch("ollama_client.requests.post", return_value=mock_resp) as mock_post:
            result = call_ollama(OPENAI_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        self.assertIn("Test", result)
        mock_post.assert_called_once()

    def test_correct_url_and_headers(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
        with patch("ollama_client.requests.post", return_value=mock_resp) as mock_post:
            call_ollama(OPENAI_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        call_args = mock_post.call_args
        self.assertIn("/v1/chat/completions", call_args[0][0])
        headers = call_args[1]["headers"]
        self.assertEqual(headers["Authorization"], "Bearer sk-test")

    def test_payload_has_temperature_at_top_level(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.json.return_value = {"choices": [{"message": {"content": "{}"}}]}
        with patch("ollama_client.requests.post", return_value=mock_resp) as mock_post:
            call_ollama(OPENAI_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["temperature"], 0.1)
        self.assertNotIn("options", payload)

    def test_connection_refused(self) -> None:
        with patch(
            "ollama_client.requests.post",
            side_effect=requests.ConnectionError,
        ):
            with self.assertRaises(RuntimeError) as cm:
                call_ollama(OPENAI_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        self.assertIn("Cannot reach API", str(cm.exception))

    def test_http_error(self) -> None:
        mock_resp = unittest.mock.Mock()
        mock_resp.status_code = 401
        mock_resp.text = "Unauthorized"
        mock_resp.raise_for_status.side_effect = requests.HTTPError("401")
        with patch("ollama_client.requests.post", return_value=mock_resp):
            with self.assertRaises(RuntimeError) as cm:
                call_ollama(OPENAI_CONFIG, SYSTEM_PROMPT, TRANSCRIPT)
        self.assertIn("HTTP", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
