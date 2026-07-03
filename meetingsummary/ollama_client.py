"""LLM client supporting both Ollama (local) and OpenAI-compatible (external) APIs."""

import sys
import os
from typing import Any

import requests

from .config import OllamaConfig

DEFAULT_TIMEOUT = int(os.getenv("MEETING_SUMMARY_TIMEOUT", "600"))
DEFAULT_MAX_TOKENS = int(os.getenv("MEETING_SUMMARY_MAX_TOKENS", "2048"))


def call_ollama(
    config: OllamaConfig,
    system_prompt: str,
    transcript: str,
    timeout: int = 0,
) -> str:
    """Send a chat request and return the model response.

    Automatically adapts request format based on config.provider:
    - "ollama": POST /api/chat, no auth
    - "openai_compatible": POST /v1/chat/completions, Bearer auth

    Args:
        config: Connection configuration.
        system_prompt: System-level instruction for the model.
        transcript: The meeting transcript to summarize.
        timeout: Request timeout in seconds (0 = use MEETING_SUMMARY_TIMEOUT).

    Returns:
        The model's response text.

    Raises RuntimeError on connection or HTTP errors.
    """
    messages = [
        {"role": "system", "content": "/no_think\n" + system_prompt},
        {"role": "user", "content": transcript},
    ]

    if config.provider == "openai_compatible":
        return _call_openai_compatible(config, messages, timeout)
    else:
        return _call_ollama_api(config, messages, timeout)


def _call_ollama_api(
    config: OllamaConfig,
    messages: list[dict[str, str]],
    timeout: int,
) -> str:
    url = f"{config.base_url}/api/chat"
    payload = {
        "model": config.model,
        "messages": messages,
        "stream": False,
        "think": False,
        "options": {
            "temperature": 0.1,
            "num_predict": DEFAULT_MAX_TOKENS,
        },
    }
    timeout_val = DEFAULT_TIMEOUT if timeout <= 0 else timeout
    try:
        response = requests.post(url, json=payload, timeout=timeout_val)
    except requests.Timeout:
        raise RuntimeError(
            f"Ollama request timed out after {timeout_val} seconds. "
            "Increase timeout or check if Ollama is overloaded."
        )
    except requests.ConnectionError:
        raise RuntimeError(
            f"Cannot reach Ollama at {config.base_url}. "
            "Is Ollama running? Start it with: ollama serve"
        )

    try:
        response.raise_for_status()
    except requests.HTTPError:
        raise RuntimeError(
            f"Ollama returned HTTP {response.status_code}.\n"
            f"Response: {response.text}"
        )

    return response.json()["message"]["content"]


def _call_openai_compatible(
    config: OllamaConfig,
    messages: list[dict[str, str]],
    timeout: int,
) -> str:
    url = f"{config.base_url}{config.chat_path}"
    headers = {
        "Authorization": f"Bearer {config.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": config.model,
        "messages": messages,
        "temperature": 0.1,
        "max_tokens": DEFAULT_MAX_TOKENS,
    }
    timeout_val = DEFAULT_TIMEOUT if timeout <= 0 else timeout
    try:
        response = requests.post(
            url, json=payload, headers=headers, timeout=timeout_val
        )
    except requests.Timeout:
        raise RuntimeError(
            f"API request timed out after {timeout_val} seconds."
        )
    except requests.ConnectionError:
        raise RuntimeError(
            f"Cannot reach API at {config.base_url}. "
            "Check your network and base_url."
        )

    try:
        response.raise_for_status()
    except requests.HTTPError:
        raise RuntimeError(
            f"API returned HTTP {response.status_code}.\n"
            f"Response: {response.text}"
        )

    return response.json()["choices"][0]["message"]["content"]
