"""
Strict validation gate for characteristic name/value matching.

All values emitted to output must pass through validate_new_chars_strict:
- char_name must exist in the characteristics table for the category
- value must be mappable to an entry in the values table, OR
- for non-restrictive characteristics with no values defined, the raw value
  is accepted as freeform (by design — these fields allow free text).
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from core.loader import MarketplaceData


def normalize_char_name(s: str) -> str:
    """Canonical lookup key for characteristic names.

    Strips whitespace, collapses multiple spaces, removes trailing ':', casefolds.
    'Culoare:' → 'culoare', '  Culoare  ' → 'culoare', 'CULOARE' → 'culoare'.
    """
    s = re.sub(r'\s+', ' ', str(s).strip())
    return s.rstrip(':').strip().casefold()


def validate_new_chars_strict(
    new_chars: dict,
    cat_id,
    data: "MarketplaceData",
    source: str = "unknown",
) -> tuple[dict, list[dict]]:
    """Validate a dict of {char_name: value} against the characteristics + values tables.

    Returns:
        accepted   — {canonical_display_name: mapped_valid_value}
                     Keys are the display names from the characteristics table.
        audit_log  — one entry per input char (both accepted and rejected) with:
                     source, char_input, char_canonical, char_output,
                     value_input, value_mapped, accept (bool), reason (str)

    Rejection reasons:
        char_not_in_characteristics  — char_name unknown for this category
        value_not_in_values_table    — char exists but value has no valid match
        no_values_defined_for_char   — char is restrictive and values table is empty
                                       (freeform non-restrictive chars are accepted as-is)
    """
    accepted: dict = {}
    audit_log: list[dict] = []

    for char_input, value_input in new_chars.items():
        val_str = str(value_input).strip() if value_input is not None else ""

        # ── 1. Resolve characteristic to canonical display name ─────────────
        canonical = data.canonical_char_name(cat_id, char_input)
        if canonical is None:
            audit_log.append({
                "source":         source,
                "char_input":     char_input,
                "char_canonical": None,
                "char_output":    None,
                "value_input":    val_str,
                "value_mapped":   None,
                "accept":         False,
                "reason":         "char_not_in_characteristics",
            })
            continue

        # ── 2. Map value to a valid option ──────────────────────────────────
        restrictive = data.is_restrictive(cat_id, canonical)

        mapped = data.find_valid(val_str, cat_id, canonical)
        if mapped is None:
            vs = data.valid_values(cat_id, canonical)
            if vs:
                reason = "value_not_in_values_table"
            else:
                # Try marketplace-level fallback (same char in other categories)
                fb = data.marketplace_fallback_values(canonical)
                if fb:
                    mapped = data._find_in_set(val_str, fb)
                if mapped is None:
                    if not restrictive or not vs:
                        # Non-restrictive char OR restrictive with no values defined:
                        # accept freeform value as-is (empty list = nothing to restrict to)
                        mapped = val_str
                    reason = (
                        "no_values_defined_for_char"
                        if not vs
                        else "value_not_in_values_table"
                    )

        if mapped is None:
            audit_log.append({
                "source":         source,
                "char_input":     char_input,
                "char_canonical": canonical,
                "char_output":    canonical,
                "value_input":    val_str,
                "value_mapped":   None,
                "accept":         False,
                "reason":         reason,
            })
            continue

        accepted[canonical] = mapped
        audit_log.append({
            "source":         source,
            "char_input":     char_input,
            "char_canonical": canonical,
            "char_output":    canonical,
            "value_input":    val_str,
            "value_mapped":   mapped,
            "accept":         True,
            "reason":         "ok",
        })

    return accepted, audit_log
