"""JSON extraction and validation for LLM responses."""

import json
from typing import Any


class JSONExtractionError(Exception):
    """Raised when JSON cannot be extracted from the LLM response."""

    def __init__(self, message: str, raw_response: str = ""):
        super().__init__(message)
        self.raw_response = raw_response


def extract_json(raw_response: str) -> dict[str, Any]:
    """Extract and parse a JSON object from an LLM response string.

    Applies a three-tier fallback strategy:
    1. Direct json.loads on the full response.
    2. Extract substring from first '{' to last '}' and re-parse.
    3. Raise JSONExtractionError.

    Args:
        raw_response: The raw text returned by the LLM.

    Returns:
        Parsed dictionary.

    Raises:
        JSONExtractionError: If all parsing strategies fail.
    """
    stripped = raw_response.strip()

    # Strategy 1: direct parse
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    # Strategy 2: extract from first '{' to last '}'
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end != -1 and start < end:
        try:
            return json.loads(stripped[start:end + 1])
        except json.JSONDecodeError:
            pass

    # Strategy 3: give up
    raise JSONExtractionError(
        "Cannot extract valid JSON from LLM response after all recovery attempts.",
        raw_response=raw_response,
    )


EXPECTED_TOP_KEYS = frozenset({
    "tldr", "meeting", "discussion_points", "decisions",
    "action_items", "issues_risks", "next_meeting",
})


def validate_schema(data: dict[str, Any]) -> list[str]:
    """Check that the parsed dictionary has the expected top-level keys.

    Args:
        data: Parsed meeting summary dictionary.

    Returns:
        List of missing top-level key names (empty if all present).
    """
    return sorted(EXPECTED_TOP_KEYS - set(data.keys()))
