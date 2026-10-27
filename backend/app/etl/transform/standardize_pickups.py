"""Pickup stop string standardization and alias resolution."""

import re
from typing import Dict
from ...utils.text import normalize_pickup_text

# Common spelling corrections for Pune transit landmarks
WORD_REPLACEMENTS = {
    r"\bchawk\b": "Chowk",
    r"\bchowck\b": "Chowk",
    r"\bchouk\b": "Chowk",
    r"\bchwock\b": "Chowk",
    r"\bphatta\b": "Phata",
    r"\bphatta\b": "Phata",
    r"\bfata\b": "Phata",
    r"\bhos\b": "Hospital",
    r"\bhosp\b": "Hospital",
    r"\bstn\b": "Station",
    r"\brly\b": "Railway",
    r"\bnagar\b": "Nagar",
    r"\bcol\b": "College",
    r"\bclg\b": "College",
}

CANONICAL_ALIASES = {
    "bhakti shakti": "Bhakti Shakti Nigdi",
    "bhakti shakti chowk": "Bhakti Shakti Nigdi",
    "nigdi flyover": "Bhakti Shakti Nigdi",
    "shastri chowk": "Shastri Chowk Bhosari",
    "nashik phata": "Nashik Phata Kasarwadi",
    "talegaon station": "Talegaon Railway Station",
    "somatane phata": "Somatne Phata",
}

def standardize_pickup_name(raw_name: str) -> str:
    """Applies domain vocabulary standardization and alias unification."""
    text = normalize_pickup_text(raw_name)
    if not text:
        return "Unknown Stop"

    lower = text.lower()
    for pattern, replacement in WORD_REPLACEMENTS.items():
        lower = re.sub(pattern, replacement.lower(), lower)

    # Check exact alias table
    clean_key = re.sub(r"[^a-z0-9]+", " ", lower).strip()
    if clean_key in CANONICAL_ALIASES:
        return CANONICAL_ALIASES[clean_key]

    return lower.title()
