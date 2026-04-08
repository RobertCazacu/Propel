"""
Color mapper — main entry point.

Pipeline (per call):
  1. Validate input
  2. Exact match (raw + case-insensitive)
  3. Synonym cluster lookup → candidates from same cluster
  4. Fuzzy scoring of all allowed values → top-k
  5. If near-tie OR ambiguous cluster → Phase 2 semantic rerank (if available)
  6. Apply thresholds → ColorMappingResult

Public API:
    map_detected_color_to_allowed(detected_color, allowed_values, ...) -> ColorMappingResult
"""
from __future__ import annotations
import logging
from typing import Sequence

from core.color_mapper.types import ColorMappingResult, ColorCandidate
from core.color_mapper.normalize import normalize_color_text
from core.color_mapper.synonyms import (
    find_cluster_for, cluster_synonyms, is_ambiguous_cluster,
)
from core.color_mapper.scoring import (
    score_fuzzy, apply_threshold, should_trigger_semantic,
    tie_break, AUTO_ACCEPT, SOFT_REVIEW,
)

log = logging.getLogger("marketplace.color_mapper")

TOP_K = 5   # number of candidates to surface in debug output


# ── Internal helpers ──────────────────────────────────────────────────────────

def _build_candidates(allowed_values: Sequence[str]) -> list[ColorCandidate]:
    return [
        ColorCandidate(raw_value=v, normalized=normalize_color_text(v))
        for v in allowed_values
    ]


def _exact_match(
    query_raw: str,
    query_norm: str,
    candidates: list[ColorCandidate],
) -> ColorCandidate | None:
    """Return candidate on exact raw or normalized match."""
    for c in candidates:
        if c.raw_value == query_raw:
            return c
        if c.normalized == query_norm:
            return c
    return None


def _synonym_candidates(
    query_norm: str,
    candidates: list[ColorCandidate],
) -> list[ColorCandidate]:
    """Return candidates whose normalized form is in the same cluster as query."""
    cluster_key = find_cluster_for(query_norm)
    if cluster_key is None:
        return []
    synonyms = cluster_synonyms(cluster_key)
    return [c for c in candidates if c.normalized in synonyms]


def _score_all(
    query_norm: str,
    candidates: list[ColorCandidate],
) -> list[ColorCandidate]:
    """Compute fuzzy score for every candidate. Returns sorted list (desc)."""
    scored = []
    for c in candidates:
        s = score_fuzzy(query_norm, c.normalized)
        scored.append(ColorCandidate(
            raw_value=c.raw_value,
            normalized=c.normalized,
            score=s,
            method="fuzzy",
        ))
    scored.sort(key=lambda x: (-x.score, len(x.raw_value), x.raw_value))
    return scored


# ── Public API ────────────────────────────────────────────────────────────────

def map_detected_color_to_allowed(
    detected_color: str,
    allowed_values: Sequence[str],
    *,
    context: dict | None = None,
    use_embeddings: bool = True,   # Phase 2: set False to force Phase 1 only
) -> ColorMappingResult:
    """
    Map a detected color string to the closest valid marketplace value.

    Parameters
    ----------
    detected_color : str
        Raw color string from PIL or Ollama (any language).
    allowed_values : sequence of str
        Valid marketplace values for the color characteristic.
    context : dict, optional
        Observability context (offer_id, marketplace, etc.) for logging.
    use_embeddings : bool
        Enable Phase 2 semantic reranking when fuzzy is uncertain.

    Returns
    -------
    ColorMappingResult
        Never contains a value outside allowed_values.
    """
    ctx = context or {}
    offer_id = ctx.get("offer_id", "")

    # ── Guard: empty inputs ───────────────────────────────────────────────────
    if not detected_color or not allowed_values:
        log.debug("[ColorMapper] Empty input offer=%s detected=%r", offer_id, detected_color)
        return ColorMappingResult(
            mapped_value=None, score=0.0, method="none",
            top_k=[], needs_review=True,
            debug={"reason": "empty_input", "detected": detected_color or ""},
        )

    query_raw  = detected_color.strip()
    query_norm = normalize_color_text(query_raw)
    candidates = _build_candidates(allowed_values)

    # ── Step 1: Exact match ───────────────────────────────────────────────────
    exact = _exact_match(query_raw, query_norm, candidates)
    if exact:
        log.debug(
            "[ColorMapper] EXACT '%s'→'%s' offer=%s", query_raw, exact.raw_value, offer_id,
        )
        return ColorMappingResult(
            mapped_value=exact.raw_value, score=1.0, method="exact",
            top_k=[(exact.raw_value, 1.0)], needs_review=False,
            debug={
                "detected_raw": query_raw,
                "detected_normalized": query_norm,
                "mapping_method": "exact",
                "threshold_used": AUTO_ACCEPT,
                "fallback_used": False,
            },
        )

    # ── Step 2: Synonym cluster → promoted candidates ─────────────────────────
    cluster_key  = find_cluster_for(query_norm)
    syn_cands    = _synonym_candidates(query_norm, candidates)
    is_ambiguous = is_ambiguous_cluster(cluster_key)

    log.debug(
        "[ColorMapper] cluster=%s ambiguous=%s syn_candidates=%d offer=%s",
        cluster_key, is_ambiguous, len(syn_cands), offer_id,
    )

    # Score synonym candidates at top — they get a head start
    # Cluster membership = confirmed semantic equivalence across languages.
    # Guarantee minimum score above AUTO_ACCEPT so cross-language synonyms
    # (e.g. "black" → "Negru") always map without requiring embeddings.
    CLUSTER_MIN = AUTO_ACCEPT + 0.03  # 0.85
    scored: list[ColorCandidate] = []
    if syn_cands:
        for c in syn_cands:
            s = score_fuzzy(query_norm, c.normalized)
            boosted = max(min(s + 0.15, 1.0), CLUSTER_MIN)
            scored.append(ColorCandidate(
                raw_value=c.raw_value,
                normalized=c.normalized,
                score=boosted,
                method="synonym",
            ))

    # Score all remaining candidates (not in synonym set)
    syn_raws = {c.raw_value for c in syn_cands}
    remaining = [c for c in candidates if c.raw_value not in syn_raws]
    for c in remaining:
        s = score_fuzzy(query_norm, c.normalized)
        scored.append(ColorCandidate(
            raw_value=c.raw_value,
            normalized=c.normalized,
            score=s,
            method="fuzzy",
        ))

    # Sort: score DESC, length ASC, lexical ASC (deterministic)
    scored.sort(key=lambda x: (-x.score, len(x.raw_value), x.raw_value))

    top_k_pairs = [(c.raw_value, round(c.score, 4)) for c in scored[:TOP_K]]
    best = scored[0] if scored else None

    if best is None or best.score < SOFT_REVIEW:
        log.warning(
            "[ColorMapper] No confident match for '%s' (top=%.3f) offer=%s",
            query_raw, best.score if best else 0.0, offer_id,
        )
        return ColorMappingResult(
            mapped_value=None, score=best.score if best else 0.0,
            method="none", top_k=top_k_pairs, needs_review=True,
            debug={
                "detected_raw": query_raw,
                "detected_normalized": query_norm,
                "cluster": cluster_key,
                "top_k_candidates": top_k_pairs,
                "mapping_method": "none",
                "threshold_used": SOFT_REVIEW,
                "fallback_used": False,
                "reason": "score_below_soft_review",
            },
        )

    # ── Step 3: Phase 2 — semantic rerank if uncertain or ambiguous ───────────
    fallback_used = False
    if use_embeddings and (is_ambiguous or should_trigger_semantic(top_k_pairs)):
        try:
            from core.color_mapper.embedder import rerank_with_embeddings, is_available
            if is_available():
                reranked = rerank_with_embeddings(query_raw, top_k_pairs)
                if reranked:
                    top_k_pairs = reranked
                    best_raw, best_score = reranked[0]
                    # Find the original candidate to get method
                    best = next(
                        (c for c in scored if c.raw_value == best_raw),
                        scored[0],
                    )
                    best = ColorCandidate(
                        raw_value=best_raw,
                        normalized=normalize_color_text(best_raw),
                        score=best_score,
                        method="semantic" if best.method == "fuzzy" else best.method,
                    )
                    fallback_used = True
                    log.debug(
                        "[ColorMapper] Semantic rerank → '%s' (%.3f) offer=%s",
                        best.raw_value, best.score, offer_id,
                    )
        except Exception as exc:
            log.warning("[ColorMapper] Semantic rerank error: %s", exc)

    # ── Step 4: Threshold decision ────────────────────────────────────────────
    mapped_value, needs_review = apply_threshold(best.raw_value, best.score)

    log.info(
        "[ColorMapper] '%s' → '%s' method=%s score=%.3f review=%s offer=%s",
        query_raw, mapped_value, best.method, best.score, needs_review, offer_id,
    )

    return ColorMappingResult(
        mapped_value=mapped_value,
        score=round(best.score, 4),
        method=best.method,
        top_k=top_k_pairs[:TOP_K],
        needs_review=needs_review,
        debug={
            "detected_raw": query_raw,
            "detected_normalized": query_norm,
            "cluster": cluster_key,
            "top_k_candidates": top_k_pairs[:TOP_K],
            "selected_value": mapped_value,
            "selected_score": round(best.score, 4),
            "mapping_method": best.method,
            "threshold_used": AUTO_ACCEPT if not needs_review else SOFT_REVIEW,
            "fallback_used": fallback_used,
            "ambiguous_cluster": is_ambiguous,
        },
    )
