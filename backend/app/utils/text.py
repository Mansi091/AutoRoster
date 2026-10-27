"""Text processing and normalization utilities."""

import re
from typing import Any, Optional

def normalize_pickup_text(text: Any) -> str:
    """Normalizes an Indian landmark or bus stop string for dictionary matching."""
    if text is None:
        return ""
    val = str(text).strip()
    val = re.sub(r"[,\-_/\\.]+", " ", val)
    val = re.sub(r"\s+", " ", val)
    return val.strip().title()

def normalize_coordinate_key(text: Any) -> str:
    """Normalizes a string into alphanumeric lower-case key for O(1) hash lookups."""
    if text is None:
        return ""
    return re.sub(r"[^a-z0-9]+", " ", str(text).lower()).strip()

def format_mobile_number(val: Any) -> str:
    """Sanitizes Indian mobile phone numbers."""
    if not val:
        return ""
    s = re.sub(r"[^\d]", "", str(val).split(".")[0])
    if len(s) == 12 and s.startswith("91"):
        s = s[2:]
    return s if len(s) == 10 else str(val).strip()
