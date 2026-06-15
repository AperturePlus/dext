"""Name normalization helpers for org-unit display names."""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")
_OPEN_TO_CLOSE = {"(": ")", "（": "）"}
_CLOSE_TO_OPEN = {")": "(", "）": "（"}


def clean_org_unit_name(name: str | None) -> str | None:
    if name is None:
        return None
    s = _WS.sub(" ", str(name)).strip()
    if not s:
        return None
    s = re.sub(r"\s*([（(])\s*", r"\1", s)
    s = re.sub(r"\s*([）)])\s*", r"\1", s)
    result: list[str] = []
    stack: list[str] = []
    for ch in s:
        if ch in _OPEN_TO_CLOSE:
            stack.append(ch)
            result.append(ch)
        elif ch in _CLOSE_TO_OPEN:
            if stack and stack[-1] == _CLOSE_TO_OPEN[ch]:
                stack.pop()
                result.append(ch)
        else:
            result.append(ch)
    while stack:
        result.append(_OPEN_TO_CLOSE[stack.pop()])
    cleaned = _WS.sub(" ", "".join(result)).strip()
    return cleaned or None
