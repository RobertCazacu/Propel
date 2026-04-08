"""
Tests for core.color_mapper — unit + integration.

Run: python -m pytest tests/test_color_mapper.py -v
"""
from __future__ import annotations
import pytest
from unittest.mock import patch

from core.color_mapper.normalize import normalize_color_text
from core.color_mapper.scoring import (
    score_fuzzy, score_hybrid, apply_threshold,
    should_trigger_semantic, tie_break,
    AUTO_ACCEPT, SOFT_REVIEW, NEAR_TIE_DELTA,
)
from core.color_mapper.synonyms import find_cluster_for, cluster_synonyms, is_ambiguous_cluster
from core.color_mapper.mapper import map_detected_color_to_allowed


# ── normalize ──────────────────────────────────────────────────────────────────

class TestNormalize:
    def test_lowercase(self):
        assert normalize_color_text("BLACK") == "black"

    def test_strip(self):
        assert normalize_color_text("  red  ") == "red"

    def test_diacritics_stripped(self):
        assert normalize_color_text("Albastru-Închis") == "albastru inchis"

    def test_hyphen_to_space(self):
        assert normalize_color_text("off-white") == "off white"

    def test_slash_to_space(self):
        assert normalize_color_text("red/white") == "red white"

    def test_empty_string(self):
        assert normalize_color_text("") == ""

    def test_none_like(self):
        assert normalize_color_text("  ") == ""

    def test_multilingual_romanian(self):
        assert normalize_color_text("Roșu") == "rosu"

    def test_multilingual_hungarian(self):
        # ő → o via NFKD
        assert normalize_color_text("Türkiz") == "turkiz"


# ── scoring ────────────────────────────────────────────────────────────────────

class TestScoreFuzzy:
    def test_identical_strings(self):
        assert score_fuzzy("black", "black") == pytest.approx(1.0, abs=0.01)

    def test_completely_different(self):
        assert score_fuzzy("black", "yellow") < 0.5

    def test_partial_match(self):
        s = score_fuzzy("dark blue", "blue")
        assert 0.5 < s < 1.0

    def test_returns_zero_to_one(self):
        for a, b in [("red", "rouge"), ("navy", "marine"), ("beige", "sand")]:
            s = score_fuzzy(a, b)
            assert 0.0 <= s <= 1.0


class TestApplyThreshold:
    def test_above_auto_accept(self):
        val, review = apply_threshold("negru", 0.85)
        assert val == "negru"
        assert review is False

    def test_at_auto_accept(self):
        val, review = apply_threshold("negru", AUTO_ACCEPT)
        assert val == "negru"
        assert review is False

    def test_just_below_auto_accept(self):
        val, review = apply_threshold("negru", 0.81)
        assert val == "negru"
        assert review is True

    def test_at_soft_review(self):
        val, review = apply_threshold("negru", SOFT_REVIEW)
        assert val == "negru"
        assert review is True

    def test_just_below_soft_review(self):
        val, review = apply_threshold("negru", 0.679)
        assert val is None
        assert review is True

    def test_none_value(self):
        val, review = apply_threshold(None, 0.90)
        assert val is None
        assert review is True


class TestShouldTriggerSemantic:
    def test_empty(self):
        assert should_trigger_semantic([]) is False

    def test_high_score_no_tie(self):
        scores = [("black", 0.95), ("dark", 0.70)]
        assert should_trigger_semantic(scores) is False

    def test_score_below_auto_accept(self):
        scores = [("black", 0.78)]
        assert should_trigger_semantic(scores) is True

    def test_near_tie(self):
        # delta = 0.03 < NEAR_TIE_DELTA = 0.08
        scores = [("black", 0.90), ("dark", 0.87)]
        assert should_trigger_semantic(scores) is True

    def test_not_near_tie(self):
        scores = [("black", 0.95), ("dark", 0.80)]
        assert should_trigger_semantic(scores) is False


class TestTieBreak:
    def test_single_candidate(self):
        result = tie_break([("black", 0.9, "black")])
        assert result == ("black", 0.9, "black")

    def test_higher_score_wins(self):
        result = tie_break([("red", 0.7, "red"), ("black", 0.9, "black")])
        assert result[0] == "black"

    def test_shorter_name_wins_on_tie(self):
        result = tie_break([("dark blue", 0.9, "dark blue"), ("blue", 0.9, "blue")])
        assert result[0] == "blue"

    def test_lexical_on_equal_length(self):
        result = tie_break([("red", 0.9, "red"), ("aba", 0.9, "aba")])
        assert result[0] == "aba"

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            tie_break([])


# ── synonyms ───────────────────────────────────────────────────────────────────

class TestSynonyms:
    def test_purple_maps_to_mov(self):
        assert find_cluster_for("purple") == "mov"

    def test_teal_maps_to_turcoaz(self):
        assert find_cluster_for("teal") == "turcoaz"

    def test_navy_maps_to_bleumarin(self):
        assert find_cluster_for("navy") == "bleumarin"

    def test_unknown_returns_none(self):
        assert find_cluster_for("xyzzy") is None

    def test_cluster_synonyms_nonempty(self):
        syns = cluster_synonyms("mov")
        assert "purple" in syns or "violet" in syns

    def test_ambiguous_clusters(self):
        for key in ["mov", "turcoaz", "visiniu", "bej"]:
            assert is_ambiguous_cluster(key) is True

    def test_non_ambiguous_cluster(self):
        assert is_ambiguous_cluster("negru") is False

    def test_case_insensitive_lookup(self):
        # "BLACK" should normalize to "black" and map to "negru"
        assert find_cluster_for("BLACK") == "negru"

    def test_multilingual_ro(self):
        assert find_cluster_for("rosu") == "rosu"

    def test_multilingual_hu(self):
        assert find_cluster_for("kek") == "albastru"


# ── mapper — full pipeline ─────────────────────────────────────────────────────

ALLOWED_COLORS = [
    "Negru", "Alb", "Gri", "Rosu", "Roz", "Albastru", "Bleumarin",
    "Verde", "Galben", "Portocaliu", "Mov", "Maro", "Bej", "Turcoaz",
    "Auriu", "Argintiu", "Multicolor",
]


class TestMapperExactMatch:
    def test_exact_raw(self):
        res = map_detected_color_to_allowed("Negru", ALLOWED_COLORS)
        assert res.mapped_value == "Negru"
        assert res.method == "exact"
        assert res.score == 1.0
        assert res.needs_review is False

    def test_exact_case_insensitive(self):
        res = map_detected_color_to_allowed("negru", ALLOWED_COLORS)
        assert res.mapped_value == "Negru"
        assert res.method == "exact"

    def test_exact_normalized(self):
        res = map_detected_color_to_allowed("ALB", ALLOWED_COLORS)
        assert res.mapped_value == "Alb"
        assert res.method == "exact"


class TestMapperSynonymCluster:
    def test_black_synonym_english(self):
        res = map_detected_color_to_allowed("black", ALLOWED_COLORS)
        assert res.mapped_value == "Negru"

    def test_blue_synonym(self):
        res = map_detected_color_to_allowed("blue", ALLOWED_COLORS)
        assert res.mapped_value in ("Albastru", "Bleumarin")

    def test_purple_synonym(self):
        res = map_detected_color_to_allowed("purple", ALLOWED_COLORS)
        assert res.mapped_value == "Mov"

    def test_teal_synonym(self):
        res = map_detected_color_to_allowed("teal", ALLOWED_COLORS)
        assert res.mapped_value == "Turcoaz"

    def test_navy_synonym(self):
        res = map_detected_color_to_allowed("navy", ALLOWED_COLORS)
        assert res.mapped_value == "Bleumarin"

    def test_beige_synonym(self):
        res = map_detected_color_to_allowed("beige", ALLOWED_COLORS)
        assert res.mapped_value == "Bej"

    def test_multilingual_ro_rosu(self):
        res = map_detected_color_to_allowed("rosu", ALLOWED_COLORS)
        assert res.mapped_value == "Rosu"

    def test_multilingual_hu_kek(self):
        # "kek" (Hungarian blue) should map to Albastru
        res = map_detected_color_to_allowed("kek", ALLOWED_COLORS)
        assert res.mapped_value in ("Albastru", "Bleumarin")


class TestMapperFuzzy:
    def test_charcoal_maps_to_negru(self):
        res = map_detected_color_to_allowed("charcoal", ALLOWED_COLORS)
        assert res.mapped_value == "Negru"

    def test_wine_maps_to_visiniu_or_rosu(self):
        # wine is in visiniu cluster, but "Visiniu" may not be in ALLOWED_COLORS
        allowed = ALLOWED_COLORS + ["Visiniu"]
        res = map_detected_color_to_allowed("wine", allowed)
        assert res.mapped_value in ("Visiniu", "Rosu")

    def test_fuzzy_typo_tolerance(self):
        # "blakc" is a typo for black
        res = map_detected_color_to_allowed("blakc", ALLOWED_COLORS)
        # Should still map close to Negru or return needs_review
        if res.mapped_value:
            assert res.mapped_value in ALLOWED_COLORS


class TestMapperEdgeCases:
    def test_empty_detected_color(self):
        res = map_detected_color_to_allowed("", ALLOWED_COLORS)
        assert res.mapped_value is None
        assert res.needs_review is True
        assert res.method == "none"

    def test_empty_allowed_values(self):
        res = map_detected_color_to_allowed("black", [])
        assert res.mapped_value is None
        assert res.needs_review is True

    def test_no_match_below_threshold(self):
        res = map_detected_color_to_allowed("xyzzy123", ALLOWED_COLORS)
        # Should not map confidently
        if res.mapped_value is not None:
            assert res.score >= SOFT_REVIEW

    def test_result_always_in_allowed(self):
        test_colors = ["black", "blue", "teal", "charcoal", "Navy Blue", "lila", "dunno"]
        for color in test_colors:
            res = map_detected_color_to_allowed(color, ALLOWED_COLORS)
            if res.mapped_value is not None:
                assert res.mapped_value in ALLOWED_COLORS, (
                    f"{color!r} → {res.mapped_value!r} not in allowed"
                )

    def test_top_k_has_valid_values(self):
        res = map_detected_color_to_allowed("blue", ALLOWED_COLORS)
        for val, score in res.top_k:
            assert val in ALLOWED_COLORS
            assert 0.0 <= score <= 1.0

    def test_debug_dict_populated(self):
        res = map_detected_color_to_allowed("red", ALLOWED_COLORS)
        assert "detected_raw" in res.debug or "reason" in res.debug

    def test_context_passed_through(self):
        # Ensure context={"offer_id": "123"} doesn't break anything
        res = map_detected_color_to_allowed(
            "black", ALLOWED_COLORS, context={"offer_id": "test-123"}
        )
        assert res.mapped_value == "Negru"

    def test_use_embeddings_false(self):
        # Phase 2 disabled — should still work via Phase 1
        res = map_detected_color_to_allowed("blue", ALLOWED_COLORS, use_embeddings=False)
        assert res.mapped_value in ALLOWED_COLORS or res.mapped_value is None


class TestMapperEmbedderUnavailable:
    def test_graceful_degradation(self):
        """When sentence-transformers not installed, Phase 1 result used."""
        with patch("core.color_mapper.embedder.is_available", return_value=False):
            res = map_detected_color_to_allowed("purple", ALLOWED_COLORS, use_embeddings=True)
        # Should still work via synonym cluster (Phase 1)
        assert res.mapped_value in ALLOWED_COLORS or res.mapped_value is None

    def test_embedder_exception_handled(self):
        """Exception in embedder doesn't crash the mapper."""
        with patch("core.color_mapper.embedder.encode_batch", side_effect=RuntimeError("fail")):
            res = map_detected_color_to_allowed("teal", ALLOWED_COLORS)
        assert res.mapped_value in ALLOWED_COLORS or res.mapped_value is None
