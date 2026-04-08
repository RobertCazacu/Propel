"""
Color mapper — shared data types.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Literal, Optional


@dataclass
class ColorMappingResult:
    """Result of map_detected_color_to_allowed()."""
    mapped_value: Optional[str]            # exact value from allowed_values, or None
    score: float                           # 0.0–1.0
    method: Literal[
        "exact",    # exact / case-insensitive match
        "synonym",  # matched via canonical cluster
        "fuzzy",    # rapidfuzz token_set_ratio + jaccard
        "family",   # PIL color-family fallback
        "none",     # no match found
    ]
    top_k: list[tuple[str, float]]         # [(value, score), ...] top candidates
    needs_review: bool                     # True when confidence is uncertain
    debug: dict = field(default_factory=dict)


@dataclass
class ColorCandidate:
    """Internal candidate during scoring."""
    raw_value: str          # original string from allowed_values
    normalized: str         # normalize_color_text(raw_value)
    score: float = 0.0
    method: str = ""
