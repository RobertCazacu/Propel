"""
AI Request/Response Logger.

Saves every AI call (request + response) to data/ai_logs/YYYY-MM-DD.json.
Entries older than 24h are automatically removed on each write.

Each entry records:
  - timestamp, provider, model, marketplace, offer_id(s)
  - full prompt sent
  - full raw response received
  - parsed result
  - duration_ms
  - accepted / rejected counts
"""
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

AI_LOGS_DIR = Path(__file__).parent.parent / "data" / "ai_logs"
MAX_AGE_DAYS = 30

_run_file: Path | None = None


# ── Run context ────────────────────────────────────────────────────────────────

def start_run(marketplace: str) -> Path:
    """
    Call at the start of each processing run.
    Creates a new log file: data/ai_logs/YYYY-MM-DD_HH-MM-SS_marketplace.json
    """
    global _run_file
    AI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _cleanup()
    ts_str  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    safe_mp = marketplace.replace(" ", "_")
    _run_file = AI_LOGS_DIR / f"{ts_str}_{safe_mp}.json"
    return _run_file


# ── Cleanup ────────────────────────────────────────────────────────────────────

def _cleanup():
    """Remove log files older than MAX_AGE_DAYS."""
    if not AI_LOGS_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    for f in AI_LOGS_DIR.glob("*.json"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except Exception:
            pass


def _current_file() -> Path:
    """Return the active run file, or a fallback if start_run() was not called."""
    if _run_file is not None:
        return _run_file
    AI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return AI_LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"


def _append_entry(entry: dict):
    """Append one entry to the current run's log file (JSON array)."""
    path = _current_file()
    if path.exists():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            entries = []
    else:
        entries = []
    entries.append(entry)
    path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")


# ── Public API ─────────────────────────────────────────────────────────────────

class AICallTimer:
    """Context manager that measures elapsed time in ms."""
    def __enter__(self):
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_):
        self.duration_ms = round((time.perf_counter() - self._start) * 1000)


def log_category_batch(
    *,
    marketplace: str,
    provider: str,
    model: str,
    products: list[dict],          # [{"id":..., "title":..., "description":...}]
    category_list: list[str],
    prompt: str,
    raw_response: str,
    parsed: dict,                  # {str_index: category_or_None}
    results: dict,                 # {offer_id: category_or_None}
    duration_ms: int,
    accepted: int,
    rejected: int,
    max_tokens: int,
):
    entry = {
        "timestamp":   datetime.now().isoformat(timespec="milliseconds"),
        "type":        "category_batch",
        "provider":    provider,
        "model":       model,
        "marketplace": marketplace,
        "duration_ms": duration_ms,
        "request": {
            "batch_size":      len(products),
            "max_tokens":      max_tokens,
            "categories_count": len(category_list),
            "products": [
                {
                    "offer_id":    p.get("id", ""),
                    "title":       str(p.get("title", ""))[:200],
                    "description": str(p.get("description", ""))[:100],
                }
                for p in products
            ],
            "prompt": prompt,
        },
        "response": {
            "raw":    raw_response,
            "parsed": parsed,
        },
        "results": {
            str(k): v for k, v in results.items()
        },
        "stats": {
            "accepted": accepted,
            "rejected": rejected,
        },
    }
    try:
        _append_entry(entry)
    except Exception:
        pass


def log_char_enrichment(
    *,
    marketplace: str,
    provider: str,
    model: str,
    offer_id: str,
    title: str,
    category: str,
    existing_chars: dict,
    missing_chars: dict,           # {char_name: set_of_valid_values}
    prompt: str,
    raw_response: str,
    parsed: dict,
    validated: dict,
    duration_ms: int,
    max_tokens: int,
    rejection_details: dict | None = None,  # {char_name: {reason, llm_value, top_k, ...}}
    review_flags: dict | None = None,       # {char_name: {value, score, top_k, ...}} for accepted-but-flagged
):
    # Strip internal keys from validated before logging as accepted
    accepted_clean = {k: v for k, v in validated.items() if not k.startswith("_")}
    rejected_clean = {k: v for k, v in parsed.items() if not k.startswith("_") and k not in accepted_clean}

    entry = {
        "timestamp":   datetime.now().isoformat(timespec="milliseconds"),
        "type":        "char_enrichment",
        "provider":    provider,
        "model":       model,
        "marketplace": marketplace,
        "duration_ms": duration_ms,
        "request": {
            "offer_id":    offer_id,
            "title":       str(title)[:200],
            "category":    category,
            "max_tokens":  max_tokens,
            "existing_characteristics": existing_chars,
            "missing_characteristics": {
                k: sorted(v)[:20] if v else []
                for k, v in missing_chars.items()
            },
            "prompt": prompt,
        },
        "response": {
            "raw":    raw_response,
            "parsed": parsed,
        },
        "results": {
            "accepted": accepted_clean,
            "rejected": rejected_clean,
            "accepted_count": len(accepted_clean),
            "rejected_count": len(rejected_clean),
            "rejection_details": rejection_details or {},
            "review_flags": review_flags or {},
        },
    }
    try:
        _append_entry(entry)
    except Exception:
        pass


def log_image_analysis(
    *,
    offer_id: str,
    marketplace: str,
    image_url: str,
    enable_color: bool,
    enable_product_hint: bool,
    result: dict,           # ImageAnalysisResult.to_dict()
):
    entry = {
        "timestamp":   datetime.now().isoformat(timespec="milliseconds"),
        "type":        "image_analysis",
        "marketplace": marketplace,
        "offer_id":    offer_id,
        "request": {
            "image_url":           image_url,
            "enable_color":        enable_color,
            "enable_product_hint": enable_product_hint,
        },
        "response": {
            "download_success":           result.get("download_success"),
            "download_error":             result.get("download_error", ""),
            "dominant_color_raw":         result.get("dominant_color_raw", ""),
            "dominant_color_normalized":  result.get("dominant_color_normalized", ""),
            "secondary_color_normalized": result.get("secondary_color_normalized", ""),
            "color_confidence":           result.get("color_confidence", 0.0),
            "is_multicolor":              result.get("is_multicolor", False),
            "product_type_hint":          result.get("product_type_hint", ""),
            "product_type_confidence":    result.get("product_type_confidence", 0.0),
        },
        "results": {
            "suggested_attributes":  result.get("suggested_attributes", {}),
            "used_for_attribute_fill": result.get("used_for_attribute_fill", False),
            "needs_review":          result.get("needs_review", False),
            "review_reason":         result.get("review_reason", ""),
            "skipped_reason":        result.get("skipped_reason", ""),
        },
    }
    try:
        _append_entry(entry)
    except Exception:
        pass


def log_char_source_detail(
    *,
    offer_id: str,
    marketplace: str,
    title: str,
    category: str,
    char_entries: list[dict],
):
    """Log per-characteristic source + validation detail.

    Each entry in char_entries must contain:
      char_name, source (rule|ai|image), value,
      allowed_values_count (int), validation_pass (bool)
    """
    if not char_entries:
        return
    entry = {
        "timestamp":   datetime.now().isoformat(timespec="milliseconds"),
        "type":        "char_source_detail",
        "marketplace": marketplace,
        "offer_id":    offer_id,
        "title":       str(title)[:200],
        "category":    category,
        "chars":       char_entries,
        "stats": {
            "total":           len(char_entries),
            "rule":            sum(1 for e in char_entries if e.get("source") == "rule"),
            "ai":              sum(1 for e in char_entries if e.get("source") == "ai"),
            "image":           sum(1 for e in char_entries if e.get("source") == "image"),
            "validation_pass": sum(1 for e in char_entries if e.get("validation_pass")),
            "validation_fail": sum(1 for e in char_entries if not e.get("validation_pass")),
        },
    }
    try:
        _append_entry(entry)
    except Exception:
        pass


# ── Read helpers (for diagnostic page) ────────────────────────────────────────

def list_ai_log_files() -> list[Path]:
    """Return available AI log files sorted newest first."""
    if not AI_LOGS_DIR.exists():
        return []
    return sorted(AI_LOGS_DIR.glob("*.json"), reverse=True)


def read_ai_log(path: Path) -> list[dict]:
    """Read all entries from a log file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []


def write_run_to_duckdb(
    *,
    run_id: str,
    ean: str | None,
    offer_id: str | None,
    marketplace: str,
    model_used: str,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    fields_requested: int,
    fields_accepted: int,
    fields_rejected: int,
    retry_count: int,
    fallback_used: bool,
    duration_ms: int,
    structured_mode: str = "off",
    structured_attempted: bool = False,
    structured_success: bool = False,
    structured_fallback_used: bool = False,
    structured_latency_ms: int = 0,
    structured_model_used: str = "",
    schema_fields_count: int = 0,
    shadow_diff: dict | None = None,
) -> None:
    """Scrie telemetry în ai_run_log DuckDB. Silent fail dacă DuckDB nu e disponibil."""
    try:
        from core.reference_store_duckdb import write_ai_run_log
        write_ai_run_log(
            run_id=run_id, ean=ean, offer_id=offer_id,
            marketplace=marketplace, model_used=model_used,
            tokens_input=tokens_input, tokens_output=tokens_output,
            cost_usd=cost_usd, fields_requested=fields_requested,
            fields_accepted=fields_accepted, fields_rejected=fields_rejected,
            retry_count=retry_count, fallback_used=fallback_used,
            duration_ms=duration_ms,
            structured_mode=structured_mode,
            structured_attempted=structured_attempted,
            structured_success=structured_success,
            structured_fallback_used=structured_fallback_used,
            structured_latency_ms=structured_latency_ms,
            structured_model_used=structured_model_used,
            schema_fields_count=schema_fields_count,
            shadow_diff=shadow_diff,
        )
    except Exception:
        pass  # Telemetry nu blochează niciodată procesarea
