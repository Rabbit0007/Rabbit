from __future__ import annotations

import json
import re
from typing import Any

FENCED_BLOCK_RE = re.compile(r"^[ \t]*```(?:json)?[ \t]*\n(.*?)\n[ \t]*```[ \t]*$", re.IGNORECASE | re.DOTALL | re.MULTILINE)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(_value):
    raise ValueError("non-finite numbers are not valid JSON")


def extract_json_object(text: str) -> dict[str, Any]:
    """Read one complete object, never salvage children of a damaged envelope.

    Markdown fences and prose around an object remain supported. An invalid
    outer object is an error, even if a nested Step/Finding is independently
    valid JSON. Multiple answers, duplicate keys and non-JSON numbers fail closed.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("no JSON object found in output")
    segment = text.strip()
    fences = list(FENCED_BLOCK_RE.finditer(segment))
    if segment.startswith(("{", "[")):
        fences = []  # Backticks inside a JSON string are just data.
    elif fences:
        if len(fences) != 1:
            raise ValueError("expected exactly one JSON response")
        match = fences[0]
        outside = segment[:match.start()] + segment[match.end():]
        if any(c in outside for c in "{[}"):
            raise ValueError("ambiguous JSON outside response fence")
        segment = match.group(1).strip()
    elif segment.startswith("```"):
        raise ValueError("unclosed JSON response fence")
    elif not segment.startswith(("{", "[")):
        start = segment.find("{")
        if start < 0:
            raise ValueError("no JSON object found in output")
        segment = segment[start:]

    decoder = json.JSONDecoder(object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    try:
        parsed, end = decoder.raw_decode(segment)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON response at line {exc.lineno}, column {exc.colno}") from exc
    remainder = segment[end:].strip()
    if remainder and (fences or any(c in remainder for c in '{}[]`')):
        raise ValueError("unexpected content after JSON response")
    if not isinstance(parsed, dict):
        raise ValueError("JSON response must be an object")
    return parsed


def serialization_diagnostic(text: str) -> str:
    """Describe syntax only, without echoing evidence, tokens, or string values.

    This is a repair prompt hint, never a parser or an automatic completion rule.
    The repaired response must still pass extract_json_object and its schema.
    """
    segment = text.strip()
    if segment.startswith('```'):
        segment = re.sub(r'^```(?:json)?\s*', '', segment, flags=re.IGNORECASE)
        segment = re.sub(r'\s*```$', '', segment)
    stack = []
    quoted = escaped = False
    mismatch = False
    for char in segment:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '{[':
            stack.append(char)
        elif char in '}]':
            if not stack or stack.pop() != ('{' if char == '}' else '['):
                mismatch = True
                break
    if quoted:
        return 'Validation diagnostic: unterminated string; do not guess missing evidence.'
    if stack and not mismatch:
        return f'Validation diagnostic: {len(stack)} object/array closing delimiters are missing. Close the OUTERMOST envelope too.'
    return 'Validation diagnostic: invalid syntax or schema. Verify the full envelope, field names, and all quote escaping.'
