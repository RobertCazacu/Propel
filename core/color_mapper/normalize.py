"""
Color text normalization — language-agnostic.

normalize_color_text("Albastru-Închis") → "albastru inchis"
normalize_color_text("Türkiz") → "turkiz"
normalize_color_text("Μαύρο") → "μαυρο"  (Greek stays, diacritics stripped)
"""
from __future__ import annotations
import re
import unicodedata


def normalize_color_text(text: str) -> str:
    """
    Normalize a color string for comparison:
      1. Strip + lowercase
      2. Unicode NFKD decompose
      3. Remove combining diacritics (é→e, ă→a, ő→o, etc.)
      4. Replace punctuation / separators with space
      5. Collapse whitespace
    """
    if not text:
        return ""
    text = str(text).strip().lower()
    # NFKD decomposition → split base + combining chars
    text = unicodedata.normalize("NFKD", text)
    # Remove combining diacritical marks
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    # Replace hyphens, slashes, underscores, dots used as separators → space
    text = re.sub(r"[-/_.,;:]+", " ", text)
    # Remove other non-alphanumeric, non-space characters
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text
