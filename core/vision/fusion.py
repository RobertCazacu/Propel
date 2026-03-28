"""
Text + Image fusion rule engine for category and attribute decisions.

Takes text-based and image-based signals and combines them according to
configurable rules, producing a single final decision with full reasoning.

Public API:
    fuse_category(text, image, rules, ...) -> FusionResult
    action_to_confidence(action) -> float   # helper for process.py
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


# ── Input / output types ──────────────────────────────────────────────────────

@dataclass
class TextCategoryResult:
    candidate: str   = ""    # resolved category name or ""
    confidence: float = 0.0  # 0..1
    source: str      = ""    # "rules" | "ai" | "unknown" | "ok" | "assigned"


@dataclass
class ImageCategoryResult:
    candidate: str    = ""
    confidence: float = 0.0
    source: str       = ""   # "yolo" | "clip" | "yolo+clip"


@dataclass
class FusionResult:
    final_category:   str   = ""
    final_confidence: float = 0.0
    rule_applied:     str   = ""   # "prefer_text"|"prefer_image"|"review"|"text_only"
    reason:           str   = ""   # human-readable Romanian explanation
    needs_review:     bool  = False

    # Echo inputs (for logging)
    text_candidate:   str   = ""
    text_confidence:  float = 0.0
    image_candidate:  str   = ""
    image_confidence: float = 0.0


# ── Confidence thresholds ─────────────────────────────────────────────────────

_HIGH  = 0.75
_LOW   = 0.30


# ── Main fusion function ──────────────────────────────────────────────────────

def fuse_category(
    text: TextCategoryResult,
    image: Optional[ImageCategoryResult],
    rules: dict,
    run_logger=None,   # VisionRunLogger | None
    offer_id: str = "",
) -> FusionResult:
    """
    Combine text and image category signals into a single decision.

    Rules (from visual_rules.json / cat_rules):
        prefer_text_over_image : bool    (default True)
        conflict_policy        : str     "review" | "prefer_text" | "prefer_image"
        min_clip_confidence    : float   minimum image confidence to trust (default 0.25)
        min_text_conf_for_image: float   text confidence below which image is checked (0.70)
    """
    prefer_text      = rules.get("prefer_text_over_image", True)
    conflict_policy  = rules.get("conflict_policy", "review")
    min_img_conf     = rules.get("min_clip_confidence", 0.25)

    res = FusionResult(
        text_candidate=text.candidate,
        text_confidence=text.confidence,
        image_candidate=image.candidate if image else "",
        image_confidence=image.confidence if image else 0.0,
    )

    # ── No image result ───────────────────────────────────────────────────────
    if not image or not image.candidate:
        res.final_category   = text.candidate
        res.final_confidence = text.confidence
        res.rule_applied     = "text_only"
        res.reason = (
            f"Imagine indisponibilă sau fără rezultat. "
            f"Categorie din text: '{text.candidate}' (conf={text.confidence:.2f})"
        )
        _log(res, run_logger, offer_id)
        return res

    text_ok = bool(text.candidate) and text.candidate not in ("", "unknown")
    img_ok  = bool(image.candidate) and image.confidence >= min_img_conf
    same    = (
        text_ok and img_ok and
        text.candidate.strip().lower() == image.candidate.strip().lower()
    )

    # ── Case 1: Text high confidence → keep text ──────────────────────────────
    if text_ok and text.confidence >= _HIGH:
        bonus = 0.05 if same else 0.0
        res.final_category   = text.candidate
        res.final_confidence = min(text.confidence + bonus, 1.0)
        res.rule_applied     = "prefer_text"
        if same:
            res.reason = (
                f"Text cu confidență mare ({text.confidence:.2f}) confirmat de imagine "
                f"('{image.candidate}', conf={image.confidence:.2f})."
            )
        elif img_ok:
            res.reason = (
                f"Text cu confidență mare ({text.confidence:.2f}). "
                f"Imaginea sugerează '{image.candidate}' ({image.confidence:.2f}) — ignorat (prefer_text)."
            )
        else:
            res.reason = f"Text cu confidență mare ({text.confidence:.2f}). Imagine slabă/absentă."

        # Conflict alert: both signals strong but disagree
        if img_ok and not same and image.confidence >= _HIGH:
            res.needs_review = True
            res.reason += " ⚠️ Conflict: ambele semnale puternice dar categorii diferite."

        _log(res, run_logger, offer_id)
        return res

    # ── Case 2: Text weak/absent + image strong → use image ──────────────────
    if (not text_ok or text.confidence < _LOW) and img_ok and image.confidence >= _HIGH:
        res.final_category   = image.candidate
        res.final_confidence = image.confidence
        res.rule_applied     = "prefer_image"
        res.reason = (
            f"Text neclar/absent (conf={text.confidence:.2f}). "
            f"Imaginea sugerează '{image.candidate}' cu confidență mare ({image.confidence:.2f})."
        )
        _log(res, run_logger, offer_id)
        return res

    # ── Case 3: Both agree → confirm ─────────────────────────────────────────
    if text_ok and img_ok and same:
        avg = (text.confidence + image.confidence) / 2
        res.final_category   = text.candidate
        res.final_confidence = min(avg + 0.10, 1.0)
        res.rule_applied     = "prefer_text"
        res.reason = (
            f"Text și imagine confirmă aceeași categorie '{text.candidate}'. "
            f"(text={text.confidence:.2f}, img={image.confidence:.2f})"
        )
        _log(res, run_logger, offer_id)
        return res

    # ── Case 4: Conflict — apply policy ──────────────────────────────────────
    if conflict_policy == "prefer_text" and text_ok:
        res.final_category   = text.candidate
        res.final_confidence = text.confidence
        res.rule_applied     = "prefer_text"
        res.reason = (
            f"Conflict: text='{text.candidate}'({text.confidence:.2f}) vs "
            f"imagine='{image.candidate}'({image.confidence:.2f}). Policy: prefer_text."
        )
    elif conflict_policy == "prefer_image" and img_ok:
        res.final_category   = image.candidate
        res.final_confidence = image.confidence
        res.rule_applied     = "prefer_image"
        res.reason = (
            f"Conflict: text='{text.candidate}'({text.confidence:.2f}) vs "
            f"imagine='{image.candidate}'({image.confidence:.2f}). Policy: prefer_image."
        )
    else:
        # Default: review — pick higher confidence signal but flag
        if text_ok and text.confidence >= (image.confidence if img_ok else 0):
            best = text.candidate
            best_conf = text.confidence
        elif img_ok:
            best = image.candidate
            best_conf = image.confidence
        else:
            best = text.candidate
            best_conf = text.confidence
        res.final_category   = best
        res.final_confidence = best_conf * 0.80  # discounted due to conflict
        res.rule_applied     = "review"
        res.needs_review     = True
        res.reason = (
            f"Conflict nerezolvat: text='{text.candidate}'({text.confidence:.2f}) vs "
            f"imagine='{image.candidate}'({image.confidence:.2f}). "
            f"Marcat pentru review manual. Confidență redusă la {res.final_confidence:.2f}."
        )

    _log(res, run_logger, offer_id)
    return res


# ── Helpers ───────────────────────────────────────────────────────────────────

def _log(result: FusionResult, run_logger, offer_id: str) -> None:
    if not run_logger:
        return
    level = "WARNING" if result.needs_review else "INFO"
    run_logger.log(
        stage="fusion", event="category_decision",
        offer_id=offer_id, status="review" if result.needs_review else "ok",
        level=level,
        data={
            "text_candidate":    result.text_candidate,
            "text_confidence":   round(result.text_confidence, 4),
            "image_candidate":   result.image_candidate,
            "image_confidence":  round(result.image_confidence, 4),
            "rule_applied":      result.rule_applied,
            "final_category":    result.final_category,
            "final_confidence":  round(result.final_confidence, 4),
            "needs_review":      result.needs_review,
            "reason":            result.reason,
        },
    )


def action_to_confidence(action: str) -> float:
    """
    Convert a category resolution action string to a numeric confidence.
    Used by process.py to feed the fusion engine.
    """
    return {
        "ok":           0.95,
        "assigned":     0.75,
        "cat_assigned": 0.70,
        "ai_assigned":  0.70,
        "unknown":      0.00,
    }.get(action, 0.0)
