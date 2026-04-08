"""
YOLO object detection — lazy-loaded, fully fallback-safe.

If ultralytics is not installed all functions return empty/fallback results
without raising exceptions.  Model weights are downloaded on first use by
ultralytics and cached in the default Ultralytics cache dir.

Public API:
    detect_objects(img, model_name, conf_threshold, ...) -> YoloResult
    crop_to_detection(img, detection) -> PIL.Image
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image as _PILImage

_ultralytics_ok: Optional[bool] = None
_model_cache: dict = {}


# ── Availability check ────────────────────────────────────────────────────────

def is_available() -> bool:
    global _ultralytics_ok
    if _ultralytics_ok is None:
        try:
            import ultralytics  # noqa
            _ultralytics_ok = True
        except ImportError:
            _ultralytics_ok = False
    return _ultralytics_ok


# ── Result types ──────────────────────────────────────────────────────────────

@dataclass
class YoloDetection:
    label: str
    confidence: float
    bbox: list  # [x1, y1, x2, y2] in pixel coords (original image)


@dataclass
class YoloResult:
    available: bool     = False   # False → ultralytics not installed
    fallback_used: bool = False   # True  → no detections, use full image
    detections: list    = field(default_factory=list)  # list[YoloDetection]
    best: Optional[YoloDetection] = None
    model_name: str     = ""
    conf_threshold: float = 0.0
    duration_ms: int    = 0
    error: str          = ""


# ── Main detection function ───────────────────────────────────────────────────

def detect_objects(
    img,                         # PIL.Image.Image
    model_name: str = "yolov8n.pt",
    conf_threshold: float = 0.50,
    label_allowlist: list | None = None,  # None or [] = no filter
    run_logger=None,             # VisionRunLogger | None
    offer_id: str = "",
    image_url: str = "",
) -> YoloResult:
    """
    Run YOLO on a PIL image.  Returns YoloResult with detections sorted
    by confidence descending.  Falls back to full-image if no detections.
    """
    if not is_available():
        if run_logger:
            run_logger.log(
                stage="yolo", event="unavailable", offer_id=offer_id,
                image_url=image_url, status="skip", level="WARNING",
                data={"reason": "ultralytics not installed"},
            )
            run_logger.inc("yolo_fallback")
        return YoloResult(available=False, fallback_used=True,
                          error="ultralytics not installed")

    t0 = time.perf_counter()
    try:
        if run_logger:
            run_logger.log(
                stage="yolo", event="inference_start", offer_id=offer_id,
                image_url=image_url, status="ok", level="DEBUG",
                data={"model": model_name, "conf_threshold": conf_threshold,
                      "img_size": list(img.size)},
            )

        model   = _get_model(model_name)
        results = model(img, conf=conf_threshold, verbose=False)
        duration_ms = round((time.perf_counter() - t0) * 1000)

        detections: list[YoloDetection] = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                conf   = float(box.conf[0])
                cls_id = int(box.cls[0])
                label  = model.names.get(cls_id, str(cls_id))
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                detections.append(YoloDetection(
                    label=label,
                    confidence=round(conf, 4),
                    bbox=[round(x1), round(y1), round(x2), round(y2)],
                ))

        detections.sort(key=lambda d: -d.confidence)

        # Apply label allowlist filter
        if label_allowlist:
            allowed = {lbl.lower() for lbl in label_allowlist}
            filtered = [d for d in detections if d.label.lower() in allowed]
            if run_logger and len(filtered) < len(detections):
                removed = [d.label for d in detections if d.label.lower() not in allowed]
                run_logger.log(
                    stage="yolo", event="label_filter",
                    offer_id=offer_id, image_url=image_url,
                    status="ok", level="DEBUG",
                    data={"removed_labels": removed, "kept": len(filtered)},
                )
            detections = filtered

        best         = detections[0] if detections else None
        fallback_used = best is None

        if run_logger:
            run_logger.log(
                stage="yolo", event="inference_done",
                offer_id=offer_id, image_url=image_url,
                status="ok", duration_ms=duration_ms,
                level="INFO" if best else "WARNING",
                data={
                    "model":           model_name,
                    "conf_threshold":  conf_threshold,
                    "n_detections":    len(detections),
                    "detections": [
                        {"label": d.label, "confidence": d.confidence, "bbox": d.bbox}
                        for d in detections[:10]
                    ],
                    "best": (
                        {"label": best.label, "confidence": best.confidence, "bbox": best.bbox}
                        if best else None
                    ),
                    "fallback_used":    fallback_used,
                    "selection_reason": (
                        "highest confidence"
                        if best else "no detections — fallback to full image"
                    ),
                },
            )
            run_logger.inc("yolo_ok" if best else "yolo_fallback")

        return YoloResult(
            available=True,
            fallback_used=fallback_used,
            detections=detections,
            best=best,
            model_name=model_name,
            conf_threshold=conf_threshold,
            duration_ms=duration_ms,
        )

    except Exception as exc:
        duration_ms = round((time.perf_counter() - t0) * 1000)
        if run_logger:
            run_logger.log(
                stage="yolo", event="error",
                offer_id=offer_id, image_url=image_url,
                status="error", duration_ms=duration_ms, level="ERROR",
                data={"error": str(exc)[:300]},
            )
            run_logger.inc("yolo_fallback")
        return YoloResult(
            available=True, fallback_used=True,
            model_name=model_name, duration_ms=duration_ms,
            error=str(exc)[:200],
        )


# ── Crop helper ───────────────────────────────────────────────────────────────

def crop_to_detection(img, detection: YoloDetection):
    """
    Return img cropped to YOLO bounding box.
    Clamps coordinates to image bounds; returns original if bbox is degenerate.
    """
    x1, y1, x2, y2 = detection.bbox
    w, h = img.size
    x1 = max(0, min(int(x1), w - 1))
    y1 = max(0, min(int(y1), h - 1))
    x2 = max(0, min(int(x2), w))
    y2 = max(0, min(int(y2), h))
    if x2 > x1 + 5 and y2 > y1 + 5:
        return img.crop((x1, y1, x2, y2))
    return img


def save_crop(img, detection: YoloDetection, path) -> bool:
    """Save cropped detection to path. Returns True on success."""
    try:
        cropped = crop_to_detection(img, detection)
        cropped.save(str(path), "JPEG", quality=85)
        return True
    except Exception:
        return False


def save_yolo_overlay(img, yolo_result: YoloResult, path) -> bool:
    """
    Draw YOLO bounding boxes on image and save.
    Requires PIL.ImageDraw (no OpenCV needed).
    """
    try:
        from PIL import ImageDraw, ImageFont
        overlay = img.copy()
        draw    = ImageDraw.Draw(overlay)
        for det in yolo_result.detections[:5]:
            x1, y1, x2, y2 = det.bbox
            color = "red" if det is yolo_result.best else "yellow"
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
            label_text = f"{det.label} {det.confidence:.2f}"
            draw.text((x1 + 2, y1 + 2), label_text, fill=color)
        overlay.save(str(path), "JPEG", quality=85)
        return True
    except Exception:
        return False


# ── Model cache ───────────────────────────────────────────────────────────────

def _get_model(model_name: str):
    if model_name not in _model_cache:
        try:
            from ultralytics import YOLO
            _model_cache[model_name] = YOLO(model_name)
        except Exception as exc:
            # P07: raise explicit error so callers get a clear message, not AttributeError/None
            raise RuntimeError(
                f"YOLO model '{model_name}' could not be loaded: {exc}"
            ) from exc
    return _model_cache[model_name]
