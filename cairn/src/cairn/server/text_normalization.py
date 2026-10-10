from __future__ import annotations

import html


def normalize_hint_content(value: str) -> str:
    return html.unescape(value).strip()
