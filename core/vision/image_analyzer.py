"""
Image analysis orchestrator.

Combines (in order, all optional):
  1. YOLO object detection   (enable_yolo)
  2. Crop to best detection  (automatic when YOLO has result)
  3. Algorithmic color detection on crop (enable_color)
  4. CLIP semantic scoring   (enable_clip)
  5. Ollama vision product hint (enable_product_hint)

Returns an ImageAnalysisResult with suggested_attributes already mapped
to marketplace valid values and ready to be merged into new_chars.

Backward-compatible: all existing callers work unchanged.  New params have
safe defaults that preserve the original behavior.
"""
from __future__ import annotations
import time
import re
from dataclasses import dataclass, field
from typing import Optional
from PIL import Image
from core.app_logger import get_logger
from core.vision.image_fetcher import fetch_image
from core.vision.color_analyzer import (
    analyze_colors, ColorResult, find_multicolor_value, pick_best_accepted_color,
    _rgb_to_family,
)
from core.vision.visual_rules import get_category_rules, load_rules, ensure_rules_file, is_vision_eligible

log = get_logger("marketplace.vision.analyzer")


# ── Result schema ─────────────────────────────────────────────────────────────

@dataclass
class ImageAnalysisResult:
    # Fetch
    image_url: str              = ""
    download_success: bool      = False
    download_error: str         = ""

    # Color (PIL-based or OpenCV)
    dominant_color_raw: str         = ""
    dominant_color_normalized: str  = ""
    secondary_color_raw: str        = ""
    secondary_color_normalized: str = ""
    is_multicolor: bool             = False
    color_confidence: float         = 0.0

    # YOLO
    detected_object: str        = ""
    yolo_confidence: float      = 0.0
    yolo_bbox: list             = field(default_factory=list)
    yolo_fallback_used: bool    = False
    used_crop: bool             = False

    # CLIP
    clip_best_label: str        = ""
    clip_scores: dict           = field(default_factory=dict)
    clip_confidence: float      = 0.0

    # Vision product hint (Ollama)
    product_type_hint: str          = ""
    product_type_confidence: float  = 0.0
    used_for_category_support: bool = False

    # Structured vision extraction (cloud only, feature-flagged)
    vision_extracted_attrs: dict = field(default_factory=dict)   # validated attrs from cloud
    vision_extraction_error: str = ""

    # Output
    suggested_attributes: dict  = field(default_factory=dict)
    used_for_attribute_fill: bool = False

    # Review / skip flags
    needs_review: bool          = False
    review_reason: str          = ""
    skipped_reason: str         = ""

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
            "detected_object":            self.detected_object,
            "yolo_confidence":            self.yolo_confidence,
            "yolo_bbox":                  self.yolo_bbox,
            "yolo_fallback_used":         self.yolo_fallback_used,
            "used_crop":                  self.used_crop,
            "clip_best_label":            self.clip_best_label,
            "clip_scores":                self.clip_scores,
            "clip_confidence":            self.clip_confidence,
            "product_type_hint":          self.product_type_hint,
            "product_type_confidence":    self.product_type_confidence,
            "used_for_category_support":  self.used_for_category_support,
            "used_for_attribute_fill":    self.used_for_attribute_fill,
            "vision_extracted_attrs":     self.vision_extracted_attrs,
            "vision_extraction_error":    self.vision_extraction_error,
            "suggested_attributes":       self.suggested_attributes,
            "needs_review":               self.needs_review,
            "review_reason":              self.review_reason,
            "skipped_reason":             self.skipped_reason,
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _missing_color_char(color_chars, mandatory_chars, all_filled) -> Optional[str]:
    for ch in color_chars:
        if ch in mandatory_chars and not all_filled.get(ch):
            return ch
    return None


def _map_to_valid(color_name: str, valid_values: set) -> Optional[str]:
    if not valid_values or not color_name:
        return None
    if color_name in valid_values:
        return color_name
    lower_map = {v.lower(): v for v in valid_values}
    return lower_map.get(color_name.lower())


def _build_clip_labels(category: str, valid_values_for_cat: dict) -> list[str]:
    """
    Build a focused list of candidate labels for CLIP scoring.
    Uses the category name + generic product-type terms.
    """
    base = [
        "t-shirt", "hoodie", "jacket", "pants", "shorts", "shoes", "sneakers",
        "dress", "shirt", "coat", "bag", "backpack", "hat", "cap", "socks",
        "sportswear", "running shoes", "football", "basketball",
    ]
    labels = [category] if category else []
    labels += base
    # Deduplicate preserving order
    seen: set = set()
    result = []
    for lbl in labels:
        if lbl.lower() not in seen:
            seen.add(lbl.lower())
            result.append(lbl)
    return result[:25]


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
    # ── New optional params (all backward-compat defaults) ────────────────────
    enable_yolo: bool = False,
    enable_clip: bool = False,
    yolo_model: str = "yolov8n.pt",
    clip_model: str = "ViT-B-32",
    yolo_conf: float = 0.35,
    clip_conf: float = 0.25,
    suggestion_only: bool = False,
    save_debug: bool = False,
    run_logger=None,           # VisionRunLogger | None
    # ── Structured vision extraction (cloud only, off by default) ─────────────
    enable_structured_vision: bool = False,   # feature flag — default OFF
    cloud_vision_provider=None,               # OpenAIVisionProvider | None
    data=None,                                # MarketplaceData | None
    cat_id=None,                              # category id for validation
) -> ImageAnalysisResult:
    """
    Main entry point for image-based attribute extraction.
    All new parameters are optional with safe defaults that preserve
    the original behavior when not supplied.
    """
    ensure_rules_file()
    t_global = time.perf_counter()
    result   = ImageAnalysisResult(image_url=image_url)

    # ── Early guards ──────────────────────────────────────────────────────────
    if not image_url or not image_url.strip().startswith("http"):
        result.skipped_reason = "No valid image URL"
        log.warning("[Vision] Skip — URL invalid offer=%s", offer_id)
        if run_logger:
            run_logger.log("pipeline", "skipped", offer_id=offer_id, image_url=image_url,
                           status="skip", level="WARNING",
                           data={"reason": "invalid or missing URL"})
            run_logger.inc("skipped")
        return result

    any_enabled = enable_color or enable_product_hint or enable_yolo or enable_clip or enable_structured_vision
    if not any_enabled:
        result.skipped_reason = "Image analysis disabled"
        log.debug("[Vision] Skip — analiza dezactivata offer=%s", offer_id)
        if run_logger:
            run_logger.log("pipeline", "skipped", offer_id=offer_id, image_url=image_url,
                           status="skip", level="DEBUG",
                           data={"reason": "all image analysis flags are False"})
            run_logger.inc("skipped")
        return result

    # ── Load rules ────────────────────────────────────────────────────────────
    rules     = load_rules()
    cat_rules = get_category_rules(category, rules)

    color_chars          = cat_rules.get("color_mandatory_chars", [])
    min_color_conf       = cat_rules.get("min_color_confidence", 0.60)
    prefer_text_color    = cat_rules.get("prefer_text_color_over_image", True)
    fallback_review      = cat_rules.get("fallback_to_review_if_conflict", True)
    multicolor_threshold = cat_rules.get("multicolor_threshold", 0.80)
    min_prod_conf        = cat_rules.get("min_product_confidence", 0.65)
    effective_yolo_conf  = cat_rules.get("min_yolo_confidence", yolo_conf)
    effective_clip_conf  = cat_rules.get("min_clip_confidence", clip_conf)
    effective_yolo_allowlist = cat_rules.get("yolo_label_allowlist", [])
    effective_suggestion = suggestion_only or cat_rules.get("suggestion_only", False)

    if run_logger:
        run_logger.log(
            "pipeline", "start", offer_id=offer_id, image_url=image_url,
            level="INFO",
            data={
                "category": category, "marketplace": marketplace,
                "enable_color": enable_color, "enable_yolo": enable_yolo,
                "enable_clip": enable_clip, "enable_product_hint": enable_product_hint,
                "enable_structured_vision": enable_structured_vision,
                "yolo_model": yolo_model, "clip_model": clip_model,
                "yolo_conf": effective_yolo_conf, "clip_conf": effective_clip_conf,
                "suggestion_only": effective_suggestion, "save_debug": save_debug,
                "mandatory_chars": mandatory_chars,
                "existing_filled": [k for k, v in existing_chars.items() if v],
            },
        )
        run_logger.inc("total_images")

    log.debug("[Vision] Start offer=%s cat=%r yolo=%s clip=%s url=%s",
              offer_id, category, enable_yolo, enable_clip, image_url[:80])

    # ── Fetch image ───────────────────────────────────────────────────────────
    t_fetch = time.perf_counter()
    img, error = fetch_image(image_url, sku=sku or offer_id)
    fetch_ms   = round((time.perf_counter() - t_fetch) * 1000)

    if run_logger:
        if error:
            run_logger.log("fetch", "failed", offer_id=offer_id, image_url=image_url,
                           status="error", duration_ms=fetch_ms, level="ERROR",
                           data={"error": error})
            run_logger.inc("fetch_fail")
        else:
            run_logger.log("fetch", "ok", offer_id=offer_id, image_url=image_url,
                           status="ok", duration_ms=fetch_ms, level="INFO",
                           data={"img_size": list(img.size) if img else None})
            run_logger.inc("fetch_ok")

    if error or img is None:
        result.download_success = False
        result.download_error   = error or "Unknown fetch error"
        result.skipped_reason   = f"Image download failed: {result.download_error}"
        log.warning("[Vision] Fetch failed offer=%s err=%s", offer_id, error)
        return result

    result.download_success = True
    working_img = img   # this may become the crop after YOLO

    # ── YOLO detection ────────────────────────────────────────────────────────
    if enable_yolo:
        try:
            from core.vision.detection_yolo import detect_objects, crop_to_detection
            from core.vision.detection_yolo import save_crop, save_yolo_overlay

            yolo_res = detect_objects(
                img, model_name=yolo_model, conf_threshold=effective_yolo_conf,
                label_allowlist=effective_yolo_allowlist or None,
                run_logger=run_logger, offer_id=offer_id, image_url=image_url,
            )
            result.yolo_fallback_used = yolo_res.fallback_used

            if yolo_res.best:
                result.detected_object = yolo_res.best.label
                result.yolo_confidence = yolo_res.best.confidence
                result.yolo_bbox       = yolo_res.best.bbox
                # P15: skip crop for low-confidence detections to avoid misleading color analysis
                if yolo_res.best.confidence >= 0.50:
                    working_img      = crop_to_detection(img, yolo_res.best)
                    result.used_crop = True
                    log.debug("[Vision] YOLO crop offer=%s det=%r conf=%.2f bbox=%s",
                              offer_id, yolo_res.best.label, yolo_res.best.confidence,
                              yolo_res.best.bbox)
                else:
                    log.debug("[Vision] YOLO low confidence — skipping crop offer=%s det=%r conf=%.2f",
                              offer_id, yolo_res.best.label, yolo_res.best.confidence)

            # Save debug artifacts
            if save_debug and run_logger and yolo_res.best:
                art_dir = run_logger.artifacts_dir(offer_id)
                save_crop(img, yolo_res.best, art_dir / "crop.jpg")
                save_yolo_overlay(img, yolo_res, art_dir / "yolo_overlay.jpg")
                run_logger.log("yolo", "artifacts_saved", offer_id=offer_id,
                               image_url=image_url, level="DEBUG",
                               data={"dir": str(art_dir)})

        except Exception as e:
            log.error("[Vision] YOLO error offer=%s: %s", offer_id, e, exc_info=True)
            if run_logger:
                run_logger.log("yolo", "exception", offer_id=offer_id,
                               image_url=image_url, status="error", level="ERROR",
                               data={"error": str(e)[:300]})

    # ── Color analysis ────────────────────────────────────────────────────────
    if enable_color:
        try:
            t_color   = time.perf_counter()
            color_res = analyze_colors(working_img)
            color_ms  = round((time.perf_counter() - t_color) * 1000)

            result.dominant_color_raw         = color_res.dominant_color_raw
            result.dominant_color_normalized  = color_res.dominant_color_normalized
            result.secondary_color_raw        = color_res.secondary_color_raw
            result.secondary_color_normalized = color_res.secondary_color_normalized
            result.color_confidence           = color_res.confidence
            result.is_multicolor              = color_res.is_multicolor

            # Determine which color char is missing + mandatory
            missing_char = _missing_color_char(color_chars, mandatory_chars, existing_chars)

            if run_logger:
                run_logger.log(
                    "color", "analysis_done", offer_id=offer_id, image_url=image_url,
                    status="ok", duration_ms=color_ms, level="INFO",
                    data={
                        "method":              "pil_quantize" if not result.used_crop else "pil_quantize_on_crop",
                        "used_crop":           result.used_crop,
                        "dominant_raw":        color_res.dominant_color_raw,
                        "dominant_normalized": color_res.dominant_color_normalized,
                        "secondary":           color_res.secondary_color_normalized,
                        "confidence":          round(color_res.confidence, 4),
                        "is_multicolor":       color_res.is_multicolor,
                        "palette_top3":        color_res.palette_rgb[:3],
                        "missing_char":        missing_char,
                        "min_conf_threshold":  min_color_conf,
                    },
                )
                run_logger.inc("color_ok")

            log.debug("[Vision] Color offer=%s dominant=%r(%s) conf=%.2f multi=%s ms=%d",
                      offer_id, color_res.dominant_color_normalized, color_res.dominant_color_raw,
                      color_res.confidence, color_res.is_multicolor, color_ms)

            if missing_char:
                _apply_color_to_result(
                    result, color_res, missing_char,
                    valid_values_for_cat, min_color_conf, multicolor_threshold,
                    fallback_review, effective_suggestion,
                    run_logger, offer_id, image_url,
                )

        except Exception as e:
            log.error("[Vision] Color error offer=%s: %s", offer_id, e, exc_info=True)
            if run_logger:
                run_logger.log("color", "exception", offer_id=offer_id,
                               image_url=image_url, status="error", level="ERROR",
                               data={"error": str(e)[:300]})

    # ── CLIP scoring ──────────────────────────────────────────────────────────
    if enable_clip:
        try:
            from core.vision.semantic_clip import score_labels

            clip_labels = _build_clip_labels(category, valid_values_for_cat)
            clip_res = score_labels(
                working_img, clip_labels, model_name=clip_model,
                run_logger=run_logger, offer_id=offer_id, image_url=image_url,
                yolo_best_label=result.detected_object,
            )
            result.clip_best_label  = clip_res.best_label
            result.clip_scores      = clip_res.scores
            result.clip_confidence  = clip_res.best_score

            if save_debug and run_logger and clip_res.available:
                art_dir   = run_logger.artifacts_dir(offer_id)
                clip_file = art_dir / "clip_scores.json"
                try:
                    import json as _json
                    clip_file.write_text(
                        _json.dumps(clip_res.scores, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                except Exception:
                    pass

        except Exception as e:
            log.error("[Vision] CLIP error offer=%s: %s", offer_id, e, exc_info=True)
            if run_logger:
                run_logger.log("clip", "exception", offer_id=offer_id,
                               image_url=image_url, status="error", level="ERROR",
                               data={"error": str(e)[:300]})

    # ── Structured cloud vision extraction (feature-flagged, off by default) ──
    if enable_structured_vision and cloud_vision_provider is not None and data is not None and cat_id is not None:
        try:
            from core.vision.vision_attr_extractor import extract_attrs_cloud
            from core.vision.fusion_attrs import fuse_all_attributes

            rules_for_fusion = load_rules()

            # Build eligible_attrs: {char_name: set_of_valid_values} for vision-eligible only
            all_chars_for_cat = data.valid_values_all(cat_id) if hasattr(data, "valid_values_all") else {}
            eligible_attrs = {
                ch: vals
                for ch, vals in all_chars_for_cat.items()
                if is_vision_eligible(ch, rules_for_fusion)
            }

            if eligible_attrs:
                t_vis = time.perf_counter()
                vis_result = extract_attrs_cloud(
                    img=working_img,
                    category=category,
                    marketplace=marketplace,
                    eligible_attrs=eligible_attrs,
                    existing_chars=existing_chars,
                    data=data,
                    cat_id=cat_id,
                    vision_provider=cloud_vision_provider,
                    yolo_label=result.detected_object,
                    clip_label=result.clip_best_label,
                )
                vis_ms = round((time.perf_counter() - t_vis) * 1000)
                result.vision_extracted_attrs = vis_result.extracted_attrs
                if vis_result.error:
                    result.vision_extraction_error = vis_result.error

                if vis_result.success and vis_result.extracted_attrs:
                    # Build vision_attrs signal dict for fusion
                    vision_signals = {
                        ch: (val, 0.80, "vision_llm_cloud")
                        for ch, val in vis_result.extracted_attrs.items()
                    }
                    fusion_results = fuse_all_attributes(
                        text_chars=existing_chars,
                        vision_attrs=vision_signals,
                        rules=rules_for_fusion,
                        data=data,
                        cat_id=cat_id,
                    )
                    for ch, fr in fusion_results.items():
                        if fr.action == "use_vision" and fr.final_value:
                            result.suggested_attributes[ch] = fr.final_value
                            result.used_for_attribute_fill = True
                        elif fr.action == "conflict_review":
                            result.needs_review = True
                            result.review_reason = (
                                (result.review_reason + " | " if result.review_reason else "")
                                + fr.reason
                            )

                if run_logger:
                    run_logger.log(
                        "structured_vision", "extraction_done",
                        offer_id=offer_id, image_url=image_url,
                        status="ok" if vis_result.success else "error",
                        duration_ms=vis_ms, level="INFO" if vis_result.success else "WARNING",
                        data={
                            "provider": vis_result.provider_used,
                            "attrs_requested": vis_result.attrs_requested,
                            "attrs_accepted": vis_result.attrs_accepted,
                            "extracted": vis_result.extracted_attrs,
                            "error": vis_result.error or None,
                        },
                    )

        except Exception as e:
            log.error("[Vision] Structured extraction error offer=%s: %s", offer_id, e, exc_info=True)
            result.vision_extraction_error = str(e)[:200]
            if run_logger:
                run_logger.log("structured_vision", "exception", offer_id=offer_id,
                               image_url=image_url, status="error", level="ERROR",
                               data={"error": str(e)[:300]})

    # ── Ollama vision product hint ────────────────────────────────────────────
    if enable_product_hint and vision_provider is not None:
        if not vision_provider.is_available():
            log.warning("[Vision] Vision provider indisponibil offer=%s", offer_id)
            if run_logger:
                run_logger.log("product_hint", "provider_unavailable", offer_id=offer_id,
                               image_url=image_url, status="skip", level="WARNING")
        else:
            try:
                t_hint  = time.perf_counter()
                prompt  = (
                    f"You are analyzing a product image for marketplace listing.\n"
                    f"Category from text: '{category}'.\n"
                    f"Identify the product type in 3-5 words. Reply ONLY with the product type."
                )
                hint    = vision_provider.analyze(working_img, prompt)
                hint_ms = round((time.perf_counter() - t_hint) * 1000)

                if hint and len(hint.strip()) > 2:
                    result.product_type_hint         = hint.strip()[:120]
                    result.product_type_confidence   = min_prod_conf
                    result.used_for_category_support = True
                    log.info("[Vision] Product hint offer=%s: %r ms=%d", offer_id, hint[:80], hint_ms)
                    if run_logger:
                        run_logger.log("product_hint", "result", offer_id=offer_id,
                                       image_url=image_url, status="ok", duration_ms=hint_ms,
                                       level="INFO",
                                       data={"hint": hint.strip()[:200],
                                             "confidence": min_prod_conf,
                                             "current_category": category})
                else:
                    if run_logger:
                        run_logger.log("product_hint", "empty_response", offer_id=offer_id,
                                       image_url=image_url, status="skip", duration_ms=hint_ms,
                                       level="WARNING", data={"raw": (hint or "")[:100]})
            except Exception as e:
                log.warning("[Vision] Product hint error offer=%s: %s", offer_id, e)

    elif enable_product_hint and vision_provider is None:
        log.warning("[Vision] enable_product_hint=True but vision_provider=None offer=%s", offer_id)

    # ── Characteristics fill log ──────────────────────────────────────────────
    if run_logger:
        all_chars = {**existing_chars, **result.suggested_attributes}
        mandatory_missing = [c for c in mandatory_chars if not all_chars.get(c)]
        run_logger.log(
            "fill", "characteristics_summary", offer_id=offer_id, image_url=image_url,
            status="ok", level="INFO",
            data={
                "existing_filled": [k for k, v in existing_chars.items() if v],
                "mandatory_chars": mandatory_chars,
                "mandatory_missing_before": [c for c in mandatory_chars if not existing_chars.get(c)],
                "filled_from_image": list(result.suggested_attributes.keys()),
                "still_missing_mandatory": mandatory_missing,
                "needs_review": result.needs_review,
                "review_reason": result.review_reason,
            },
        )
        if result.suggested_attributes:
            run_logger.inc("fills")
        if result.needs_review:
            run_logger.inc("needs_review")

    # ── Summary ───────────────────────────────────────────────────────────────
    total_ms = round((time.perf_counter() - t_global) * 1000)
    log.info(
        "[Vision] Done offer=%s ms=%d | filled=%s review=%s skipped=%r | "
        "color=%r conf=%.2f yolo=%r clip=%r",
        offer_id, total_ms,
        list(result.suggested_attributes.keys()) or "none",
        result.needs_review, result.skipped_reason or None,
        result.dominant_color_normalized, result.color_confidence,
        result.detected_object or None, result.clip_best_label or None,
    )
    if run_logger:
        run_logger.log("pipeline", "done", offer_id=offer_id, image_url=image_url,
                       status="ok", duration_ms=total_ms, level="INFO",
                       data={
                           "suggested_attributes":  result.suggested_attributes,
                           "needs_review":          result.needs_review,
                           "skipped_reason":        result.skipped_reason or None,
                       })

    return result


# ── Color application helper (keeps main function readable) ───────────────────

def _apply_color_to_result(
    result: ImageAnalysisResult,
    color_res: ColorResult,
    missing_char: str,
    valid_values_for_cat: dict,
    min_color_conf: float,
    multicolor_threshold: float,
    fallback_review: bool,
    suggestion_only: bool,
    run_logger, offer_id: str, image_url: str,
) -> None:
    valid_set = valid_values_for_cat.get(missing_char, set())

    # Case 1: Multicolor
    if color_res.is_multicolor and color_res.confidence >= multicolor_threshold:
        mc_val = find_multicolor_value(valid_set) if valid_set else "Multicolor"
        if mc_val:
            if not suggestion_only:
                result.suggested_attributes[missing_char] = mc_val
                result.used_for_attribute_fill = True
            log.info("[Vision] MULTICOLOR [%s]=%r conf=%.2f offer=%s",
                     missing_char, mc_val, color_res.confidence, offer_id)
            if run_logger:
                run_logger.log("fill", "color_filled", offer_id=offer_id, image_url=image_url,
                               level="INFO",
                               data={"char": missing_char, "value": mc_val,
                                     "source": "multicolor", "confidence": color_res.confidence,
                                     "suggestion_only": suggestion_only,
                                     "decision": "multicolor detected with confidence >= threshold"})
        else:
            result.needs_review  = True
            result.review_reason = "Multicolor detected but no multicolor value in marketplace list"
            if run_logger:
                run_logger.log("fill", "color_review", offer_id=offer_id, image_url=image_url,
                               status="review", level="WARNING",
                               data={"char": missing_char, "reason": result.review_reason,
                                     "valid_sample": list(valid_set)[:5]})
        return

    # Case 2: Single color with sufficient confidence
    if color_res.dominant_color_normalized and color_res.confidence >= min_color_conf:
        normalized = color_res.dominant_color_normalized
        mapped     = None
        map_reason = ""

        if valid_set:
            mapped = _map_to_valid(normalized, valid_set)
            if mapped:
                map_reason = "exact/case-insensitive match"
            else:
                # Family scoring fallback
                m = re.match(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)", color_res.dominant_color_raw)
                if m:
                    fam    = _rgb_to_family((int(m.group(1)), int(m.group(2)), int(m.group(3))))
                    mapped = pick_best_accepted_color(fam, valid_set) or None
                    if mapped:
                        map_reason = f"family scoring (family={fam})"

            if run_logger:
                run_logger.log("fill", "color_mapping", offer_id=offer_id, image_url=image_url,
                               level="DEBUG",
                               data={"normalized": normalized, "mapped": mapped,
                                     "map_reason": map_reason or "no match",
                                     "raw": color_res.dominant_color_raw,
                                     "valid_count": len(valid_set),
                                     "valid_sample": list(valid_set)[:8]})

            if mapped:
                if not suggestion_only:
                    result.suggested_attributes[missing_char] = mapped
                    result.used_for_attribute_fill = True
                log.info("[Vision] CULOARE [%s]=%r (det=%r conf=%.2f) offer=%s",
                         missing_char, mapped, normalized, color_res.confidence, offer_id)
                if run_logger:
                    run_logger.log("fill", "color_filled", offer_id=offer_id, image_url=image_url,
                                   level="INFO",
                                   data={"char": missing_char, "value": mapped,
                                         "detected": normalized, "confidence": color_res.confidence,
                                         "map_reason": map_reason, "suggestion_only": suggestion_only})
            else:
                if fallback_review:
                    result.needs_review  = True
                    result.review_reason = (
                        f"Color '{normalized}' detected but not in marketplace "
                        f"valid values ({len(valid_set)} values)"
                    )
                log.warning("[Vision] Culoare %r nu e in lista [%s] offer=%s", normalized, missing_char, offer_id)
                if run_logger:
                    run_logger.log("fill", "color_review", offer_id=offer_id, image_url=image_url,
                                   status="review", level="WARNING",
                                   data={"char": missing_char, "detected": normalized,
                                         "valid_count": len(valid_set), "reason": "no marketplace mapping found"})
        else:
            # Freeform field
            if not suggestion_only:
                result.suggested_attributes[missing_char] = normalized
                result.used_for_attribute_fill = True
            if run_logger:
                run_logger.log("fill", "color_filled", offer_id=offer_id, image_url=image_url,
                               level="INFO",
                               data={"char": missing_char, "value": normalized,
                                     "source": "freeform", "suggestion_only": suggestion_only})
        return

    # Case 3: Low confidence
    if color_res.dominant_color_normalized:
        result.needs_review  = True
        result.review_reason = (
            f"Color detected ({color_res.dominant_color_normalized}) "
            f"but confidence too low ({color_res.confidence:.2f} < {min_color_conf})"
        )
        log.warning("[Vision] Confidence prea mica color=%r conf=%.2f < %.2f offer=%s",
                    color_res.dominant_color_normalized, color_res.confidence, min_color_conf, offer_id)
        if run_logger:
            run_logger.log("fill", "color_low_confidence", offer_id=offer_id, image_url=image_url,
                           status="review", level="WARNING",
                           data={"char": missing_char, "detected": color_res.dominant_color_normalized,
                                 "confidence": color_res.confidence, "threshold": min_color_conf})
    else:
        log.warning("[Vision] No color detected offer=%s", offer_id)
        if run_logger:
            run_logger.log("fill", "color_not_detected", offer_id=offer_id,
                           image_url=image_url, status="skip", level="WARNING")
