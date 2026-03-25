"""
Structured JSONL logger for the vision pipeline.

Creates one JSONL file per processing run:
  data/logs/vision_runs/<run_id>.jsonl

Each line is a self-contained JSON object following the schema:
  {ts, level, run_id, offer_id, marketplace, image_url, stage, event,
   status, duration_ms, data}

Also writes a summary JSON at end of run and supports debug bundle export.
"""
from __future__ import annotations
import json
import uuid
import threading
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Optional, Any

VISION_LOGS_DIR = Path(__file__).parent.parent.parent / "data" / "logs" / "vision_runs"
_MAX_STR   = 800   # truncate strings longer than this
_MAX_ITEMS = 30    # truncate lists/dicts larger than this


# ── Truncation helper ─────────────────────────────────────────────────────────

def _trunc(v: Any, max_len: int = _MAX_STR) -> Any:
    """Recursively truncate strings/lists/dicts to keep log lines manageable."""
    if isinstance(v, str):
        return v[:max_len] + f"…[+{len(v)-max_len}]" if len(v) > max_len else v
    if isinstance(v, dict):
        items = list(v.items())[:_MAX_ITEMS]
        return {k: _trunc(val, max_len) for k, val in items}
    if isinstance(v, (list, tuple)):
        return [_trunc(i, max_len) for i in v[:_MAX_ITEMS]]
    return v


# ── Main logger class ─────────────────────────────────────────────────────────

class VisionRunLogger:
    """
    Per-run JSONL logger.  Create once at start of _process_all, pass via
    image_options["run_logger"] to every analyze_product_image call.

    Usage:
        logger = new_run_logger("eMAG Romania")
        logger.log(stage="fetch", event="cache_hit", offer_id="123", ...)
        summary_path = logger.finish()
    """

    def __init__(self, marketplace: str = "", run_id: str = ""):
        self.run_id      = run_id or _make_run_id()
        self.marketplace = marketplace
        self.started_at  = datetime.now().isoformat(timespec="milliseconds")
        self._lock       = threading.Lock()
        self._event_count = 0
        self._stats: dict[str, int] = {
            "total_images": 0,
            "fetch_ok": 0,
            "fetch_fail": 0,
            "fetch_cache_hit": 0,
            "yolo_ok": 0,
            "yolo_fallback": 0,
            "clip_ok": 0,
            "clip_fallback": 0,
            "color_ok": 0,
            "fills": 0,
            "needs_review": 0,
            "skipped": 0,
        }
        VISION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        self._jsonl_path   = VISION_LOGS_DIR / f"{self.run_id}.jsonl"
        self._summary_path = VISION_LOGS_DIR / f"{self.run_id}_summary.json"

    # ── Core log ─────────────────────────────────────────────────────────────

    def log(
        self,
        stage: str,
        event: str,
        *,
        offer_id: str = "",
        image_url: str = "",
        status: str = "ok",
        duration_ms: Optional[int] = None,
        data: Optional[dict] = None,
        level: str = "INFO",
    ) -> None:
        entry: dict = {
            "ts":          datetime.now().isoformat(timespec="milliseconds"),
            "level":       level,
            "run_id":      self.run_id,
            "offer_id":    str(offer_id),
            "marketplace": self.marketplace,
            "image_url":   (image_url or "")[:200],
            "stage":       stage,
            "event":       event,
            "status":      status,
        }
        if duration_ms is not None:
            entry["duration_ms"] = duration_ms
        if data:
            entry["data"] = _trunc(data)

        with self._lock:
            self._event_count += 1
            try:
                with self._jsonl_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            except Exception:
                pass

    def inc(self, stat_key: str, n: int = 1) -> None:
        """Increment a named counter (thread-safe)."""
        with self._lock:
            self._stats[stat_key] = self._stats.get(stat_key, 0) + n

    # ── Convenience shortcuts ─────────────────────────────────────────────────

    def info(self, stage: str, event: str, **kw) -> None:
        self.log(stage, event, level="INFO", **kw)

    def debug(self, stage: str, event: str, **kw) -> None:
        self.log(stage, event, level="DEBUG", **kw)

    def warning(self, stage: str, event: str, **kw) -> None:
        self.log(stage, event, level="WARNING", **kw)

    def error(self, stage: str, event: str, **kw) -> None:
        self.log(stage, event, level="ERROR", **kw)

    # ── Artifacts dir ─────────────────────────────────────────────────────────

    def artifacts_dir(self, offer_id: str = "") -> Path:
        """Return (and create) per-offer artifacts directory under run dir."""
        sub = offer_id if offer_id else "_misc"
        d = VISION_LOGS_DIR / self.run_id / sub
        d.mkdir(parents=True, exist_ok=True)
        return d

    # ── Finish ────────────────────────────────────────────────────────────────

    def finish(self) -> Path:
        """Write summary JSON file.  Returns the summary path."""
        summary = {
            "run_id":        self.run_id,
            "marketplace":   self.marketplace,
            "started_at":    self.started_at,
            "finished_at":   datetime.now().isoformat(timespec="milliseconds"),
            "events_count":  self._event_count,
            "stats":         dict(self._stats),
            "jsonl_path":    str(self._jsonl_path),
        }
        try:
            self._summary_path.write_text(
                json.dumps(summary, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
        return self._summary_path

    def export_debug_bundle(self, output_dir: Optional[Path] = None) -> Path:
        """ZIP: JSONL + summary + all artifacts for this run."""
        out = output_dir or VISION_LOGS_DIR
        bundle = out / f"{self.run_id}_debug.zip"
        arts   = VISION_LOGS_DIR / self.run_id
        try:
            with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in [self._jsonl_path, self._summary_path]:
                    if p.exists():
                        zf.write(p, p.name)
                if arts.exists():
                    for f in arts.rglob("*"):
                        if f.is_file():
                            zf.write(f, str(f.relative_to(arts.parent)))
        except Exception:
            pass
        return bundle

    @property
    def jsonl_path(self) -> Path:
        return self._jsonl_path

    @property
    def summary_path(self) -> Path:
        return self._summary_path


# ── Factory ───────────────────────────────────────────────────────────────────

def _make_run_id() -> str:
    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    uid = str(uuid.uuid4())[:8]
    return f"vision_{ts}_{uid}"


def new_run_logger(marketplace: str = "") -> VisionRunLogger:
    """Create a fresh VisionRunLogger for a processing run."""
    return VisionRunLogger(marketplace=marketplace)
