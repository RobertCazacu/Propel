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
MAX_AGE_HOURS = 24


# ── Cleanup ────────────────────────────────────────────────────────────────────

def _cleanup():
    """Remove log files older than MAX_AGE_HOURS."""
    if not AI_LOGS_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(hours=MAX_AGE_HOURS)
    for f in AI_LOGS_DIR.glob("*.json"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except Exception:
            pass


def _today_file() -> Path:
    AI_LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return AI_LOGS_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.json"


def _append_entry(entry: dict):
    """Append one entry to today's log file (JSON array)."""
    _cleanup()
    path = _today_file()
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
):
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
            "accepted": validated,
            "rejected": {
                k: v for k, v in parsed.items()
                if k not in validated
            },
            "accepted_count": len(validated),
            "rejected_count": len([k for k in parsed if k not in validated]),
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
