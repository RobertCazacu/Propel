"""
core.color_mapper — production-grade multilingual color mapping.

Public API:
    from core.color_mapper import map_detected_color_to_allowed, ColorMappingResult
"""
from core.color_mapper.types import ColorMappingResult, ColorCandidate
from core.color_mapper.mapper import map_detected_color_to_allowed
from core.color_mapper.normalize import normalize_color_text

__all__ = [
    "map_detected_color_to_allowed",
    "ColorMappingResult",
    "ColorCandidate",
    "normalize_color_text",
]
