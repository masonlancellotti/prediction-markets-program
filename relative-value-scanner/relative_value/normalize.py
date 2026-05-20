from __future__ import annotations

import re
from datetime import datetime
from typing import Optional


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
    return _NON_ALNUM.sub(" ", value.lower()).strip()


def token_set(value: str) -> set[str]:
    return {part for part in normalize_text(value).split() if part}


def parse_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    normalized = value.strip()
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("datetime must include timezone information")
    return parsed
