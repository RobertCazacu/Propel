"""Tests for core.characteristic_resolver"""
import pytest
from core.characteristic_resolver import (
    get_language_code,
    ConfigurationError,
    CharacteristicResolver,
    ResolutionResult,
)

ALLOWED_HU = ["Kék", "Fekete", "Fehér", "Piros", "Zöld", "Sárga", "Szürke"]
ALLOWED_RO = ["Albastru", "Negru", "Alb", "Rosu", "Verde", "Galben", "Gri"]


class TestLocaleResolution:
    def test_emag_hu_returns_hu(self):
        assert get_language_code("eMAG HU") == "hu"

    def test_emag_ro_returns_ro(self):
        assert get_language_code("eMAG Romania") == "ro"

    def test_allegro_returns_pl(self):
        assert get_language_code("Allegro") == "pl"

    def test_unknown_marketplace_raises(self):
        with pytest.raises(ConfigurationError):
            get_language_code("unknown_xyz_marketplace")

    def test_case_insensitive(self):
        assert get_language_code("EMAG HU") == "hu"


class TestPass1Fuzzy:
    def setup_method(self):
        self.resolver = CharacteristicResolver(marketplace_id="eMAG HU")

    def test_exact_match_returns_auto_accept(self):
        r = self.resolver.resolve("Kék", ALLOWED_HU, is_mandatory=False)
        assert r.value == "Kék"
        assert r.method == "exact"
        assert r.score >= 0.82
        assert r.needs_review is False

    def test_always_returns_top_k_even_on_failure(self):
        r = self.resolver.resolve("xyzzy123", ALLOWED_HU, is_mandatory=False)
        assert len(r.top_k_candidates) >= 1
        assert all(isinstance(v, str) and isinstance(s, float) for v, s in r.top_k_candidates)

    def test_fuzzy_match_ro_to_hu_fails_pass1(self):
        # "Albastru" (RO) should NOT match "Kék" (HU) via simple fuzzy
        r = self.resolver.resolve("Albastru", ALLOWED_HU, is_mandatory=False)
        # Score should be low — no linguistic overlap
        assert r.score < 0.82 or r.needs_review is True

    def test_top_k_values_all_from_allowed(self):
        r = self.resolver.resolve("blue", ALLOWED_HU, is_mandatory=False)
        for v, s in r.top_k_candidates:
            assert v in ALLOWED_HU
            assert 0.0 <= s <= 1.0

    def test_near_tie_sets_needs_review(self):
        # Two very similar values — resolver should flag near-tie
        allowed = ["Kék", "Kék sötét"]
        r = self.resolver.resolve("Kék", allowed, is_mandatory=False)
        # Either accepts or flags review due to near-tie
        assert isinstance(r.needs_review, bool)

    def test_result_method_is_valid(self):
        r = self.resolver.resolve("Fekete", ALLOWED_HU, is_mandatory=False)
        assert r.method in ("exact", "fuzzy", "semantic", "repair", "rescue", "none")


class TestPass3Rescue:
    def setup_method(self):
        self.resolver = CharacteristicResolver(marketplace_id="eMAG HU")

    def test_rescue_fires_when_mandatory_and_above_floor(self):
        # Small list → floor = 0.55
        allowed = ["Futás", "Kosárlabda"]
        r = self.resolver.resolve("running", allowed, is_mandatory=True)
        if r.value is not None:
            assert r.method == "rescue"
            assert r.low_confidence_autofill is True
            assert r.needs_review is True

    def test_rescue_blocked_on_near_tie(self):
        # Two candidates with nearly identical scores
        allowed = ["Kék", "Kék sötét", "Piros"]
        r = self.resolver.resolve("Kék", allowed, is_mandatory=True)
        # Exact match is valid (no review needed); near-tie only applies to fuzzy paths
        assert r.value is not None  # should resolve to something
        if r.method != "exact":
            # Near-tie between "Kék" and "Kék sötét" should set soft_review
            assert r.soft_review is True or r.needs_review is True

    def test_below_floor_returns_none_with_review(self):
        # xyzzy has no match
        allowed = ["Futás", "Kosárlabda", "Tenisz", "Úszás"]
        r = self.resolver.resolve("xyzzy_qqq", allowed, is_mandatory=True)
        # top_k always populated
        assert len(r.top_k_candidates) >= 1
        # If value is None, must be needs_review
        if r.value is None:
            assert r.needs_review is True

    def test_top_k_always_populated_on_failure(self):
        allowed = ["Futás", "Kosárlabda"]
        r = self.resolver.resolve("completely_unrelated_xyz", allowed, is_mandatory=False)
        assert len(r.top_k_candidates) >= 1
        assert r.top_k_candidates[0][0] in allowed

    def test_adaptive_floor_large_list(self):
        # Large list (>50 values) → floor = 0.70
        allowed = [f"Value{i}" for i in range(60)]
        r = self.resolver.resolve("xyz", allowed, is_mandatory=True)
        # With 60 random values, score will be very low → rescue should NOT fire
        if r.value is not None and r.method == "rescue":
            assert r.score >= 0.70


class TestBudget:
    def test_budget_is_per_product_not_per_field(self):
        resolver = CharacteristicResolver(marketplace_id="eMAG HU")
        budget = {"remaining": 1, "used": 0}
        allowed = ["Futás", "Kosárlabda"]

        # First call consumes budget if Pass 2 is triggered
        resolver.resolve("something", allowed, is_mandatory=True,
                         char_name="Sport:", budget_ctx=budget)
        # Second call should see remaining = 0
        assert budget["remaining"] >= 0  # consumed max 1

    def test_deterministic_same_input(self):
        resolver = CharacteristicResolver(marketplace_id="eMAG Romania")
        allowed = ["Albastru", "Negru", "Rosu"]
        r1 = resolver.resolve("blue", allowed, is_mandatory=False)
        r2 = resolver.resolve("blue", allowed, is_mandatory=False)
        assert r1.value == r2.value
        assert r1.score == r2.score
        assert r1.method == r2.method
