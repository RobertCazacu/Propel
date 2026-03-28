"""
Per-attribute text + vision fusion engine.

Equivalent to fusion.py (for category) but applied to each attribute individually.
Primary gate = data.find_valid() (validation against allowed values list).
Model-reported confidence is NOT used as a gate (generated numbers are not calibrated).
The min_vision_confidence in policy is an EXTERNAL soft pre-filter, not from the model.

Public API:
    fuse_attribute(char_name, text_signal, vision_signal, policy, data, cat_id) -> AttrFusionResult
    fuse_all_attributes(text_chars, vision_attrs, rules, data, cat_id) -> dict[str, AttrFusionResult]
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
from core.app_logger import get_logger

log = get_logger("marketplace.vision.fusion_attrs")

# Type alias
Signal = Optional[Tuple[str, float, str]]  # (value, confidence, source)


@dataclass
class AttrFusionResult:
    char_name:        str
    final_value:      Optional[str]
    final_confidence: float
    source:           str    # "text_rule"|"text_ai"|"color_algorithm"|"vision_llm_cloud"|"review"|"skip"
    action:           str    # "keep_text"|"use_vision"|"conflict_review"|"skip"
    reason:           str
    needs_review:     bool   = False


def fuse_attribute(
    char_name: str,
    text_signal: Signal,
    vision_signal: Signal,
    policy: dict,
    data,
    cat_id,
) -> AttrFusionResult:
    """
    Decide the final value for an attribute based on text + vision signals.

    Rules (in order):
    1. If vision_eligible=False -> keep_text (or skip if text absent)
    2. If text is filled and override_text_if_filled=False -> keep_text
       (with conflict_review if vision contradicts and is above threshold)
    3. If text absent and vision validated -> use_vision
    4. If vision below threshold or invalid -> skip
    """
    text_val, text_conf, text_src = text_signal if text_signal else (None, 0.0, "")
    vis_val, vis_conf, vis_src = vision_signal if vision_signal else (None, 0.0, "")

    # -- Case 0: Vision ineligible for this attribute --------------------------
    if not policy.get("vision_eligible", False):
        if text_val:
            return AttrFusionResult(
                char_name, text_val, text_conf, text_src,
                "keep_text",
                f"Vision ineligibila pentru '{char_name}' (policy).",
            )
        return AttrFusionResult(
            char_name, None, 0.0, "", "skip",
            f"Vision ineligibila si text absent pentru '{char_name}'.",
        )

    min_vis_conf = policy.get("min_vision_confidence", 0.65)
    override_if_filled = policy.get("override_text_if_filled", False)
    conflict_action = policy.get("conflict_action", "prefer_text")

    # -- Validate vision against allowed values list (PRIMARY GATE) -----------
    # This is the only reliable gate -- NOT model-reported confidence.
    vis_validated = None
    if vis_val:
        vis_validated = data.find_valid(vis_val, cat_id, char_name)
        if vis_validated is None:
            valid_set = data.valid_values(cat_id, char_name)
            if not valid_set and not data.is_restrictive(cat_id, char_name):
                vis_validated = vis_val  # freeform field -- accept directly
            else:
                log.debug(
                    "fusion_attrs: vision value %r invalid for [%s] -- rejected",
                    vis_val, char_name,
                )

    # -- Case 1: Text filled + override_if_filled=False (conservative) --------
    if text_val and not override_if_filled:
        if (vis_validated and vis_validated.lower() != text_val.lower()
                and vis_conf >= min_vis_conf):
            if conflict_action == "review":
                return AttrFusionResult(
                    char_name, text_val, text_conf * 0.9, text_src,
                    "conflict_review",
                    f"Conflict: text='{text_val}'({text_conf:.2f}) vs vision='{vis_validated}'({vis_conf:.2f}). "
                    f"Text ales conservator, marcat review.",
                    needs_review=True,
                )
        confirmed = (vis_validated is not None
                     and vis_validated.lower() == text_val.lower())
        reason = (
            f"Text ales (conf={text_conf:.2f}), confirmat vizual."
            if confirmed else
            f"Text ales (conf={text_conf:.2f}), vision absent/ignorat."
        )
        return AttrFusionResult(
            char_name, text_val,
            min(text_conf + (0.05 if confirmed else 0.0), 1.0),
            text_src, "keep_text", reason,
        )

    # -- Case 2: Text absent -> vision can fill --------------------------------
    if not text_val:
        if vis_validated and vis_conf >= min_vis_conf:
            return AttrFusionResult(
                char_name, vis_validated, vis_conf, vis_src,
                "use_vision",
                f"Text absent. Vision: '{vis_validated}' (conf={vis_conf:.2f}) acceptat.",
            )
        if vis_validated and vis_conf < min_vis_conf:
            log.debug(
                "fusion_attrs: vision conf too low %.2f < %.2f for [%s]",
                vis_conf, min_vis_conf, char_name,
            )
        return AttrFusionResult(
            char_name, None, 0.0, "", "skip",
            f"Text absent. Vision absent sau sub prag ({vis_conf:.2f} < {min_vis_conf}).",
        )

    # -- Case 3: Fallthrough ---------------------------------------------------
    return AttrFusionResult(char_name, None, 0.0, "", "skip", "No valid signal.")


def fuse_all_attributes(
    text_chars: dict,
    vision_attrs: dict,        # {char_name: (value, confidence, source)}
    rules: dict,
    data,
    cat_id,
) -> dict[str, AttrFusionResult]:
    """
    Apply fuse_attribute for each attribute where vision has a signal.

    Returns {char_name: AttrFusionResult} ONLY for attributes where
    vision contributed (use_vision, conflict_review).
    Attributes where text won without contest are NOT included.
    """
    policy_table = rules.get("default", {}).get("attribute_fusion_policy", {})
    results: dict[str, AttrFusionResult] = {}

    for char_name, vis_signal in vision_attrs.items():
        policy = policy_table.get(char_name, {"vision_eligible": False})
        text_val = text_chars.get(char_name)
        text_signal = (text_val, 0.95, "text") if text_val else None

        result = fuse_attribute(char_name, text_signal, vis_signal, policy, data, cat_id)

        if result.action in ("use_vision", "conflict_review"):
            results[char_name] = result
            log.debug(
                "fuse_all: [%s] action=%s value=%r needs_review=%s",
                char_name, result.action, result.final_value, result.needs_review,
            )

    return results
