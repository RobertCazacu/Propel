"""
Phase 2 — optional semantic embedder (lazy-loaded).

Activated only when:
  - sentence-transformers is installed
  - top fuzzy score < AUTO_ACCEPT  OR  near-tie between top candidates
  - AMBIGUOUS_CLUSTERS membership

Model: paraphrase-multilingual-mpnet-base-v2 (420MB, best multilingual quality)
       or intfloat/multilingual-e5-base (280MB, good alternative)

The embedder is a module-level singleton — loaded once, reused across calls.
Thread-safe: model loading is idempotent (read-only after load).
"""
from __future__ import annotations
import logging
import threading
from typing import Optional

import numpy as np

log = logging.getLogger("marketplace.color_mapper.embedder")

_MODEL_NAME = "paraphrase-multilingual-mpnet-base-v2"
_model = None
_model_lock = threading.Lock()
_available: Optional[bool] = None


def is_available() -> bool:
    """Check if sentence-transformers is installed (without loading model)."""
    global _available
    if _available is None:
        try:
            import sentence_transformers  # noqa
            _available = True
        except ImportError:
            _available = False
    return _available


def _get_model():
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer
            log.info("[Embedder] Loading model '%s' (first use)...", _MODEL_NAME)
            _model = SentenceTransformer(_MODEL_NAME)
            log.info("[Embedder] Model loaded.")
    return _model


def encode_batch(texts: list[str]) -> np.ndarray:
    """
    Encode a list of strings → float32 matrix (N, D).
    Returns empty array on failure (graceful degradation).
    """
    if not texts or not is_available():
        return np.zeros((len(texts), 1), dtype=np.float32)
    try:
        model = _get_model()
        return model.encode(texts, convert_to_numpy=True, show_progress_bar=False)
    except Exception as exc:
        log.warning("[Embedder] encode_batch failed: %s", exc)
        return np.zeros((len(texts), 1), dtype=np.float32)


def encode_one(text: str) -> np.ndarray:
    """Encode a single string → 1D float32 array."""
    result = encode_batch([text])
    return result[0] if result.ndim == 2 else result


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1D arrays. Returns 0.0 on error."""
    try:
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(a, b) / (norm_a * norm_b))
    except Exception:
        return 0.0


def rerank_with_embeddings(
    query: str,
    candidates: list[tuple[str, float]],  # [(value, fuzzy_score), ...]
) -> list[tuple[str, float]]:
    """
    Re-rank candidates using hybrid score (semantic + fuzzy).
    Falls back to original ordering if embedder unavailable.

    Returns [(value, hybrid_score), ...] sorted descending.
    """
    if not is_available() or not candidates:
        return candidates

    from core.color_mapper.scoring import score_hybrid

    try:
        values = [v for v, _ in candidates]
        fuzzy_scores = {v: s for v, s in candidates}

        all_texts = [query] + values
        embeddings = encode_batch(all_texts)
        query_emb = embeddings[0]
        cand_embs = embeddings[1:]

        reranked = []
        for i, value in enumerate(values):
            from core.color_mapper.normalize import normalize_color_text
            sem = cosine_similarity(query_emb, cand_embs[i])
            hybrid = score_hybrid(
                normalize_color_text(query),
                normalize_color_text(value),
                semantic_score=sem,
            )
            reranked.append((value, hybrid))

        reranked.sort(key=lambda x: -x[1])
        return reranked

    except Exception as exc:
        log.warning("[Embedder] rerank_with_embeddings failed: %s", exc)
        return candidates
