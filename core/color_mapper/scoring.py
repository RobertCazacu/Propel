"""
Color candidate scoring — Phase 1 (fuzzy + token overlap).

Phase 2 hook: if embedder is available and conditions are met,
semantic score is blended in via score_with_embeddings().

Thresholds
----------
AUTO_ACCEPT  >= 0.82  → inject value, needs_review=False
SOFT_REVIEW  >= 0.68  → inject value, needs_review=True  (soft flag)
< SOFT_REVIEW         → no value injected, needs_review=True
"""
from __future__ import annotations

AUTO_ACCEPT: float = 0.82
SOFT_REVIEW: float = 0.68

# Delta below which two top candidates are considered "near-tie" (triggers Phase 2)
NEAR_TIE_DELTA: float = 0.08


def _jaccard(a: str, b: str) -> float:
    ta = set(a.split()) if a else set()
    tb = set(b.split()) if b else set()
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def score_fuzzy(query_norm: str, candidate_norm: str) -> float:
    """
    Phase 1 score: 0.75 * fuzzy + 0.25 * jaccard token overlap.
    Returns 0.0–1.0.
    """
    try:
        from rapidfuzz import fuzz as _fuzz
        fuzzy = _fuzz.token_set_ratio(query_norm, candidate_norm) / 100.0
    except ImportError:
        # Fallback: simple ratio without rapidfuzz
        from difflib import SequenceMatcher
        fuzzy = SequenceMatcher(None, query_norm, candidate_norm).ratio()

    jaccard = _jaccard(query_norm, candidate_norm)
    return round(0.75 * fuzzy + 0.25 * jaccard, 6)


def score_hybrid(
    query_norm: str,
    candidate_norm: str,
    semantic_score: float,
) -> float:
    """
    Phase 2 score (when embeddings available):
    0.65 * semantic + 0.25 * fuzzy + 0.10 * jaccard.
    """
    try:
        from rapidfuzz import fuzz as _fuzz
        fuzzy = _fuzz.token_set_ratio(query_norm, candidate_norm) / 100.0
    except ImportError:
        from difflib import SequenceMatcher
        fuzzy = SequenceMatcher(None, query_norm, candidate_norm).ratio()

    jaccard = _jaccard(query_norm, candidate_norm)
    return round(
        0.65 * semantic_score + 0.25 * fuzzy + 0.10 * jaccard,
        6,
    )


def should_trigger_semantic(top_scores: list[tuple[str, float]]) -> bool:
    """
    Return True if top-1 score is low OR top-1/top-2 are near-tie.
    Used in Phase 2 to decide whether to invoke embeddings.
    """
    if not top_scores:
        return False
    top1_score = top_scores[0][1]
    if top1_score < AUTO_ACCEPT:
        return True
    if len(top_scores) >= 2:
        delta = top1_score - top_scores[1][1]
        if delta < NEAR_TIE_DELTA:
            return True
    return False


def apply_threshold(
    mapped_value: str | None,
    score: float,
) -> tuple[str | None, bool]:
    """
    Apply AUTO_ACCEPT / SOFT_REVIEW thresholds.

    Returns:
        (value_to_inject, needs_review)
    """
    if mapped_value is None or score < SOFT_REVIEW:
        return None, True
    needs_review = score < AUTO_ACCEPT
    return mapped_value, needs_review


def tie_break(candidates: list[tuple[str, float, str]]) -> tuple[str, float, str]:
    """
    Deterministic tie-break for candidates with equal score:
      1. Higher score (already sorted)
      2. Shorter raw_value length
      3. Lexical order (alphabetical, deterministic)

    candidates: [(raw_value, score, normalized), ...]
    Returns best (raw_value, score, normalized).
    """
    if not candidates:
        raise ValueError("tie_break: empty candidates")
    if len(candidates) == 1:
        return candidates[0]
    # Sort by: score DESC, length ASC, lexical ASC
    candidates.sort(key=lambda x: (-x[1], len(x[0]), x[0]))
    return candidates[0]
