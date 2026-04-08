# DuckDB Universal Backend Migration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate all marketplaces (predefined + custom) from split Parquet/DuckDB storage to DuckDB as the single source of truth, with a feature flag for controlled rollout and backward-compatible Parquet fallback.

**Architecture:** Every marketplace gets a deterministic `marketplace_id` slug generated at runtime from its display name. The feature flag `REFERENCE_BACKEND` (`duckdb`|`parquet`|`dual`) controls where data is read/written. `init_state()` and `setup.py` consult the flag and route accordingly. Parquet code stays intact but becomes a secondary path gated by the flag.

**Tech Stack:** Python 3.10+, DuckDB, pandas, Streamlit, pytest

---

## File Map

| File | Role | Change type |
|------|------|-------------|
| `core/reference_store_duckdb.py` | DuckDB backend — import, load, query | Modify |
| `core/state.py` | Session-state init + marketplace registry | Modify |
| `pages/setup.py` | UI for file upload + persistence | Modify |
| `core/loader.py` | Parquet persistence helpers | Modify (deprecation markers only) |
| `tests/test_reference_store_duckdb.py` | Unit + integration tests | Modify |
| `scripts/migrate_parquet_to_duckdb.py` | One-shot migration utility | Create |

---

## Baseline

Current test status (run before starting, to confirm baseline):

```bash
pytest tests/test_reference_store_duckdb.py -q --tb=no
# Expected: 7 failed, 12 passed
```

Failing tests — ALL 7 are caused by the same `_norm_id_series` bug (`sample_data` uses `"cat1"`, `"cat2"` IDs):
- `test_import_emag_hu_returns_run_id`
- `test_import_emag_hu_sets_is_available`
- `test_import_emag_hu_stores_correct_counts`
- `test_import_emag_hu_is_idempotent`
- `test_get_issues_returns_list`
- `test_load_marketplace_data_column_names`
- `test_load_marketplace_data_integration_with_marketplace_data`

---

## Task 1 — Fix `_norm_id_series` (PAS 1)

**Files:**
- Modify: `core/reference_store_duckdb.py:419-424`

### Context

`_norm_id_series` crashes on alphanumeric IDs (e.g. `"cat1"`) because the lambda calls `float(x)` unconditionally. The module already has a correct `_norm_id` helper that handles this with try/except and falls back to `str(x).strip()`.

### Steps

- [ ] **Step 1: Write the failing test (already exists — verify it fails)**

```bash
pytest tests/test_reference_store_duckdb.py::test_import_emag_hu_is_idempotent -q --tb=line
# Expected: FAILED with "ValueError: could not convert string to float: 'cat1'"
```

- [ ] **Step 2: Replace `_norm_id_series` lambda with `_norm_id` call**

In `core/reference_store_duckdb.py`, replace lines 419-424:

```python
# OLD (crashes on alphanumeric):
def _norm_id_series(s: pd.Series) -> pd.Series:
    """Normalize float IDs: 2819.0 → '2819' (avoids spurious '.0' in VARCHAR fields)."""
    return s.apply(
        lambda x: str(int(float(x))) if pd.notna(x) and str(x).strip() not in ("", "nan", "None")
        else str(x) if pd.notna(x) else None
    )
```

```python
# NEW (handles both numeric and alphanumeric):
def _norm_id_series(s: pd.Series) -> pd.Series:
    """Normalize IDs for VARCHAR storage.

    Numeric-like: 2819.0 → '2819'
    Alphanumeric: 'cat-001' → 'cat-001' (preserved as-is)
    Empty/null  : → None (SQL NULL)
    """
    normed = s.apply(_norm_id)
    return normed.where(normed != "", None)
```

- [ ] **Step 3: Run tests to verify fix**

```bash
pytest tests/test_reference_store_duckdb.py -q --tb=no
# Expected: 0 failed, 19 passed
# All 7 failures were caused by the same bug; fixing it clears all of them.
```

- [ ] **Step 4: Commit**

```bash
git add core/reference_store_duckdb.py
git commit -m "fix: _norm_id_series preserves alphanumeric IDs (no float crash)"
```

---

## Task 2 — Add `marketplace_id_slug` + `ensure_marketplace` (PAS 2)

**Files:**
- Modify: `core/reference_store_duckdb.py` (add 2 functions, update `init_db`)

### Context

`DUCKDB_ID_MAP` is a hardcoded dict `{"eMAG HU": "emag_hu", "Allegro": "allegro"}`. Any new marketplace requires code changes. We need a deterministic slug generator and a function that registers any marketplace in the DB on demand.

Note: The slug of existing marketplaces MUST match their current IDs:
- `"eMAG HU"` → `"emag_hu"` ✓
- `"Allegro"` → `"allegro"` ✓

### Steps

- [ ] **Step 1: Write tests for `marketplace_id_slug`**

Append to `tests/test_reference_store_duckdb.py`:

```python
# ── Task 2: marketplace_id_slug ───────────────────────────────────────────────

def test_slug_known_marketplaces():
    from core.reference_store_duckdb import marketplace_id_slug
    assert marketplace_id_slug("eMAG HU")      == "emag_hu"
    assert marketplace_id_slug("Allegro")       == "allegro"
    assert marketplace_id_slug("eMAG Romania")  == "emag_romania"
    assert marketplace_id_slug("Trendyol")      == "trendyol"
    assert marketplace_id_slug("FashionDays")   == "fashiondays"


def test_slug_custom_marketplaces():
    from core.reference_store_duckdb import marketplace_id_slug
    assert marketplace_id_slug("My Custom MP") == "my_custom_mp"
    assert marketplace_id_slug("cat-001 Store") == "cat_001_store"
    assert marketplace_id_slug("  Spaces  ")   == "spaces"


def test_ensure_marketplace_registers_new(tmp_db):
    import duckdb
    from core.reference_store_duckdb import init_db, ensure_marketplace
    init_db(tmp_db)
    mp_id = ensure_marketplace(tmp_db, "my_custom_mp", "My Custom MP")
    assert mp_id == "my_custom_mp"
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        row = con.execute(
            "SELECT marketplace_name FROM marketplaces WHERE marketplace_id=?",
            ["my_custom_mp"],
        ).fetchone()
    assert row is not None
    assert row[0] == "My Custom MP"


def test_ensure_marketplace_idempotent(tmp_db):
    from core.reference_store_duckdb import init_db, ensure_marketplace
    init_db(tmp_db)
    ensure_marketplace(tmp_db, "test_mp", "Test MP")
    ensure_marketplace(tmp_db, "test_mp", "Test MP")  # must not raise or duplicate
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_reference_store_duckdb.py -k "slug or ensure_marketplace" -q --tb=line
# Expected: ImportError / AttributeError (functions don't exist yet)
```

- [ ] **Step 3: Add `import re` to the top-level imports of `reference_store_duckdb.py`**

At the top of the file, with the other standard library imports (`uuid`, `re`, `Path`, etc.):

```python
import re
```

(Do NOT add it inline next to the function — that violates PEP 8 E402.)

- [ ] **Step 4: Add `marketplace_id_slug` and `ensure_marketplace` to `reference_store_duckdb.py`**

After the `_norm_id` helper (around line 184), add:

```python
def marketplace_id_slug(name: str) -> str:
    """Generate a deterministic, safe VARCHAR marketplace_id from a display name.

    'eMAG HU'      → 'emag_hu'   (matches EMAG_HU_ID — no data migration needed)
    'Allegro'      → 'allegro'   (matches ALLEGRO_ID)
    'eMAG Romania' → 'emag_romania'
    'My Custom MP' → 'my_custom_mp'
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "marketplace"


def ensure_marketplace(db_path: Path, marketplace_id: str, marketplace_name: str) -> str:
    """Upsert marketplace metadata into the DB.  Returns marketplace_id.

    Safe to call multiple times (idempotent).
    Requires the DB to be initialised first (call init_db once).
    """
    with duckdb.connect(str(db_path)) as con:
        con.execute(_UPSERT_MARKETPLACE, [marketplace_id, marketplace_name])
    return marketplace_id
```

- [ ] **Step 5: Update `init_db` to not hardcode the DUCKDB_ID_MAP loop**

Replace the `for mp_name, mp_id in DUCKDB_ID_MAP.items(): con.execute(...)` loop with:

```python
# Register known pilot marketplaces (backward compat).
# New marketplaces are registered on-demand via ensure_marketplace().
for mp_id, mp_name in [(EMAG_HU_ID, EMAG_HU_NAME), (ALLEGRO_ID, ALLEGRO_NAME)]:
    con.execute(_UPSERT_MARKETPLACE, [mp_id, mp_name])
```

**IMPORTANT:** Keep `DUCKDB_ID_MAP` in the module for now — it is still referenced by `setup.py` and `state.py` until Task 4. Only remove it after Task 4 replaces all call sites.

- [ ] **Step 6: Run new tests**

```bash
pytest tests/test_reference_store_duckdb.py -k "slug or ensure_marketplace" -q --tb=short
# Expected: 4 passed
```

- [ ] **Step 7: Run full suite (no regressions)**

```bash
pytest tests/test_reference_store_duckdb.py -q --tb=no
# Expected: 0 failed, 23 passed
```

- [ ] **Step 8: Commit**

```bash
git add core/reference_store_duckdb.py tests/test_reference_store_duckdb.py
git commit -m "feat: marketplace_id_slug + ensure_marketplace for dynamic registration"
```

---

## Task 3 — Feature Flag + Updated `state.py` (PAS 3 + PAS 4)

**Files:**
- Modify: `core/state.py`

### Context

`state.py` has two hardcoded sets: `PREDEFINED_MARKETPLACES` (4 names) and `DUCKDB_MARKETPLACES = {"eMAG HU", "Allegro"}`. The init loop branches on `if mp_name in DUCKDB_MARKETPLACES:`, so every new marketplace needs manual code changes.

We will:
1. Add `get_backend()` → reads `REFERENCE_BACKEND` env var, default `"duckdb"`.
2. Remove `DUCKDB_MARKETPLACES` (keep as deprecated alias pointing to empty set to avoid import errors).
3. Rewrite `init_state()`: for each marketplace, if backend includes `duckdb`, try DuckDB first; if that fails or backend is `parquet`, try Parquet.
4. Update `clear_marketplace_data()` to always attempt DuckDB clear (not gated on DUCKDB_MARKETPLACES).

### Steps

- [ ] **Step 1: Write tests for `get_backend`**

Create a new test file `tests/test_state_backend.py`:

```python
"""Tests for state.py backend flag.

get_backend() reads os.environ at CALL time, not at import time,
so no importlib.reload() is needed.
"""
import os


def test_get_backend_default(monkeypatch):
    monkeypatch.delenv("REFERENCE_BACKEND", raising=False)
    from core.state import get_backend
    assert get_backend() == "duckdb"


def test_get_backend_parquet(monkeypatch):
    monkeypatch.setenv("REFERENCE_BACKEND", "parquet")
    from core.state import get_backend
    assert get_backend() == "parquet"


def test_get_backend_dual(monkeypatch):
    monkeypatch.setenv("REFERENCE_BACKEND", "dual")
    from core.state import get_backend
    assert get_backend() == "dual"
```

- [ ] **Step 2: Run to confirm FAIL**

```bash
pytest tests/test_state_backend.py -q --tb=line
# Expected: ImportError (get_backend doesn't exist)
```

- [ ] **Step 3: Add `get_backend` to `state.py`**

After the imports, before `PREDEFINED_MARKETPLACES`, add:

```python
import os

def get_backend() -> str:
    """Return the configured storage backend.

    Reads REFERENCE_BACKEND environment variable.
    Valid values: 'duckdb' (default), 'parquet', 'dual'.
    'dual' writes to both DuckDB and Parquet, reads from DuckDB.
    """
    return os.environ.get("REFERENCE_BACKEND", "duckdb").lower()
```

- [ ] **Step 4: Deprecate `DUCKDB_MARKETPLACES`**

Replace the existing assignment:
```python
DUCKDB_MARKETPLACES = {"eMAG HU", "Allegro"}
```
with:
```python
# DEPRECATED: all marketplaces now use DuckDB when REFERENCE_BACKEND=duckdb (default).
# Kept for backward compatibility with any import that references this symbol.
DUCKDB_MARKETPLACES: set = set()
```

- [ ] **Step 5: Rewrite `init_state()` auto-load block**

Replace lines 156-174 (the for loop) with:

```python
    # Auto-load any previously saved marketplace data.
    # Note: "eMAG HU" is NOT in PREDEFINED_MARKETPLACES — it lives in custom_mp_names
    # (loaded from data/custom_marketplaces.json). This is unchanged from the current code.
    # Both lists are iterated together so the logic below covers all known marketplaces.
    backend = get_backend()
    for mp_name in PREDEFINED_MARKETPLACES + st.session_state.get("custom_mp_names", []):
        if mp_name in st.session_state["marketplaces"]:
            continue

        loaded = False

        # ── Try DuckDB first (if backend is duckdb or dual) ──────────────────
        if backend in ("duckdb", "dual"):
            try:
                from core import reference_store_duckdb as _ddb
                mp_id = _ddb.marketplace_id_slug(mp_name)
                if _ddb.is_available(mp_id):
                    cats, chars, vals = _ddb.load_marketplace_data(mp_id)
                    mp = MarketplaceData(mp_name)
                    mp.load_from_dataframes(cats, chars, vals)
                    st.session_state["marketplaces"][mp_name] = mp
                    log.info("Loaded %s from DuckDB (backend=%s)", mp_name, backend)
                    loaded = True
            except Exception as exc:
                log.warning("DuckDB load failed for %s: %s", mp_name, exc)

        # ── Fallback to Parquet (if not loaded from DuckDB, or backend=parquet) ──
        if not loaded and backend in ("parquet", "dual"):
            mp = MarketplaceData(mp_name)
            folder = DATA_DIR / mp_name.replace(" ", "_")
            if mp.load_from_disk(folder):
                st.session_state["marketplaces"][mp_name] = mp
                log.info("Loaded %s from Parquet (fallback, backend=%s)", mp_name, backend)
```

- [ ] **Step 6: Add deprecation docstring to `set_marketplace()` in `state.py`**

`set_marketplace()` currently writes to Parquet unconditionally. In `duckdb` mode, it is no longer called from the save flow (replaced by `_do_save_unified` in Task 4), but it remains a public API. Add a note so future callers understand the intent:

```python
def set_marketplace(name: str, mp: MarketplaceData):
    """Store a MarketplaceData in session state and persist to Parquet.

    .. deprecated::
        In REFERENCE_BACKEND=duckdb mode, persistence is handled by
        ``_do_save_unified`` in ``pages/setup.py`` via DuckDB import pipeline.
        This function still writes Parquet for REFERENCE_BACKEND=parquet|dual.
        Do not call this function when using the DuckDB backend.
    """
    st.session_state["marketplaces"][name] = mp
    # Persist to disk (Parquet — only effective in parquet/dual mode)
    folder = DATA_DIR / name.replace(" ", "_")
    mp.save_to_disk(folder)
```

- [ ] **Step 7: Update `clear_marketplace_data()` to always try DuckDB**

Replace the `if name in DUCKDB_MARKETPLACES:` block with:

```python
    # Always attempt DuckDB clear (no-op if marketplace doesn't exist in DB)
    try:
        from core import reference_store_duckdb as _ddb
        mp_id = _ddb.marketplace_id_slug(name)
        _ddb.clear_marketplace_data(mp_id)
    except Exception as exc:
        log.warning("Erro la ștergerea DuckDB pentru %s: %s", name, exc)
```

- [ ] **Step 8: Run backend tests**

```bash
pytest tests/test_state_backend.py -q --tb=short
# Expected: 3 passed
```

- [ ] **Step 9: Commit**

```bash
git add core/state.py tests/test_state_backend.py
git commit -m "feat: REFERENCE_BACKEND flag + unified DuckDB-first init_state for all marketplaces"
```

---

## Task 4 — Unified Save Flow in `setup.py` (PAS 3 + PAS 6)

**Files:**
- Modify: `pages/setup.py`

### Context

`setup.py` currently branches: if `selected in DUCKDB_MARKETPLACES` → `_do_save_duckdb`, else → `_do_save` (Parquet). The DuckDB set is now empty (`DUCKDB_MARKETPLACES = set()`), so all saves would fall to Parquet. We need a unified `_do_save_unified` that:
- Always uses DuckDB when `backend in ("duckdb", "dual")`
- Writes Parquet as well when `backend == "dual"`
- Uses Parquet only when `backend == "parquet"`

The DuckDB status panel should show for ALL marketplaces (not just DUCKDB_MARKETPLACES).

### Steps

- [ ] **Step 1: Replace `_do_save` and `_do_save_duckdb` with `_do_save_unified`**

Remove both existing functions and replace with:

```python
def _do_save_unified(selected: str, cat_src, char_src, val_src, source_type: str = "upload"):
    """Unified save function — routes to DuckDB, Parquet, or both based on REFERENCE_BACKEND."""
    from core.state import get_backend
    from core import reference_store_duckdb as duckdb_store
    from core.loader import load_categories, load_characteristics, load_values

    backend = get_backend()

    with st.spinner("Se procesează și se salvează..."):
        try:
            cats  = load_categories(cat_src)
            chars = load_characteristics(char_src)
            vals  = load_values(val_src)
        except Exception as e:
            st.error(f"❌ Eroare la parsarea fișierelor: {e}")
            return

        # ── DuckDB save ───────────────────────────────────────────────────────
        if backend in ("duckdb", "dual"):
            try:
                mp_id = duckdb_store.marketplace_id_slug(selected)
                duckdb_store.init_db(duckdb_store.DB_PATH)
                duckdb_store.ensure_marketplace(duckdb_store.DB_PATH, mp_id, selected)

                sources = {
                    "categories":      getattr(cat_src, "name", str(cat_src)),
                    "characteristics": getattr(char_src, "name", str(char_src)),
                    "values":          getattr(val_src,  "name", str(val_src)),
                }
                run_id = duckdb_store.import_marketplace(
                    mp_id, cats, chars, vals, source_type, sources
                )

                cats2, chars2, vals2 = duckdb_store.load_marketplace_data(mp_id)
                mp_new = MarketplaceData(selected)
                mp_new.load_from_dataframes(cats2, chars2, vals2)
                st.session_state["marketplaces"][selected] = mp_new
                st.session_state.pop(f"_reload_{selected}", None)

                summary = duckdb_store.get_import_summary(run_id)
                issues  = duckdb_store.get_issues(run_id)

                st.success(
                    f"✅ Date salvate în DuckDB pentru **{selected}**: "
                    f"{summary['categories']} categorii, "
                    f"{summary['characteristics']} caracteristici, "
                    f"{summary['values']:,} valori. "
                    f"({summary['warnings']} warnings, {summary['errors']} errors)"
                )

                errors_list   = [i for i in issues if i["severity"] == "error"]
                warnings_list = [i for i in issues if i["severity"] == "warning"]
                for iss in errors_list:
                    st.error(f"❌ [{iss['issue_type']}] {iss['message']}")
                if warnings_list:
                    with st.expander(f"⚠️ {len(warnings_list)} warning-uri la import"):
                        for iss in warnings_list:
                            st.warning(f"[{iss['issue_type']}] {iss['message']}")

            except Exception as e:
                st.error(f"❌ Eroare la import DuckDB: {e}")
                log.error("DuckDB save failed for %s: %s", selected, e, exc_info=True)
                return

        # ── Parquet save (dual or parquet-only) ──────────────────────────────
        if backend in ("parquet", "dual"):
            try:
                from core.state import DATA_DIR
                mp_parquet = MarketplaceData(selected)
                if backend == "dual":
                    # Files already parsed for DuckDB step above.
                    # Streamlit UploadedFile cursor is at EOF — MUST NOT re-read.
                    # Reuse the already-parsed DataFrames.
                    mp_parquet.load_from_dataframes(cats, chars, vals)
                else:
                    # parquet-only: first parse
                    mp_parquet.load_from_files(cat_src, char_src, val_src)
                folder = DATA_DIR / selected.replace(" ", "_")
                mp_parquet.save_to_disk(folder)
                if backend == "parquet":
                    st.session_state["marketplaces"][selected] = mp_parquet
                    st.session_state.pop(f"_reload_{selected}", None)
                    stats = mp_parquet.stats()
                    st.success(
                        f"✅ Date salvate (Parquet) pentru **{selected}**: "
                        f"{stats['categories']} categorii, "
                        f"{stats['characteristics']} caracteristici, "
                        f"{stats['values']:,} valori permise."
                    )
                else:
                    log.info("Dual mode: Parquet also written for %s", selected)
            except Exception as e:
                st.error(f"❌ Eroare la salvare Parquet: {e}")
                return

        st.rerun()
```

- [ ] **Step 2: Import `get_backend` and add logger at top of `setup.py`**

After the existing imports, add:

```python
from core.state import get_backend
from core.app_logger import get_logger
log = get_logger("marketplace.setup")
```

- [ ] **Step 3: Replace all call sites in `render()`**

Replace the 4 conditional save calls (lines ~208-211 and ~255-259):

```python
# OLD:
if selected in DUCKDB_MARKETPLACES:
    _do_save_duckdb(...)
else:
    _do_save(...)

# NEW (both tabs):
_do_save_unified(selected, <files>, source_type=<type>)
```

- [ ] **Step 4: Update the pilot DuckDB badge to show for all marketplaces**

Replace:
```python
# OLD:
if selected in DUCKDB_MARKETPLACES:
    st.info("🦆 **Pilot DuckDB** ...")
```
with:
```python
backend = get_backend()
if backend in ("duckdb", "dual"):
    st.info(f"🦆 **Backend: DuckDB** (`REFERENCE_BACKEND={backend}`) — `data/reference_data.duckdb`")
elif backend == "parquet":
    st.warning("⚠️ **Backend: Parquet** (`REFERENCE_BACKEND=parquet`) — date stocate local ca fișiere.")
```

- [ ] **Step 5: Update the DuckDB status panel to show for all marketplaces**

Replace:
```python
# OLD:
if selected in DUCKDB_MARKETPLACES:
    st.subheader("🦆 Status DuckDB")
    ...
    _mp_id = _ddb.DUCKDB_ID_MAP.get(selected)
```
with:
```python
if get_backend() in ("duckdb", "dual"):
    st.markdown("---")
    st.subheader("🦆 Status DuckDB")
    from core import reference_store_duckdb as _ddb
    _mp_id = _ddb.marketplace_id_slug(selected)
    db_status = _ddb.get_db_status(_mp_id)
    ...
```

- [ ] **Step 6: Smoke-test setup.py imports (no Streamlit server needed)**

```bash
python -c "import pages.setup; print('OK')"
# Expected: OK (no ImportError)
```

- [ ] **Step 7: Commit**

```bash
git add pages/setup.py
git commit -m "feat: unified save flow for all marketplaces via DuckDB (REFERENCE_BACKEND flag)"
```

---

## Task 5 — Migration Script (PAS 5)

**Files:**
- Create: `scripts/migrate_parquet_to_duckdb.py`

### Context

Existing Parquet data at `data/{name}/categories.parquet` etc. needs to be imported into DuckDB. The script must be:
- Idempotent: running twice produces the same result (import_marketplace already deletes old data before inserting)
- Safe: read-only from Parquet, verified by count comparison
- Transparent: prints a report per marketplace

### Steps

- [ ] **Step 1: Write the migration script**

Create `scripts/migrate_parquet_to_duckdb.py`:

```python
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
import argparse
from pathlib import Path

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
```

- [ ] **Step 2: Test migration dry-run**

```bash
python scripts/migrate_parquet_to_duckdb.py --dry-run
# Expected: lists all parquet marketplaces with "Would import: N cats..." lines
```

- [ ] **Step 3: Commit**

```bash
git add scripts/migrate_parquet_to_duckdb.py
git commit -m "feat: migrate_parquet_to_duckdb.py — idempotent migration utility"
```

---

## Task 6 — New Tests for Universal Backend (PAS 7)

**Files:**
- Modify: `tests/test_reference_store_duckdb.py`

### Steps

- [ ] **Step 1: Add tests for universal import + cross-marketplace isolation**

Append to `tests/test_reference_store_duckdb.py`:

```python
# ── Task 6: Universal marketplace support ─────────────────────────────────────

def test_import_marketplace_numeric_ids(tmp_db):
    """Numeric IDs (as float strings) are normalized without crash."""
    from core.reference_store_duckdb import init_db, import_marketplace, is_available
    cats = pd.DataFrame({
        "id": ["2819.0", "2820.0"], "emag_id": ["100.0", "101.0"],
        "name": ["Cat A", "Cat B"], "parent_id": [None, None],
    })
    chars = pd.DataFrame({
        "id": ["10.0", "20.0"], "category_id": ["2819.0", "2820.0"],
        "name": ["Culoare", "Marime"], "mandatory": [True, False],
    })
    vals = pd.DataFrame({
        "category_id": ["2819.0"], "characteristic_id": ["10.0"],
        "characteristic_name": ["Culoare"], "value": ["Rosu"],
    })
    init_db(tmp_db)
    run_id = import_marketplace("test_num", cats, chars, vals, "test", {}, tmp_db)
    assert run_id
    assert is_available("test_num", tmp_db)


def test_import_marketplace_alphanumeric_ids(tmp_db):
    """Alphanumeric IDs are preserved as-is without crash."""
    from core.reference_store_duckdb import init_db, import_marketplace, is_available
    cats = pd.DataFrame({
        "id": ["cat-001", "cat-002"], "emag_id": ["cat-001", "cat-002"],
        "name": ["Cat A", "Cat B"], "parent_id": [None, None],
    })
    chars = pd.DataFrame({
        "id": ["ch-A1", "ch-B2"], "category_id": ["cat-001", "cat-001"],
        "name": ["Culoare", "Marime"], "mandatory": [True, False],
    })
    vals = pd.DataFrame({
        "category_id": ["cat-001"], "characteristic_id": ["ch-A1"],
        "characteristic_name": ["Culoare"], "value": ["Rosu"],
    })
    init_db(tmp_db)
    run_id = import_marketplace("test_alpha", cats, chars, vals, "test", {}, tmp_db)
    assert run_id
    assert is_available("test_alpha", tmp_db)


def test_two_marketplaces_are_isolated(tmp_db, sample_data):
    """Data from marketplace A must not appear in marketplace B query."""
    from core.reference_store_duckdb import init_db, ensure_marketplace, import_marketplace, load_marketplace_data
    cats, chars, vals = sample_data
    init_db(tmp_db)
    ensure_marketplace(tmp_db, "mp_a", "MP A")
    ensure_marketplace(tmp_db, "mp_b", "MP B")
    import_marketplace("mp_a", cats, chars, vals, "test", {}, tmp_db)
    import_marketplace("mp_b", pd.DataFrame({"id": ["x"], "emag_id": ["x"], "name": ["X"], "parent_id": [None]}),
                       pd.DataFrame({"id": ["99"], "category_id": ["x"], "name": ["Col"], "mandatory": [False]}),
                       pd.DataFrame({"category_id": ["x"], "characteristic_id": ["99"],
                                     "characteristic_name": ["Col"], "value": ["V"]}),
                       "test", {}, tmp_db)

    cats_a, _, _ = load_marketplace_data("mp_a", tmp_db)
    cats_b, _, _ = load_marketplace_data("mp_b", tmp_db)
    assert len(cats_a) == 2
    assert len(cats_b) == 1


def test_custom_marketplace_end_to_end(tmp_db):
    """A brand-new custom marketplace goes through full lifecycle."""
    from core.reference_store_duckdb import (
        init_db, marketplace_id_slug, ensure_marketplace,
        import_marketplace, load_marketplace_data, is_available
    )
    from core.loader import MarketplaceData

    mp_name = "My Test Marketplace"
    mp_id   = marketplace_id_slug(mp_name)
    assert mp_id == "my_test_marketplace"

    cats = pd.DataFrame({"id": ["1"], "emag_id": ["1"], "name": ["Shoes"], "parent_id": [None]})
    chars = pd.DataFrame({"id": ["10"], "category_id": ["1"], "name": ["Size"], "mandatory": [True]})
    vals = pd.DataFrame({"category_id": ["1"], "characteristic_id": ["10"],
                         "characteristic_name": ["Size"], "value": ["42"]})

    init_db(tmp_db)
    ensure_marketplace(tmp_db, mp_id, mp_name)
    import_marketplace(mp_id, cats, chars, vals, "test", {}, tmp_db)

    assert is_available(mp_id, tmp_db)
    cats_r, chars_r, vals_r = load_marketplace_data(mp_id, tmp_db)
    mp = MarketplaceData(mp_name)
    mp.load_from_dataframes(cats_r, chars_r, vals_r)

    assert mp.is_loaded()
    assert "Shoes" in mp.category_list()
    cat_id = mp.category_id("Shoes")
    assert "Size" in mp.mandatory_chars(cat_id)
    assert "42" in mp.valid_values(cat_id, "Size")
```

- [ ] **Step 2: Run all tests**

```bash
pytest tests/test_reference_store_duckdb.py tests/test_state_backend.py -q --tb=short
# Expected: all pass (0 failures)
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -q --tb=no
# Expected: no regressions vs. current baseline
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_reference_store_duckdb.py
git commit -m "test: universal marketplace, numeric/alphanumeric IDs, isolation, end-to-end"
```

---

## Task 7 — Deprecation Markers in `loader.py` (PAS 8)

**Files:**
- Modify: `core/loader.py:536-567` (save_to_disk / load_from_disk methods)

### Steps

- [ ] **Step 1: Add deprecation warnings to Parquet persistence methods**

In `MarketplaceData.save_to_disk` (around line 536):

```python
def save_to_disk(self, folder: Path):
    """Save marketplace data as Parquet files.

    .. deprecated::
        Use DuckDB backend via ``core.reference_store_duckdb.import_marketplace`` instead.
        This method is preserved for ``REFERENCE_BACKEND=parquet`` and ``dual`` modes.
        Will be removed once all marketplaces are fully migrated to DuckDB.
    """
    import warnings
    warnings.warn(
        "save_to_disk is deprecated; use DuckDB backend (REFERENCE_BACKEND=duckdb).",
        DeprecationWarning,
        stacklevel=2,
    )
    folder.mkdir(parents=True, exist_ok=True)
    self.categories.to_parquet(folder / "categories.parquet")
    self.characteristics.to_parquet(folder / "characteristics.parquet")
    self.values.to_parquet(folder / "values.parquet")
```

In `MarketplaceData.load_from_disk` (around line 542):

```python
def load_from_disk(self, folder: Path) -> bool:
    """Load marketplace data from Parquet files.

    .. deprecated::
        Use DuckDB backend via ``core.reference_store_duckdb.load_marketplace_data`` instead.
        This method is preserved for ``REFERENCE_BACKEND=parquet`` and ``dual`` modes.
    """
    import warnings
    warnings.warn(
        "load_from_disk is deprecated; use DuckDB backend (REFERENCE_BACKEND=duckdb).",
        DeprecationWarning,
        stacklevel=2,
    )
    # ... existing body unchanged ...
```

- [ ] **Step 2: Verify tests still pass (deprecation warnings must not break anything)**

```bash
pytest tests/ -q --tb=no -W ignore::DeprecationWarning
# Expected: same result as before
```

- [ ] **Step 3: Commit**

```bash
git add core/loader.py
git commit -m "deprecate: save_to_disk/load_from_disk — use DuckDB backend instead"
```

---

## Task 8 — Run Migration + Final Verification

### Steps

- [ ] **Step 1: Run migration on real data**

```bash
python scripts/migrate_parquet_to_duckdb.py --dry-run
# Review output — all marketplaces listed

python scripts/migrate_parquet_to_duckdb.py
# Migrate all data to DuckDB
```

- [ ] **Step 2: Verify DuckDB has data for all migrated marketplaces**

```bash
python -c "
from core import reference_store_duckdb as ddb
import duckdb
with duckdb.connect(str(ddb.DB_PATH), read_only=True) as con:
    rows = con.execute('SELECT marketplace_id, COUNT(*) FROM categories GROUP BY 1').fetchall()
    for r in rows: print(r)
"
# Expected: each migrated marketplace shows category count
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/ -q --tb=short -W ignore::DeprecationWarning
# Expected: 0 failures
```

- [ ] **Step 4: Smoke-test Streamlit import**

```bash
python -c "
import streamlit as st
# Mock session state to avoid Streamlit runtime errors
import unittest.mock as mock
with mock.patch.object(st, 'session_state', {}):
    from core.state import init_state
    print('init_state import OK')
from pages import setup
print('setup import OK')
"
# Expected: both OK lines
```

- [ ] **Step 5: Final commit**

```bash
git add -A
git commit -m "chore: run Parquet→DuckDB migration; all marketplaces now DuckDB-backed"
```

---

## Risks & Next Steps

### Risks in this PR

| Risk | Likelihood | Mitigation |
|------|-----------|------------|
| `set_marketplace()` still writes Parquet regardless of backend | Low | Only called from `_do_save` (replaced in Task 4). Marked as deprecated in Task 3 so future callers are warned. |
| `DUCKDB_MARKETPLACES = set()` breaks code that checks `if name in DUCKDB_MARKETPLACES` | Medium | Grep before merging: `grep -r "DUCKDB_MARKETPLACES" .` — only `state.py` and `setup.py` reference it; both updated by Tasks 3+4. |
| Migration detects `FashionDays_BG` and `eMAG_BG` as display names not in any registry | High | Migration script prints an explicit warning and the DuckDB slug; user must manually add the names to `data/custom_marketplaces.json` for auto-loading to work. |
| `clear_marketplace_data()` calls `shutil.rmtree(folder)` unconditionally, deleting the Parquet backup even in `duckdb` mode | Medium | Intentional behavior change: once migrated to DuckDB, the Parquet backup is no longer needed and can be deleted. If rollback is needed, restore from version control. Documented here as a known behavior change. |
| Parquet `DeprecationWarning` breaks tests that check warning types | Low | Add `-W ignore::DeprecationWarning` to pytest, or `warnings.filterwarnings("ignore", category=DeprecationWarning)` in `conftest.py`. |

### Next PR (out of scope here)

1. Remove `save_to_disk` / `load_from_disk` entirely (after 1-2 months of DuckDB-only operation).
2. Delete Parquet folders from `data/` after confirming DuckDB is the live source.
3. Add `conftest.py` with `pytest.ini_options` to suppress DeprecationWarnings in test runs.
4. Add DuckDB health check endpoint to the Streamlit sidebar.
