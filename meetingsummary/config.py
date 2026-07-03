"""Configuration loading with environment variable overrides."""

import json
import os
from pathlib import Path
from typing import NamedTuple


class OllamaConfig(NamedTuple):
    """Immutable Ollama connection configuration.

    provider: "ollama" (local) or "openai_compatible" (external API).
    api_key: only needed for openai_compatible provider; leave empty for Ollama.
    chat_path: API endpoint path for openai_compatible (default /v1/chat/completions).
    """

    base_url: str
    model: str
    provider: str = "ollama"
    api_key: str = ""
    chat_path: str = "/v1/chat/completions"


def load_config(config_path: Path) -> OllamaConfig:
    """Load config from JSON file, with env var overrides.

    Args:
        config_path: Path to config.json.

    Returns:
        OllamaConfig with base_url and model resolved.

    Raises:
        FileNotFoundError: If config_path does not exist.
        json.JSONDecodeError: If the file contains invalid JSON.
    """
    with open(config_path, encoding="utf-8") as f:
        data = json.load(f)

    ollama = data["ollama"]
    base_url = os.environ.get("OLLAMA_BASE_URL", ollama["base_url"])
    model = os.environ.get("OLLAMA_MODEL", ollama["model"])
    provider = os.environ.get("OLLAMA_PROVIDER", ollama.get("provider", "ollama"))
    api_key = os.environ.get("OLLAMA_API_KEY", ollama.get("api_key", ""))
    chat_path = os.environ.get(
        "OLLAMA_CHAT_PATH",
        ollama.get("chat_path", "/v1/chat/completions"),
    )

    return OllamaConfig(
        base_url=base_url,
        model=model,
        provider=provider,
        api_key=api_key,
        chat_path=chat_path,
    )
