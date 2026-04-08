"""
CLIP semantic scoring — lazy-loaded, fully fallback-safe.

Prefers open_clip_torch, falls back to transformers CLIP, then returns
an empty result if neither is installed.

Public API:
    score_labels(img, labels, model_name, ...) -> ClipResult
    is_available() -> bool
"""
from __future__ import annotations
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger("marketplace.clip")

_open_clip_ok: Optional[bool]       = None
_transformers_ok: Optional[bool]    = None
_open_clip_cache: dict              = {}
_transformers_cache: dict           = {}


# ── Availability checks ───────────────────────────────────────────────────────

def _check_open_clip() -> bool:
    global _open_clip_ok
    if _open_clip_ok is None:
        try:
            import open_clip  # noqa
            _open_clip_ok = True
        except ImportError:
            _open_clip_ok = False
    return _open_clip_ok


def _check_transformers() -> bool:
    global _transformers_ok
    if _transformers_ok is None:
        try:
            from transformers import CLIPProcessor, CLIPModel  # noqa
            _transformers_ok = True
        except ImportError:
            _transformers_ok = False
    return _transformers_ok


def is_available() -> bool:
    return _check_open_clip() or _check_transformers()


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ClipResult:
    available: bool      = False
    backend: str         = ""     # "open_clip" | "transformers" | ""
    model_name: str      = ""
    labels: list         = field(default_factory=list)
    scores: dict         = field(default_factory=dict)   # {label: float 0..1}
    best_label: str      = ""
    best_score: float    = 0.0
    validates_yolo: Optional[bool] = None  # True/False/None
    duration_ms: int     = 0
    error: str           = ""


# ── Public entry point ────────────────────────────────────────────────────────

def score_labels(
    img,                           # PIL.Image.Image
    labels: list[str],
    model_name: str = "ViT-B-32",
    run_logger=None,               # VisionRunLogger | None
    offer_id: str = "",
    image_url: str = "",
    yolo_best_label: str = "",     # if set: compute validates_yolo flag
) -> ClipResult:
    """
    Score a list of text labels against an image using CLIP.
    Scores are softmax-normalized probabilities (sum to 1.0).
    Falls back gracefully if no CLIP backend is installed.
    """
    if not labels:
        return ClipResult(available=False, error="no labels provided")

    if _check_open_clip():
        return _score_open_clip(img, labels, model_name,
                                run_logger, offer_id, image_url, yolo_best_label)
    if _check_transformers():
        return _score_transformers(img, labels, model_name,
                                   run_logger, offer_id, image_url, yolo_best_label)

    if run_logger:
        run_logger.log(
            stage="clip", event="unavailable",
            offer_id=offer_id, image_url=image_url,
            status="skip", level="WARNING",
            data={"reason": "open_clip_torch and transformers[vision] not installed"},
        )
        run_logger.inc("clip_fallback")
    return ClipResult(available=False, error="no CLIP backend installed")


# ── open_clip backend ─────────────────────────────────────────────────────────

def _score_open_clip(img, labels, model_name, run_logger, offer_id, image_url, yolo_label) -> ClipResult:
    t0 = time.perf_counter()
    try:
        import open_clip
        import torch

        if run_logger:
            run_logger.log(
                stage="clip", event="inference_start",
                offer_id=offer_id, image_url=image_url, status="ok", level="DEBUG",
                data={"backend": "open_clip", "model": model_name, "n_labels": len(labels)},
            )

        model, _, preprocess = _get_open_clip_model(model_name)
        tokenizer   = open_clip.get_tokenizer(model_name)   # P03: tokenizer matches model
        img_tensor  = preprocess(img).unsqueeze(0)
        text_tokens = tokenizer(labels)

        with torch.no_grad():
            img_feat  = model.encode_image(img_tensor)
            txt_feat  = model.encode_text(text_tokens)
            img_feat  = img_feat / img_feat.norm(dim=-1, keepdim=True)
            txt_feat  = txt_feat / txt_feat.norm(dim=-1, keepdim=True)
            probs     = (100.0 * img_feat @ txt_feat.T).softmax(dim=-1)[0]

        scores     = {lbl: float(probs[i]) for i, lbl in enumerate(labels)}
        best_label = max(scores, key=scores.get)
        best_score = scores[best_label]
        validates  = _check_validates_yolo(yolo_label, best_label, scores)

        result = ClipResult(
            available=True, backend="open_clip", model_name=model_name,
            labels=labels, scores=scores, best_label=best_label,
            best_score=round(best_score, 4), validates_yolo=validates,
            duration_ms=round((time.perf_counter() - t0) * 1000),
        )
        _log_clip_result(result, run_logger, offer_id, image_url)
        return result

    except Exception as exc:
        duration_ms = round((time.perf_counter() - t0) * 1000)
        if run_logger:
            run_logger.log(
                stage="clip", event="error",
                offer_id=offer_id, image_url=image_url,
                status="error", duration_ms=duration_ms, level="ERROR",
                data={"error": str(exc)[:300]},
            )
            run_logger.inc("clip_fallback")
        return ClipResult(available=False, error=str(exc)[:200], duration_ms=duration_ms)


# ── transformers backend ──────────────────────────────────────────────────────

def _score_transformers(img, labels, model_name, run_logger, offer_id, image_url, yolo_label) -> ClipResult:
    t0 = time.perf_counter()
    try:
        from transformers import CLIPProcessor, CLIPModel
        import torch

        if run_logger:
            run_logger.log(
                stage="clip", event="inference_start",
                offer_id=offer_id, image_url=image_url, status="ok", level="DEBUG",
                data={"backend": "transformers", "model": model_name, "n_labels": len(labels)},
            )

        model, processor = _get_transformers_model(model_name)
        inputs = processor(text=labels, images=img, return_tensors="pt", padding=True)

        with torch.no_grad():
            outputs = model(**inputs)
            probs   = outputs.logits_per_image.softmax(dim=1)[0]

        scores     = {lbl: float(probs[i]) for i, lbl in enumerate(labels)}
        best_label = max(scores, key=scores.get)
        best_score = scores[best_label]
        validates  = _check_validates_yolo(yolo_label, best_label, scores)

        result = ClipResult(
            available=True, backend="transformers", model_name=model_name,
            labels=labels, scores=scores, best_label=best_label,
            best_score=round(best_score, 4), validates_yolo=validates,
            duration_ms=round((time.perf_counter() - t0) * 1000),
        )
        _log_clip_result(result, run_logger, offer_id, image_url)
        return result

    except Exception as exc:
        duration_ms = round((time.perf_counter() - t0) * 1000)
        if run_logger:
            run_logger.log(
                stage="clip", event="error",
                offer_id=offer_id, image_url=image_url,
                status="error", duration_ms=duration_ms, level="ERROR",
                data={"error": str(exc)[:300]},
            )
            run_logger.inc("clip_fallback")
        return ClipResult(available=False, error=str(exc)[:200], duration_ms=duration_ms)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _check_validates_yolo(yolo_label: str, best_clip_label: str, scores: dict) -> Optional[bool]:
    """True if CLIP top result is consistent with YOLO best detection label."""
    if not yolo_label:
        return None
    yn = yolo_label.lower()
    # "validates" if YOLO label appears in the best CLIP label (or vice versa)
    # or if YOLO label scores among top-2 CLIP results
    if yn in best_clip_label.lower() or best_clip_label.lower() in yn:
        return True
    sorted_scores = sorted(scores.items(), key=lambda x: -x[1])
    top2 = [lbl.lower() for lbl, _ in sorted_scores[:2]]
    return any(yn in lbl or lbl in yn for lbl in top2)


def _log_clip_result(result: ClipResult, run_logger, offer_id: str, image_url: str) -> None:
    if not run_logger:
        return
    level = "INFO" if result.available else "WARNING"
    sorted_scores = sorted(result.scores.items(), key=lambda x: -x[1])
    run_logger.log(
        stage="clip", event="inference_done",
        offer_id=offer_id, image_url=image_url,
        status="ok" if result.available else "skip",
        duration_ms=result.duration_ms, level=level,
        data={
            "backend":         result.backend,
            "model":           result.model_name,
            "n_labels":        len(result.labels),
            "scores": {lbl: round(s, 4) for lbl, s in sorted_scores[:15]},
            "best_label":      result.best_label,
            "best_score":      round(result.best_score, 4),
            "validates_yolo":  result.validates_yolo,
        },
    )
    run_logger.inc("clip_ok" if result.available else "clip_fallback")


# ── Model caches ──────────────────────────────────────────────────────────────

def _get_open_clip_model(model_name: str):
    if model_name not in _open_clip_cache:
        import open_clip
        try:
            m, _, p = open_clip.create_model_and_transforms(model_name, pretrained="openai")
        except Exception as exc:
            # P12: explicit warning when requested model is unavailable and fallback kicks in
            log.warning(
                "CLIP model '%s' unavailable (%s) — falling back to ViT-B-32", model_name, exc
            )
            m, _, p = open_clip.create_model_and_transforms("ViT-B-32", pretrained="openai")
        m.eval()
        _open_clip_cache[model_name] = (m, None, p)
    return _open_clip_cache[model_name]


def _get_transformers_model(model_name: str):
    if model_name not in _transformers_cache:
        from transformers import CLIPProcessor, CLIPModel
        # Map common CLIP names to HuggingFace IDs
        hf_map = {
            "ViT-B-32": "openai/clip-vit-base-patch32",
            "ViT-L-14": "openai/clip-vit-large-patch14",
        }
        hf_id = hf_map.get(model_name, "openai/clip-vit-base-patch32")
        model     = CLIPModel.from_pretrained(hf_id)
        processor = CLIPProcessor.from_pretrained(hf_id)
        model.eval()
        _transformers_cache[model_name] = (model, processor)
    return _transformers_cache[model_name]
