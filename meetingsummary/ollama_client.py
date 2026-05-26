"""LLM client supporting both Ollama (local) and OpenAI-compatible (external) APIs."""

import sys
from typing import Any

import requests

from config import OllamaConfig


def call_ollama(
    config: OllamaConfig,
    system_prompt: str,
    transcript: str,
    timeout: int = 120,
) -> str:
    """Send a chat request and return the model response.

    Automatically adapts request format based on config.provider:
    - "ollama": POST /api/chat, no auth
    - "openai_compatible": POST /v1/chat/completions, Bearer auth

    Args:
        config: Connection configuration.
        system_prompt: System-level instruction for the model.
        transcript: The meeting transcript to summarize.
        timeout: Request timeout in seconds (default 120).

    Returns:
        The model's response text.

    Exits with code 1 on connection or HTTP errors.
    """
    messages = [
        {"role": "system", "content": system_prompt},
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
        "options": {"temperature": 0.1},
    }
    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.ConnectionError:
        print(
            f"Error: Cannot reach Ollama at {config.base_url}. "
            "Is Ollama running? Start it with: ollama serve"
        )
        sys.exit(1)
    except requests.Timeout:
        print(f"Error: Request to Ollama timed out after {timeout} seconds.")
        sys.exit(1)

    try:
        response.raise_for_status()
    except requests.HTTPError:
        print(
            f"Error: Ollama returned HTTP {response.status_code}.\n"
            f"Response: {response.text}"
        )
        sys.exit(1)

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
    }
    try:
        response = requests.post(
            url, json=payload, headers=headers, timeout=timeout
        )
    except requests.ConnectionError:
        print(
            f"Error: Cannot reach API at {config.base_url}. "
            "Check your network and base_url."
        )
        sys.exit(1)
    except requests.Timeout:
        print(f"Error: Request to API timed out after {timeout} seconds.")
        sys.exit(1)

    try:
        response.raise_for_status()
    except requests.HTTPError:
        print(
            f"Error: API returned HTTP {response.status_code}.\n"
            f"Response: {response.text}"
        )
        sys.exit(1)

    return response.json()["choices"][0]["message"]["content"]
