from __future__ import annotations

import re


def normalize_text(text: str) -> str:
    lowered = text.lower().strip()
    return re.sub(r"\s+", " ", lowered)

