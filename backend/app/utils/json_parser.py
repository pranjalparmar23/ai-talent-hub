"""Robust JSON extraction from LLM responses.

LLMs frequently wrap JSON in markdown code fences (```json ... ```) or add
preamble text ("Here's the JSON:") despite instructions to return only JSON.
This module normalizes their output before parsing.
"""
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


class LLMJSONError(Exception):
    """Raised when the LLM output cannot be parsed as JSON after normalization."""


# Matches ```json ... ``` OR ``` ... ``` code fences (with or without language tag).
# Non-greedy, DOTALL so it handles multi-line JSON blobs.
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*\n?(.*?)```", re.DOTALL)


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if present, keep the inner content."""
    match = _FENCE_RE.search(text)
    if match:
        return match.group(1).strip()
    return text.strip()


def _extract_json_span(text: str) -> str:
    """Find the first {...} or [...] span in text using brace balancing.

    Handles cases where the LLM adds preamble/postamble around valid JSON:
        'Here is the analysis: {"score": 85} — hope this helps!'
    """
    for start_ch, end_ch in [("{", "}"), ("[", "]")]:
        start = text.find(start_ch)
        if start == -1:
            continue

        depth = 0
        in_string = False
        escape_next = False

        for i in range(start, len(text)):
            ch = text[i]

            if escape_next:
                escape_next = False
                continue
            if ch == "\\":
                escape_next = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue

            if ch == start_ch:
                depth += 1
            elif ch == end_ch:
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

    return text  # Fallback — let json.loads throw a real error


def parse_llm_json(raw: str, *, fallback: Any = None) -> Any:
    """Extract and parse a JSON object/array from LLM output.

    Handles:
      - Markdown code fences (```json ... ```)
      - Surrounding text ("Here you go: {...} thanks!")
      - Trailing commas (common LLM mistake)

    Args:
        raw: The LLM's raw string output.
        fallback: If provided, returned when parsing fails instead of raising.

    Returns:
        The parsed dict/list.

    Raises:
        LLMJSONError: If parsing fails and no fallback given.
    """
    if not raw or not raw.strip():
        if fallback is not None:
            return fallback
        raise LLMJSONError("LLM returned empty output")

    # 1. Strip markdown fences if present
    cleaned = _strip_fences(raw)

    # 2. First attempt — clean output, no surgery
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    # 3. Extract the {...} or [...] span, ignoring preamble/postamble
    extracted = _extract_json_span(cleaned)
    try:
        return json.loads(extracted)
    except json.JSONDecodeError:
        pass

    # 4. Repair trailing commas — {"a": 1,} is invalid JSON but common LLM output
    repaired = re.sub(r",(\s*[}\]])", r"\1", extracted)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError as e:
        # 5. Give up
        logger.warning(
            "LLM JSON parse failed. First 200 chars: %s", raw[:200].replace("\n", " ")
        )
        if fallback is not None:
            return fallback
        raise LLMJSONError(f"Could not parse LLM output as JSON: {e}") from e