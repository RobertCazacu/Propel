"""
Tests for the vision pipeline: YOLO + CLIP + fusion + image_analyzer.

Coverage:
  1) Fallback without models installed — processing never raises
  2) Conflict policy text vs image
  3) Output schema backward-compatible
  4) Multi-image strategy basic
  5) Color mapping + confidence thresholds
"""
import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_pil_image(color=(200, 100, 50), size=(64, 64)):
    """Create a minimal PIL image for tests (no real file I/O)."""
    try:
        from PIL import Image
        img = Image.new("RGB", size, color=color)
        return img
    except ImportError:
        return MagicMock()


# ── 1. Fallback without models installed ─────────────────────────────────────

class TestFallbackWithoutModels:
    def test_yolo_unavailable_returns_fallback(self):
        """detect_objects must return YoloResult(available=False) when ultralytics missing."""
        from core.vision.detection_yolo import detect_objects
        img = _make_pil_image()
        with patch("core.vision.detection_yolo._ultralytics_ok", False):
            result = detect_objects(img, run_logger=None)
        assert result.available is False
        assert result.fallback_used is True
        assert result.error  # non-empty error message

    def test_clip_unavailable_returns_fallback(self):
        """score_labels must return ClipResult(available=False) when no CLIP backend."""
        from core.vision.semantic_clip import score_labels
        img = _make_pil_image()
        with patch("core.vision.semantic_clip._open_clip_ok", False), \
             patch("core.vision.semantic_clip._transformers_ok", False):
            result = score_labels(img, labels=["t-shirt", "dress"], run_logger=None)
        assert result.available is False
        assert result.error

    def test_analyze_product_image_no_crash_without_models(self):
        """analyze_product_image must complete (not raise) even if no vision models available."""
        from core.vision.image_analyzer import analyze_product_image

        with patch("core.vision.detection_yolo._ultralytics_ok", False), \
             patch("core.vision.semantic_clip._open_clip_ok", False), \
             patch("core.vision.semantic_clip._transformers_ok", False), \
             patch("core.vision.image_fetcher.fetch_image") as mock_dl:
            img = _make_pil_image()
            mock_dl.return_value = (img, None)  # (image, error)
            result = analyze_product_image(
                image_url="http://example.com/img.jpg",
                category="Tricouri",
                existing_chars={},
                valid_values_for_cat={},
                mandatory_chars=[],
                enable_color=True,
                enable_yolo=True,
                enable_clip=True,
            )
        # Should not raise; result must have basic fields
        assert hasattr(result, "download_success")
        assert hasattr(result, "suggested_attributes")
        assert isinstance(result.suggested_attributes, dict)


# ── 2. Conflict policy text vs image ─────────────────────────────────────────

class TestFusionConflictPolicy:
    def _text(self, candidate, confidence, source="rules"):
        from core.vision.fusion import TextCategoryResult
        return TextCategoryResult(candidate=candidate, confidence=confidence, source=source)

    def _image(self, candidate, confidence, source="yolo"):
        from core.vision.fusion import ImageCategoryResult
        return ImageCategoryResult(candidate=candidate, confidence=confidence, source=source)

    def test_prefer_text_policy(self):
        from core.vision.fusion import fuse_category
        rules = {"prefer_text_over_image": True, "conflict_policy": "prefer_text",
                 "min_clip_confidence": 0.25}
        result = fuse_category(
            self._text("Tricouri", 0.50),
            self._image("Pantaloni", 0.60),
            rules,
        )
        assert result.final_category == "Tricouri"
        assert result.rule_applied == "prefer_text"

    def test_prefer_image_policy(self):
        from core.vision.fusion import fuse_category
        rules = {"prefer_text_over_image": False, "conflict_policy": "prefer_image",
                 "min_clip_confidence": 0.25}
        result = fuse_category(
            self._text("Tricouri", 0.50),
            self._image("Pantaloni", 0.60),
            rules,
        )
        assert result.final_category == "Pantaloni"
        assert result.rule_applied == "prefer_image"

    def test_review_policy_marks_needs_review(self):
        from core.vision.fusion import fuse_category
        rules = {"conflict_policy": "review", "min_clip_confidence": 0.25}
        result = fuse_category(
            self._text("Tricouri", 0.50),
            self._image("Pantaloni", 0.60),
            rules,
        )
        assert result.needs_review is True
        assert result.rule_applied == "review"

    def test_high_confidence_text_wins_over_weak_image(self):
        from core.vision.fusion import fuse_category
        rules = {"prefer_text_over_image": True, "conflict_policy": "review",
                 "min_clip_confidence": 0.25}
        result = fuse_category(
            self._text("Tricouri", 0.90),
            self._image("Pantaloni", 0.40),
            rules,
        )
        assert result.final_category == "Tricouri"
        assert result.needs_review is False

    def test_both_high_confidence_disagreement_flags_review(self):
        from core.vision.fusion import fuse_category
        rules = {"prefer_text_over_image": True, "conflict_policy": "review",
                 "min_clip_confidence": 0.25}
        result = fuse_category(
            self._text("Tricouri", 0.90),
            self._image("Pantaloni", 0.85),
            rules,
        )
        assert result.final_category == "Tricouri"
        assert result.needs_review is True  # conflict flag

    def test_both_agree_boosts_confidence(self):
        from core.vision.fusion import fuse_category
        rules = {"prefer_text_over_image": True, "conflict_policy": "review",
                 "min_clip_confidence": 0.25}
        result = fuse_category(
            self._text("Tricouri", 0.60),
            self._image("tricouri", 0.65),  # lowercase — same
            rules,
        )
        assert result.final_category == "Tricouri"
        assert result.final_confidence > 0.60  # boosted
        assert result.needs_review is False

    def test_no_image_uses_text_only(self):
        from core.vision.fusion import fuse_category
        rules = {"conflict_policy": "review", "min_clip_confidence": 0.25}
        result = fuse_category(
            self._text("Tricouri", 0.70),
            None,
            rules,
        )
        assert result.final_category == "Tricouri"
        assert result.rule_applied == "text_only"


# ── 3. Output schema backward-compatible ─────────────────────────────────────

class TestOutputSchemaBackwardCompat:
    LEGACY_KEYS = {
        "download_success", "image_url", "dominant_color_normalized", "color_confidence",
        "product_type_hint", "product_type_confidence",
        "suggested_attributes", "needs_review",
    }
    NEW_KEYS = {
        "detected_object", "yolo_confidence", "yolo_bbox", "yolo_fallback_used",
        "used_crop", "clip_best_label", "clip_scores", "clip_confidence",
    }

    def test_to_dict_contains_all_legacy_keys(self):
        from core.vision.image_analyzer import ImageAnalysisResult
        result = ImageAnalysisResult()
        d = result.to_dict()
        for key in self.LEGACY_KEYS:
            assert key in d, f"Legacy key '{key}' missing from to_dict()"

    def test_to_dict_contains_new_keys(self):
        from core.vision.image_analyzer import ImageAnalysisResult
        result = ImageAnalysisResult()
        d = result.to_dict()
        for key in self.NEW_KEYS:
            assert key in d, f"New key '{key}' missing from to_dict()"

    def test_default_new_fields_are_safe(self):
        from core.vision.image_analyzer import ImageAnalysisResult
        r = ImageAnalysisResult()
        assert r.detected_object == ""
        assert r.yolo_confidence == 0.0
        assert r.yolo_bbox == []
        assert r.yolo_fallback_used is False
        assert r.used_crop is False
        assert r.clip_best_label == ""
        assert r.clip_scores == {}
        assert r.clip_confidence == 0.0


# ── 4. Multi-image strategy basic ────────────────────────────────────────────

class TestMultiImageStrategy:
    def test_image_strategy_key_in_visual_rules_defaults(self):
        from core.vision.visual_rules import DEFAULT_RULES
        assert "image_strategy" in DEFAULT_RULES["default"]
        assert DEFAULT_RULES["default"]["image_strategy"] == "first_only"

    def test_max_images_key_in_visual_rules_defaults(self):
        from core.vision.visual_rules import DEFAULT_RULES
        assert "max_images_per_product" in DEFAULT_RULES["default"]
        assert DEFAULT_RULES["default"]["max_images_per_product"] >= 1


# ── 5. Color mapping + confidence thresholds ─────────────────────────────────

class TestColorMappingAndThresholds:
    def test_multicolor_threshold_key_in_defaults(self):
        from core.vision.visual_rules import DEFAULT_RULES
        assert "multicolor_threshold" in DEFAULT_RULES["default"]
        assert 0 < DEFAULT_RULES["default"]["multicolor_threshold"] <= 1.0

    def test_min_color_confidence_key_in_defaults(self):
        from core.vision.visual_rules import DEFAULT_RULES
        assert "min_color_confidence" in DEFAULT_RULES["default"]
        assert DEFAULT_RULES["default"]["min_color_confidence"] > 0

    def test_category_rules_override_default(self):
        from core.vision.visual_rules import get_category_rules
        rules = {
            "default": {"min_color_confidence": 0.60, "multicolor_threshold": 0.80},
            "categories": {
                "Tricouri sport barbati": {"min_color_confidence": 0.75},
            },
        }
        cat_rules = get_category_rules("Tricouri sport barbati", rules=rules)
        assert cat_rules["min_color_confidence"] == 0.75  # overridden
        assert cat_rules["multicolor_threshold"] == 0.80  # inherited

    def test_unknown_category_uses_default(self):
        from core.vision.visual_rules import get_category_rules
        rules = {
            "default": {"min_color_confidence": 0.60},
            "categories": {},
        }
        cat_rules = get_category_rules("Categorie necunoscuta", rules=rules)
        assert cat_rules["min_color_confidence"] == 0.60

    def test_action_to_confidence_mapping(self):
        from core.vision.fusion import action_to_confidence
        assert action_to_confidence("ok") == 0.95
        assert action_to_confidence("assigned") == 0.75
        assert action_to_confidence("ai_assigned") == 0.70
        assert action_to_confidence("unknown") == 0.00
        assert action_to_confidence("nonexistent_action") == 0.00


# ── 6. Vision run logger ──────────────────────────────────────────────────────

class TestVisionRunLogger:
    def test_logger_creates_jsonl_file(self, tmp_path):
        from core.vision import vision_logger
        # Redirect log dir to tmp_path for isolation
        with patch.object(vision_logger, "VISION_LOGS_DIR", tmp_path):
            logger = vision_logger.VisionRunLogger(marketplace="test_mp")
            # Redirect the paths inside the instance too
            logger._jsonl_path   = tmp_path / f"{logger.run_id}.jsonl"
            logger._summary_path = tmp_path / f"{logger.run_id}_summary.json"
            logger.log(
                stage="test", event="hello",
                offer_id="123", image_url="http://x.com/img.jpg",
                status="ok", level="INFO", data={"key": "value"},
            )
            logger.finish()
        assert logger._jsonl_path.exists()
        content = logger._jsonl_path.read_text(encoding="utf-8")
        assert "hello" in content

    def test_logger_inc_and_summary(self, tmp_path):
        from core.vision import vision_logger
        with patch.object(vision_logger, "VISION_LOGS_DIR", tmp_path):
            logger = vision_logger.VisionRunLogger(marketplace="test_mp")
            logger._jsonl_path   = tmp_path / f"{logger.run_id}.jsonl"
            logger._summary_path = tmp_path / f"{logger.run_id}_summary.json"
            logger.inc("yolo_ok")
            logger.inc("yolo_ok")
            logger.inc("clip_fallback")
            summary_path = logger.finish()
        import json
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert summary["stats"]["yolo_ok"] == 2
        assert summary["stats"]["clip_fallback"] == 1

    def test_new_run_logger_factory(self, tmp_path):
        from core.vision import vision_logger
        with patch.object(vision_logger, "VISION_LOGS_DIR", tmp_path):
            logger = vision_logger.new_run_logger("eMAG Romania")
            logger._jsonl_path   = tmp_path / f"{logger.run_id}.jsonl"
            logger._summary_path = tmp_path / f"{logger.run_id}_summary.json"
        assert logger is not None
        assert logger.marketplace == "eMAG Romania"
        logger.finish()
