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
import time
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
    t_global = time.perf_counter()
    result   = ImageAnalysisResult(image_url=image_url)

    log.debug(
        "[Vision] Start analiza offer=%s category=%r enable_color=%s enable_hint=%s url=%s",
        offer_id, category, enable_color, enable_product_hint, image_url[:80],
    )

    if not image_url or not image_url.strip().startswith("http"):
        result.skipped_reason = "No valid image URL"
        log.warning("[Vision] Skipuit — URL invalid offer=%s url=%r", offer_id, image_url)
        return result

    if not enable_color and not enable_product_hint:
        result.skipped_reason = "Image analysis disabled"
        log.debug("[Vision] Skipuit — analiza dezactivata offer=%s", offer_id)
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
    log.debug("[Vision] Imagine descarcata cu succes offer=%s url=%s", offer_id, image_url[:80])

    # ── Color analysis ─────────────────────────────────────────────────────────
    if enable_color:
        try:
            t_color    = time.perf_counter()
            color_res: ColorResult = analyze_colors(img)
            color_ms   = round((time.perf_counter() - t_color) * 1000)

            result.dominant_color_raw         = color_res.dominant_color_raw
            result.dominant_color_normalized  = color_res.dominant_color_normalized
            result.secondary_color_raw        = color_res.secondary_color_raw
            result.secondary_color_normalized = color_res.secondary_color_normalized
            result.color_confidence           = color_res.confidence
            result.is_multicolor              = color_res.is_multicolor

            log.debug(
                "[Vision] Culoare detectata offer=%s dominant=%r(%s) secundar=%r(%s) "
                "conf=%.2f multicolor=%s palette=%s analiza=%dms",
                offer_id,
                color_res.dominant_color_normalized, color_res.dominant_color_raw,
                color_res.secondary_color_normalized, color_res.secondary_color_raw,
                color_res.confidence, color_res.is_multicolor,
                color_res.palette_rgb[:3],
                color_ms,
            )

            # Find which color char is missing and mandatory
            missing_char = _missing_color_char(color_chars, mandatory_chars, existing_chars)

            log.debug(
                "[Vision] Color chars de verificat: %s | mandatory: %s | missing_char: %s | offer=%s",
                color_chars, mandatory_chars, missing_char, offer_id,
            )

            if not missing_char:
                already = {ch: existing_chars.get(ch) for ch in color_chars if existing_chars.get(ch)}
                log.debug(
                    "[Vision] Toti color chars deja completati sau nu sunt obligatorii offer=%s: %s",
                    offer_id, already,
                )

            if missing_char:
                valid_set = valid_values_for_cat.get(missing_char, set())
                log.debug(
                    "[Vision] Camp culoare lipsa: [%s] | valori permise in marketplace: %d | offer=%s",
                    missing_char, len(valid_set), offer_id,
                )

                # Case 1: Multicolor detected with high confidence
                if color_res.is_multicolor and color_res.confidence >= multicolor_threshold:
                    mc_val = find_multicolor_value(valid_set) if valid_set else "Multicolor"
                    if mc_val:
                        result.suggested_attributes[missing_char] = mc_val
                        result.used_for_attribute_fill = True
                        log.info(
                            "[Vision] MULTICOLOR completat [%s]=%r conf=%.2f offer=%s",
                            missing_char, mc_val, color_res.confidence, offer_id,
                        )
                    else:
                        result.needs_review  = True
                        result.review_reason = "Multicolor detected but value not in marketplace list"
                        log.warning(
                            "[Vision] Multicolor detectat dar valoarea lipseste din lista marketplace "
                            "offer=%s valid_set_sample=%s",
                            offer_id, list(valid_set)[:5],
                        )

                # Case 2: Single dominant color with sufficient confidence
                elif color_res.dominant_color_normalized and color_res.confidence >= min_confidence:
                    normalized = color_res.dominant_color_normalized

                    if valid_set:
                        # 1. Exact / case-insensitive match on normalized name
                        mapped = _map_to_valid(normalized, valid_set)
                        log.debug(
                            "[Vision] Match exact/case-insensitive: %r -> %r offer=%s",
                            normalized, mapped, offer_id,
                        )
                        # 2. Fallback: family-term scoring against accepted values
                        if not mapped:
                            from core.vision.color_analyzer import _rgb_to_family
                            import re as _re
                            m = _re.match(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", color_res.dominant_color_raw)
                            if m:
                                fam = _rgb_to_family((int(m.group(1)), int(m.group(2)), int(m.group(3))))
                                mapped = pick_best_accepted_color(fam, valid_set) or None
                                log.debug(
                                    "[Vision] Fallback family scoring: rgb=%s family=%r -> mapped=%r offer=%s",
                                    color_res.dominant_color_raw, fam, mapped, offer_id,
                                )
                        if mapped:
                            result.suggested_attributes[missing_char] = mapped
                            result.used_for_attribute_fill = True
                            log.info(
                                "[Vision] CULOARE completata [%s]=%r (detectat=%r conf=%.2f) offer=%s",
                                missing_char, mapped, normalized, color_res.confidence, offer_id,
                            )
                        else:
                            if fallback_review:
                                result.needs_review  = True
                                result.review_reason = (
                                    f"Color '{normalized}' detected but not in marketplace "
                                    f"valid values ({len(valid_set)} values)"
                                )
                            log.warning(
                                "[Vision] Culoare %r nu exista in lista marketplace "
                                "[%s] (valid_count=%d sample=%s) offer=%s",
                                normalized, missing_char, len(valid_set),
                                list(valid_set)[:6], offer_id,
                            )
                    else:
                        # Freeform field — use normalized name directly
                        result.suggested_attributes[missing_char] = normalized
                        result.used_for_attribute_fill = True
                        log.info(
                            "[Vision] CULOARE freeform completata [%s]=%r offer=%s",
                            missing_char, normalized, offer_id,
                        )

                # Case 3: Low confidence
                else:
                    if color_res.dominant_color_normalized:
                        result.needs_review  = True
                        result.review_reason = (
                            f"Color detected ({color_res.dominant_color_normalized}) "
                            f"but confidence too low ({color_res.confidence:.2f} < {min_confidence})"
                        )
                        log.warning(
                            "[Vision] Confidence prea mica — nu completam: "
                            "culoare=%r conf=%.2f < prag=%.2f offer=%s",
                            color_res.dominant_color_normalized,
                            color_res.confidence, min_confidence, offer_id,
                        )
                    else:
                        log.warning(
                            "[Vision] Nicio culoare detectata in imagine offer=%s url=%s",
                            offer_id, image_url[:80],
                        )

        except Exception as e:
            log.error("[Vision] Eroare analiza culori offer=%s: %s", offer_id, e, exc_info=True)

    # ── Product type hint (vision model) ───────────────────────────────────────
    if enable_product_hint and vision_provider is not None:
        if not vision_provider.is_available():
            log.warning("[Vision] Vision provider indisponibil — product hint sarit offer=%s", offer_id)
        else:
            try:
                t_hint = time.perf_counter()
                prompt = (
                    f"You are analyzing a product image for a marketplace listing.\n"
                    f"Current category assigned from text: '{category}'.\n"
                    f"Look at the image and identify what product type this is in 3-5 words.\n"
                    f"Reply ONLY with the product type name, nothing else. No explanations."
                )
                hint    = vision_provider.analyze(img, prompt)
                hint_ms = round((time.perf_counter() - t_hint) * 1000)
                if hint and len(hint.strip()) > 2:
                    result.product_type_hint         = hint.strip()[:120]
                    result.product_type_confidence   = min_prod_confidence
                    result.used_for_category_support = True
                    log.info(
                        "[Vision] Product hint offer=%s: %r (categorie curenta: %r) timp=%dms",
                        offer_id, hint[:80], category, hint_ms,
                    )
                else:
                    log.warning(
                        "[Vision] Product hint raspuns gol sau prea scurt offer=%s raspuns=%r timp=%dms",
                        offer_id, hint, hint_ms,
                    )
            except Exception as e:
                log.warning("[Vision] Eroare product hint offer=%s: %s", offer_id, e, exc_info=True)
    elif enable_product_hint and vision_provider is None:
        log.warning("[Vision] enable_product_hint=True dar vision_provider=None offer=%s", offer_id)

    # ── Summary log ────────────────────────────────────────────────────────────
    total_ms = round((time.perf_counter() - t_global) * 1000)
    log.info(
        "[Vision] Analiza completa offer=%s total=%dms | "
        "filled=%s needs_review=%s skipped=%r | "
        "culoare=%r conf=%.2f",
        offer_id, total_ms,
        list(result.suggested_attributes.keys()) or "nimic",
        result.needs_review,
        result.skipped_reason or None,
        result.dominant_color_normalized,
        result.color_confidence,
    )

    return result
