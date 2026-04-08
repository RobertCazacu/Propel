"""
Migrate all Parquet-backed marketplace data into DuckDB.

Usage:
    python scripts/migrate_parquet_to_duckdb.py [--dry-run]

Options:
    --dry-run   Load Parquet and validate only, don't write to DuckDB.

Idempotent: safe to run multiple times. Existing DuckDB data is replaced.
"""
from __future__ import annotations

import sys
import io
import argparse
from pathlib import Path

# Force UTF-8 output so Unicode characters render correctly on Windows
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
else:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

# Add project root to path so core.* imports work
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from core.app_logger import get_logger
from core import reference_store_duckdb as ddb
from core.loader import MarketplaceData

log = get_logger("migration")

DATA_DIR = Path(__file__).parent.parent / "data"


def _load_known_names() -> set[str]:
    """Return all known marketplace display names (predefined + custom)."""
    known = {"eMAG Romania", "Trendyol", "Allegro", "FashionDays"}
    custom_file = DATA_DIR / "custom_marketplaces.json"
    if custom_file.exists():
        try:
            import json
            known.update(json.loads(custom_file.read_text(encoding="utf-8")))
        except Exception:
            pass
    return known


def find_parquet_marketplaces() -> list[tuple[str, Path]]:
    """Detect marketplace folders that contain the 3 required Parquet files.

    Warns when a discovered name is not in any known registry (predefined or custom).
    These marketplaces will be migrated to DuckDB but won't be auto-loaded by
    init_state() unless added to PREDEFINED_MARKETPLACES or custom_marketplaces.json.
    """
    known = _load_known_names()
    found = []
    if not DATA_DIR.exists():
        return found
    for folder in sorted(DATA_DIR.iterdir()):
        if not folder.is_dir():
            continue
        required = ["categories.parquet", "characteristics.parquet", "values.parquet"]
        if all((folder / f).exists() for f in required):
            # Reverse the folder → display name transformation (underscore back to space)
            display_name = folder.name.replace("_", " ")
            if display_name not in known:
                print(
                    f"  ⚠️  WARNING: '{display_name}' (folder: {folder.name}) is NOT in any "
                    f"marketplace registry.\n"
                    f"     Data will be migrated to DuckDB under mp_id='{ddb.marketplace_id_slug(display_name)}'.\n"
                    f"     To auto-load it, add '{display_name}' to data/custom_marketplaces.json.\n"
                )
            found.append((display_name, folder))
    return found


def migrate_one(display_name: str, folder: Path, dry_run: bool) -> dict:
    """Migrate a single marketplace. Returns a result dict."""
    mp_id = ddb.marketplace_id_slug(display_name)
    result = {"name": display_name, "mp_id": mp_id, "status": "pending", "details": ""}

    try:
        mp = MarketplaceData(display_name)
        if not mp.load_from_disk(folder):
            result["status"] = "skip"
            result["details"] = "load_from_disk returned False (empty or corrupt files)"
            return result

        stats_parquet = mp.stats()

        if dry_run:
            result["status"] = "dry_run"
            result["details"] = (
                f"Would import: {stats_parquet['categories']} cats, "
                f"{stats_parquet['characteristics']} chars, "
                f"{stats_parquet['values']} vals"
            )
            return result

        # Register + import
        ddb.init_db(ddb.DB_PATH)
        ddb.ensure_marketplace(ddb.DB_PATH, mp_id, display_name)

        sources = {
            "categories":      str(folder / "categories.parquet"),
            "characteristics": str(folder / "characteristics.parquet"),
            "values":          str(folder / "values.parquet"),
        }
        run_id = ddb.import_marketplace(
            mp_id,
            mp.categories,
            mp.characteristics,
            mp.values,
            "migration",
            sources,
        )

        summary = ddb.get_import_summary(run_id)

        # Verify counts
        issues = []
        if summary["categories"] != stats_parquet["categories"]:
            issues.append(
                f"categories mismatch: parquet={stats_parquet['categories']}, "
                f"duckdb={summary['categories']}"
            )
        # Values count may differ slightly (empty rows are excluded in DuckDB import)
        # so we only flag >5% divergence
        pq_vals = stats_parquet["values"]
        db_vals = summary["values"]
        if pq_vals > 0 and abs(db_vals - pq_vals) / pq_vals > 0.05:
            issues.append(f"values diverge >5%: parquet={pq_vals}, duckdb={db_vals}")

        if issues:
            result["status"] = "warning"
            result["details"] = "; ".join(issues)
        else:
            result["status"] = "ok"
            result["details"] = (
                f"{summary['categories']} cats, "
                f"{summary['characteristics']} chars, "
                f"{summary['values']:,} vals"
            )

    except Exception as exc:
        result["status"] = "error"
        result["details"] = str(exc)
        log.error("Migration failed for %s: %s", display_name, exc, exc_info=True)

    return result


def main():
    parser = argparse.ArgumentParser(description="Migrate Parquet data to DuckDB")
    parser.add_argument("--dry-run", action="store_true", help="Validate only, no writes")
    args = parser.parse_args()

    print(f"\n{'DRY RUN — ' if args.dry_run else ''}Migrare Parquet → DuckDB\n{'='*50}")

    marketplaces = find_parquet_marketplaces()
    if not marketplaces:
        print("Nu s-au găsit marketplace-uri cu fișiere Parquet.")
        return

    results = []
    for display_name, folder in marketplaces:
        print(f"\n→ {display_name} ({folder.name}) ...", end=" ", flush=True)
        r = migrate_one(display_name, folder, dry_run=args.dry_run)
        results.append(r)
        status_icons = {"ok": "✅", "warning": "⚠️", "error": "❌", "skip": "⏭", "dry_run": "🔍"}
        print(f"{status_icons.get(r['status'], '?')} {r['details']}")

    print(f"\n{'='*50}")
    ok      = sum(1 for r in results if r["status"] == "ok")
    warn    = sum(1 for r in results if r["status"] == "warning")
    errors  = sum(1 for r in results if r["status"] == "error")
    skipped = sum(1 for r in results if r["status"] == "skip")
    print(f"Rezultat: {ok} OK, {warn} warnings, {errors} erori, {skipped} sărite")

    if errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
