"""
Cloud vision structured attribute extractor.

Sends image to a cloud vision provider (OpenAI gpt-4o-mini or Anthropic claude-haiku)
and returns attributes validated against the marketplace's allowed values list.

DESIGN DECISIONS:
- Ollama is NOT used here (llava-phi3 is not reliable for structured JSON with policy tables).
- Model-reported confidence is NOT requested or used. Primary gate = data.find_valid().
- Fallback is always {} (never parse free text from vision model output).
- Feature flag: enable_structured_vision=False by default in image_analyzer.py.
"""
from __future__ import annotations
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from PIL import Image
from core.app_logger import get_logger

log = get_logger("marketplace.vision.extractor")

_VISION_SYSTEM = (
    "You are a visual product attribute extractor for e-commerce catalogs.\n"
    "Analyze the product image and identify ONLY clearly visible attributes.\n\n"
    "You can detect: color, print/pattern, sleeve length, shoe profile (low/mid/high-top), "
    "product type (t-shirt/hoodie/jacket/pants/shoes), closure type (laces/velcro/zipper/slip-on), "
    "gender target (if clearly visible on image).\n\n"
    "You DO NOT guess: material, season, size, age, or sport type.\n\n"
    "Return ONLY valid JSON: {\"Attribute Name\": \"exact value from list\"}\n"
    "If unsure about any attribute → OMIT it.\n"
    "Zero text outside JSON. No markdown. No confidence scores."
)


@dataclass
class VisionExtractionResult:
    extracted_attrs: dict    = field(default_factory=dict)
    raw_response: str        = ""
    provider_used: str       = ""
    latency_ms: int          = 0
    success: bool            = False
    error: str               = ""
    attrs_requested: int     = 0
    attrs_accepted: int      = 0


def _build_vision_extraction_prompt(
    category: str,
    marketplace: str,
    eligible_attrs: dict,        # {char_name: set_of_allowed_values}
    existing_chars: dict,        # already filled — exclude from prompt
    yolo_label: str = "",
    clip_label: str = "",
) -> str:
    """
    Build structured extraction prompt for cloud vision provider.

    NEVER asks for confidence scores (model-generated numbers are unreliable).
    NEVER includes already-filled attributes.
    NEVER includes non-visual attributes (Material, Season, Size etc).
    """
    to_extract = {
        ch: vals for ch, vals in eligible_attrs.items()
        if not existing_chars.get(ch)
    }
    if not to_extract:
        return ""

    ctx_parts = [f"Category: {category}", f"Marketplace: {marketplace}"]
    if yolo_label:
        ctx_parts.append(f"YOLO detected: {yolo_label}")
    if clip_label:
        ctx_parts.append(f"CLIP label: {clip_label}")

    attr_lines = []
    for ch_name, vals in to_extract.items():
        if vals:
            sample = json.dumps(sorted(vals)[:15], ensure_ascii=False)
            attr_lines.append(f'  "{ch_name}": choose from {sample}')
        else:
            attr_lines.append(f'  "{ch_name}": (free text — describe briefly)')

    return (
        " | ".join(ctx_parts) + "\n\n"
        "Extract ONLY these attributes from the image (visually determinable only):\n"
        + "\n".join(attr_lines) + "\n\n"
        "Return JSON only: {\"attr_name\": \"exact_value\", ...}\n"
        "Omit any attribute you cannot clearly see."
    )


def _parse_vision_response(
    raw: str,
    eligible_char_names: set,
    data,
    cat_id,
) -> dict:
    """
    Parse vision response and validate against allowed values list.

    IMPORTANT: If JSON cannot be parsed → return {} ALWAYS.
    Never attempt to extract information from free text output.
    Primary gate: data.find_valid() — the only reliable filter.
    """
    if not raw or not raw.strip():
        return {}

    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{[^{}]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group())
            except Exception:
                pass

    if not isinstance(parsed, dict):
        log.warning("vision_extractor: non-JSON response ignored (len=%d)", len(raw))
        return {}

    result = {}
    for ch_name, ch_val in parsed.items():
        if ch_name not in eligible_char_names:
            log.debug("vision_extractor: unrequested attr '%s' ignored (hallucination)", ch_name)
            continue

        val_str = str(ch_val).strip()
        if not val_str:
            continue

        mapped = data.find_valid(val_str, cat_id, ch_name)
        if mapped is not None:
            result[ch_name] = mapped
            log.debug("vision_extractor: [%s]=%r validated → %r", ch_name, ch_val, mapped)
        else:
            valid_set = data.valid_values(cat_id, ch_name)
            if not valid_set and not data.is_restrictive(cat_id, ch_name):
                result[ch_name] = val_str
                log.debug("vision_extractor: [%s]=%r accepted freeform", ch_name, ch_val)
            else:
                log.warning(
                    "vision_extractor: [%s]=%r REJECTED — not in allowed list",
                    ch_name, ch_val,
                )

    return result


def extract_attrs_cloud(
    img: Image.Image,
    category: str,
    marketplace: str,
    eligible_attrs: dict,
    existing_chars: dict,
    data,
    cat_id,
    vision_provider,
    yolo_label: str = "",
    clip_label: str = "",
    timeout_s: int = 15,
) -> VisionExtractionResult:
    """
    Extract structured attributes from image via cloud vision provider.

    Call is gated by feature flag in image_analyzer.py.
    Does NOT use Ollama (llava-phi3 is not reliable for structured JSON).
    """
    result = VisionExtractionResult()

    if not eligible_attrs:
        result.error = "No eligible attributes"
        return result

    prompt = _build_vision_extraction_prompt(
        category, marketplace, eligible_attrs, existing_chars, yolo_label, clip_label,
    )
    if not prompt:
        result.error = "All eligible attrs already filled"
        return result

    result.attrs_requested = len([
        ch for ch in eligible_attrs if not existing_chars.get(ch)
    ])

    t_start = time.perf_counter()
    try:
        if not vision_provider.is_available():
            result.error = "Vision provider unavailable"
            return result

        raw = vision_provider.analyze(img, prompt)
        result.raw_response = raw
        result.latency_ms = round((time.perf_counter() - t_start) * 1000)
        result.provider_used = getattr(vision_provider, "name", "unknown")

        eligible_names = set(eligible_attrs.keys())
        extracted = _parse_vision_response(raw, eligible_names, data, cat_id)
        result.extracted_attrs = extracted
        result.attrs_accepted = len(extracted)
        result.success = True

        log.info(
            "vision_extractor: %d/%d attrs extracted (provider=%s latency=%dms)",
            result.attrs_accepted, result.attrs_requested,
            result.provider_used, result.latency_ms,
        )

    except Exception as exc:
        result.latency_ms = round((time.perf_counter() - t_start) * 1000)
        result.error = str(exc)[:200]
        result.success = False
        log.error("vision_extractor: error %s", exc, exc_info=True)

    return result
