"""
Image analysis orchestrator.

Combines:
  1. Algorithmic color detection (always, when enable_color=True)
  2. Optional vision-model product hint (when enable_product_hint=True)

Returns an ImageAnalysisResult with suggested_attributes already mapped
to the marketplace's valid values and ready to be merged into new_chars.

This module is the ONLY entry point needed by the rest of the app.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
from PIL import Image
from core.app_logger import get_logger
from core.vision.image_fetcher import fetch_image
from core.vision.color_analyzer import analyze_colors, ColorResult, find_multicolor_value, pick_best_accepted_color
from core.vision.visual_rules import get_category_rules, load_rules, ensure_rules_file

log = get_logger("marketplace.vision.analyzer")


# ── Result schema ─────────────────────────────────────────────────────────────

@dataclass
class ImageAnalysisResult:
    # Fetch
    image_url: str             = ""
    download_success: bool     = False
    download_error: str        = ""

    # Color
    dominant_color_raw: str         = ""
    dominant_color_normalized: str  = ""
    secondary_color_raw: str        = ""
    secondary_color_normalized: str = ""
    is_multicolor: bool             = False
    color_confidence: float         = 0.0

    # Vision product hint (optional)
    product_type_hint: str          = ""
    product_type_confidence: float  = 0.0
    used_for_category_support: bool = False

    # Output
    suggested_attributes: dict = field(default_factory=dict)
    used_for_attribute_fill: bool = False

    # Review flags
    needs_review: bool   = False
    review_reason: str   = ""
    skipped_reason: str  = ""

    def to_dict(self) -> dict:
        return {
            "image_url":                  self.image_url,
            "download_success":           self.download_success,
            "download_error":             self.download_error,
            "dominant_color_raw":         self.dominant_color_raw,
            "dominant_color_normalized":  self.dominant_color_normalized,
            "secondary_color_raw":        self.secondary_color_raw,
            "secondary_color_normalized": self.secondary_color_normalized,
            "is_multicolor":              self.is_multicolor,
            "color_confidence":           self.color_confidence,
            "product_type_hint":          self.product_type_hint,
            "product_type_confidence":    self.product_type_confidence,
            "used_for_category_support":  self.used_for_category_support,
            "used_for_attribute_fill":    self.used_for_attribute_fill,
            "suggested_attributes":       self.suggested_attributes,
            "needs_review":               self.needs_review,
            "review_reason":              self.review_reason,
            "skipped_reason":             self.skipped_reason,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _missing_color_char(
    color_chars: list,
    mandatory_chars: list,
    all_filled: dict,
) -> Optional[str]:
    """Return the first color characteristic that is both mandatory and missing."""
    for ch in color_chars:
        if ch in mandatory_chars and not all_filled.get(ch):
            return ch
    return None


def _map_to_valid(color_name: str, valid_values: set) -> Optional[str]:
    """
    Map a normalized color name to an exact marketplace valid value.
    Tries exact match, then case-insensitive match.
    """
    if not valid_values or not color_name:
        return None
    if color_name in valid_values:
        return color_name
    lower_map = {v.lower(): v for v in valid_values}
    return lower_map.get(color_name.lower())


# ── Main entry ────────────────────────────────────────────────────────────────

def analyze_product_image(
    image_url: str,
    category: str,
    existing_chars: dict,
    valid_values_for_cat: dict,
    mandatory_chars: list,
    marketplace: str = "",
    offer_id: str = "",
    enable_color: bool = True,
    enable_product_hint: bool = False,
    vision_provider=None,
    sku: str = "",
) -> ImageAnalysisResult:
    """
    Main entry point for image-based attribute extraction.

    Args:
        image_url:           Public URL of the product image.
        category:            Resolved category name for this product.
        existing_chars:      Already-filled characteristics (from offer + rule detection).
        valid_values_for_cat: {char_name: set_of_valid_values} for the category.
        mandatory_chars:     List of mandatory characteristic names for the category.
        marketplace:         Marketplace name (for logging).
        offer_id:            Offer ID (for logging + cache key fallback).
        enable_color:        Run algorithmic color detection.
        enable_product_hint: Run vision model for product type hint.
        vision_provider:     BaseVisionProvider instance (needed only if enable_product_hint).
        sku:                 Product SKU — used as image cache filename if provided.

    Returns:
        ImageAnalysisResult with suggested_attributes ready to merge into new_chars.
    """
    ensure_rules_file()
    result = ImageAnalysisResult(image_url=image_url)

    if not image_url or not image_url.strip().startswith("http"):
        result.skipped_reason = "No valid image URL"
        return result

    if not enable_color and not enable_product_hint:
        result.skipped_reason = "Image analysis disabled"
        return result

    # ── Load rules ─────────────────────────────────────────────────────────────
    rules     = load_rules()
    cat_rules = get_category_rules(category, rules)

    color_chars         = cat_rules.get("color_mandatory_chars", [])
    min_confidence      = cat_rules.get("min_color_confidence", 0.60)
    prefer_text         = cat_rules.get("prefer_text_color_over_image", True)
    fallback_review     = cat_rules.get("fallback_to_review_if_conflict", True)
    multicolor_threshold= cat_rules.get("multicolor_threshold", 0.80)
    min_prod_confidence = cat_rules.get("min_product_confidence", 0.65)

    # ── Fetch image ────────────────────────────────────────────────────────────
    img, error = fetch_image(image_url, sku=sku or offer_id)
    if error or img is None:
        result.download_success = False
        result.download_error   = error or "Unknown fetch error"
        result.skipped_reason   = f"Image download failed: {result.download_error}"
        log.warning("[Vision] Fetch failed offer=%s url=%s err=%s", offer_id, image_url[:80], error)
        return result

    result.download_success = True
    log.debug("[Vision] Image fetched offer=%s url=%s", offer_id, image_url[:80])

    # ── Color analysis ─────────────────────────────────────────────────────────
    if enable_color:
        try:
            color_res: ColorResult = analyze_colors(img)

            result.dominant_color_raw         = color_res.dominant_color_raw
            result.dominant_color_normalized  = color_res.dominant_color_normalized
            result.secondary_color_raw        = color_res.secondary_color_raw
            result.secondary_color_normalized = color_res.secondary_color_normalized
            result.color_confidence           = color_res.confidence
            result.is_multicolor              = color_res.is_multicolor

            log.debug(
                "[Vision] Color offer=%s dominant=%s conf=%.2f multicolor=%s",
                offer_id, color_res.dominant_color_normalized,
                color_res.confidence, color_res.is_multicolor,
            )

            # Find which color char is missing and mandatory
            missing_char = _missing_color_char(color_chars, mandatory_chars, existing_chars)

            if missing_char:
                valid_set = valid_values_for_cat.get(missing_char, set())

                # Case 1: Multicolor detected with high confidence
                if color_res.is_multicolor and color_res.confidence >= multicolor_threshold:
                    mc_val = find_multicolor_value(valid_set) if valid_set else "Multicolor"
                    if mc_val:
                        result.suggested_attributes[missing_char] = mc_val
                        result.used_for_attribute_fill = True
                        log.info("[Vision] Multicolor fill [%s] offer=%s", missing_char, offer_id)
                    else:
                        result.needs_review  = True
                        result.review_reason = "Multicolor detected but value not in marketplace list"

                # Case 2: Single dominant color with sufficient confidence
                elif color_res.dominant_color_normalized and color_res.confidence >= min_confidence:
                    normalized = color_res.dominant_color_normalized
                    family     = color_res.dominant_color_raw  # reused as family via analyzer

                    if valid_set:
                        # 1. Exact / case-insensitive match on normalized name
                        mapped = _map_to_valid(normalized, valid_set)
                        # 2. Fallback: family-term scoring against accepted values
                        if not mapped:
                            from core.vision.color_analyzer import _rgb_to_family, _norm
                            # derive family from raw RGB stored in dominant_color_raw
                            import re as _re
                            m = _re.match(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", color_res.dominant_color_raw)
                            if m:
                                fam = _rgb_to_family((int(m.group(1)), int(m.group(2)), int(m.group(3))))
                                mapped = pick_best_accepted_color(fam, valid_set) or None
                        if mapped:
                            result.suggested_attributes[missing_char] = mapped
                            result.used_for_attribute_fill = True
                            log.info("[Vision] Color fill [%s]=%r offer=%s", missing_char, mapped, offer_id)
                        else:
                            if fallback_review:
                                result.needs_review  = True
                                result.review_reason = (
                                    f"Color '{normalized}' detected but not in marketplace "
                                    f"valid values ({len(valid_set)} values)"
                                )
                    else:
                        # Freeform field — use normalized name directly
                        result.suggested_attributes[missing_char] = normalized
                        result.used_for_attribute_fill = True
                        log.info("[Vision] Freeform color fill [%s]=%r offer=%s", missing_char, normalized, offer_id)

                # Case 3: Low confidence
                else:
                    if color_res.dominant_color_normalized:
                        result.needs_review  = True
                        result.review_reason = (
                            f"Color detected ({color_res.dominant_color_normalized}) "
                            f"but confidence too low ({color_res.confidence:.2f} < {min_confidence})"
                        )

        except Exception as e:
            log.error("[Vision] Color analysis error offer=%s: %s", offer_id, e, exc_info=True)

    # ── Product type hint (vision model) ───────────────────────────────────────
    if enable_product_hint and vision_provider is not None:
        try:
            if vision_provider.is_available():
                prompt = (
                    f"You are analyzing a product image for a marketplace listing.\n"
                    f"Current category assigned from text: '{category}'.\n"
                    f"Look at the image and identify what product type this is in 3-5 words.\n"
                    f"Reply ONLY with the product type name, nothing else. No explanations."
                )
                hint = vision_provider.analyze(img, prompt)
                if hint and len(hint.strip()) > 2:
                    result.product_type_hint        = hint.strip()[:120]
                    result.product_type_confidence  = min_prod_confidence
                    result.used_for_category_support = True
                    log.info("[Vision] Product hint offer=%s: %r (cat: %s)", offer_id, hint[:60], category)
        except Exception as e:
            log.warning("[Vision] Product hint error offer=%s: %s", offer_id, e)

    return result
