# DuckDB eMAG HU Pilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrarea stratului de stocare a datelor de referință pentru `eMAG HU` de la Parquet la DuckDB local, fără a modifica procesarea, AI enrichment sau exportul.

**Architecture:** Se creează un modul nou `core/reference_store_duckdb.py` care gestionează tot ce ține de DuckDB (schema, import, validare, read). `state.py` face lazy import al modulului și branching pe `eMAG HU` la init. `setup.py` apelează `_do_save_duckdb()` în loc de `_do_save()` pentru `eMAG HU`. `MarketplaceData` rămâne neschimbat — datele sunt încărcate via `load_from_dataframes()`.

**Tech Stack:** Python 3.11+, DuckDB >= 0.10.0, pandas, Streamlit, pytest

---

## File Map

| Acțiune | Fișier | Responsabilitate |
|---------|--------|-----------------|
| Creare | `core/reference_store_duckdb.py` | Schema DDL, init_db, import, validare, read API |
| Creare | `tests/test_reference_store_duckdb.py` | Teste pentru modulul DuckDB |
| Modificare | `requirements.txt` | Adăugare duckdb>=0.10.0 |
| Modificare | `core/state.py` | DUCKDB_MARKETPLACES + branching lazy în init_state() |
| Modificare | `pages/setup.py` | _do_save_duckdb() + branching UI pentru eMAG HU |

**Neschimbate:** `core/loader.py`, `core/processor.py`, `core/ai_enricher.py`, `core/exporter.py`, `pages/process.py`, `pages/results.py`

---

## Task 1: Adaugă dependința duckdb

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Adaugă duckdb în requirements.txt**

Adaugă pe o linie nouă după `numpy>=1.24.0`:
```
duckdb>=0.10.0
```

- [ ] **Step 2: Instalează dependința**

```bash
pip install duckdb>=0.10.0
```
Expected: instalare fără erori

- [ ] **Step 3: Verifică importul**

```bash
python -c "import duckdb; print(duckdb.__version__)"
```
Expected: printează versiunea (ex: `0.10.x` sau mai mare)

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "feat: add duckdb dependency for eMAG HU pilot"
```

---

## Task 2: Creează scheletul modulului + init_db + is_available

**Files:**
- Create: `core/reference_store_duckdb.py`
- Create: `tests/test_reference_store_duckdb.py`

- [ ] **Step 1: Creează fișierul de test cu primele teste (failing)**

Creează `tests/test_reference_store_duckdb.py`:

```python
"""
Tests for core/reference_store_duckdb.py
Run with: pytest tests/test_reference_store_duckdb.py -v
"""
import tempfile
from pathlib import Path
import pytest
import pandas as pd


@pytest.fixture
def tmp_db(tmp_path):
    """Returns a Path for a temp DuckDB file."""
    return tmp_path / "test_reference.duckdb"


def test_init_db_creates_file(tmp_db):
    from core.reference_store_duckdb import init_db
    assert not tmp_db.exists()
    init_db(tmp_db)
    assert tmp_db.exists()


def test_init_db_creates_all_tables(tmp_db):
    import duckdb
    from core.reference_store_duckdb import init_db
    init_db(tmp_db)
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        tables = {row[0] for row in con.execute("SHOW TABLES").fetchall()}
    expected = {"marketplaces", "categories", "characteristics",
                "characteristic_values", "import_runs", "import_issues"}
    assert expected.issubset(tables)


def test_init_db_upserts_emag_hu_marketplace(tmp_db):
    import duckdb
    from core.reference_store_duckdb import init_db, EMAG_HU_ID
    init_db(tmp_db)
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        row = con.execute(
            "SELECT marketplace_id, storage_backend FROM marketplaces WHERE marketplace_id=?",
            [EMAG_HU_ID]
        ).fetchone()
    assert row is not None
    assert row[0] == EMAG_HU_ID
    assert row[1] == "duckdb"


def test_init_db_idempotent(tmp_db):
    """Calling init_db twice should not raise."""
    from core.reference_store_duckdb import init_db
    init_db(tmp_db)
    init_db(tmp_db)  # should not raise


def test_is_available_false_when_no_db(tmp_db):
    from core.reference_store_duckdb import is_available
    assert not is_available("emag_hu", tmp_db)


def test_is_available_false_after_init_only(tmp_db):
    """init_db alone (no import) → is_available should return False."""
    from core.reference_store_duckdb import init_db, is_available
    init_db(tmp_db)
    assert not is_available("emag_hu", tmp_db)
```

- [ ] **Step 2: Rulează testele — verifică că eșuează cu ModuleNotFoundError**

```bash
cd C:\Users\manue\Desktop\marketplace_tool
python -m pytest tests/test_reference_store_duckdb.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'core.reference_store_duckdb'`

- [ ] **Step 3: Creează `core/reference_store_duckdb.py` — constante + DDL + init_db + is_available**

```python
"""
DuckDB reference store — pilot pentru eMAG HU.

Gestionează schema, importul, validarea și citirea datelor de referință
(categories, characteristics, values) din DuckDB local.

Folosit EXCLUSIV pentru eMAG HU. Celelalte marketplace-uri continuă cu Parquet.
"""
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import duckdb
import pandas as pd

from core.app_logger import get_logger

log = get_logger("marketplace.duckdb")

# ── Constante ──────────────────────────────────────────────────────────────────
EMAG_HU_ID   = "emag_hu"
EMAG_HU_NAME = "eMAG HU"

# Path absolut anchored la modul — nu relativ la cwd
DB_PATH = Path(__file__).parent.parent / "data" / "reference_data.duckdb"

# ── DDL ────────────────────────────────────────────────────────────────────────
_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS marketplaces (
        marketplace_id   VARCHAR PRIMARY KEY,
        marketplace_name VARCHAR NOT NULL,
        storage_backend  VARCHAR NOT NULL,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS categories (
        marketplace_id     VARCHAR NOT NULL,
        category_id        VARCHAR NOT NULL,
        emag_id            VARCHAR,
        category_name      VARCHAR NOT NULL,
        parent_category_id VARCHAR,
        import_run_id      VARCHAR,
        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS characteristics (
        marketplace_id      VARCHAR NOT NULL,
        characteristic_id   VARCHAR NOT NULL,
        category_id         VARCHAR NOT NULL,
        characteristic_name VARCHAR NOT NULL,
        mandatory           BOOLEAN NOT NULL DEFAULT FALSE,
        import_run_id       VARCHAR,
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS characteristic_values (
        marketplace_id      VARCHAR NOT NULL,
        category_id         VARCHAR,
        characteristic_id   VARCHAR,
        characteristic_name VARCHAR,
        value               VARCHAR NOT NULL,
        import_run_id       VARCHAR,
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_runs (
        import_run_id          VARCHAR PRIMARY KEY,
        marketplace_id         VARCHAR NOT NULL,
        source_type            VARCHAR NOT NULL,
        categories_source      VARCHAR,
        characteristics_source VARCHAR,
        values_source          VARCHAR,
        status                 VARCHAR NOT NULL,
        created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at           TIMESTAMP,
        notes                  VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_issues (
        issue_id       VARCHAR PRIMARY KEY,
        import_run_id  VARCHAR NOT NULL,
        marketplace_id VARCHAR NOT NULL,
        severity       VARCHAR NOT NULL,
        issue_type     VARCHAR NOT NULL,
        entity_type    VARCHAR,
        entity_id      VARCHAR,
        message        VARCHAR NOT NULL,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

_UPSERT_MARKETPLACE = """
    INSERT INTO marketplaces (marketplace_id, marketplace_name, storage_backend, is_active)
    VALUES (?, ?, 'duckdb', TRUE)
    ON CONFLICT (marketplace_id) DO UPDATE SET
        marketplace_name = excluded.marketplace_name,
        storage_backend  = excluded.storage_backend,
        is_active        = excluded.is_active
"""


def init_db(db_path: Path = DB_PATH) -> None:
    """
    Inițializează fișierul DB: creează directorul, tabelele și
    înregistrarea eMAG HU în tabela marketplaces (upsert idempotent).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        for ddl in _DDL_STATEMENTS:
            con.execute(ddl)
        con.execute(_UPSERT_MARKETPLACE, [EMAG_HU_ID, EMAG_HU_NAME])
    log.info("DuckDB inițializat: %s", db_path)


def is_available(marketplace_id: str = EMAG_HU_ID, db_path: Path = DB_PATH) -> bool:
    """
    Returnează True dacă:
    1. Fișierul DB există
    2. Există cel puțin un import_run completed pentru marketplace
    3. Există cel puțin un rând în categories pentru marketplace
    """
    if not db_path.exists():
        return False
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            run_count = con.execute(
                "SELECT COUNT(*) FROM import_runs WHERE marketplace_id=? AND status='completed'",
                [marketplace_id],
            ).fetchone()[0]
            if run_count == 0:
                return False
            cat_count = con.execute(
                "SELECT COUNT(*) FROM categories WHERE marketplace_id=?",
                [marketplace_id],
            ).fetchone()[0]
            return cat_count > 0
    except Exception as exc:
        log.warning("is_available check failed for %s: %s", marketplace_id, exc)
        return False
```

- [ ] **Step 4: Rulează testele — verifică că trec**

```bash
python -m pytest tests/test_reference_store_duckdb.py::test_init_db_creates_file \
                 tests/test_reference_store_duckdb.py::test_init_db_creates_all_tables \
                 tests/test_reference_store_duckdb.py::test_init_db_upserts_emag_hu_marketplace \
                 tests/test_reference_store_duckdb.py::test_init_db_idempotent \
                 tests/test_reference_store_duckdb.py::test_is_available_false_when_no_db \
                 tests/test_reference_store_duckdb.py::test_is_available_false_after_init_only \
                 -v
```
Expected: toate 6 PASS

- [ ] **Step 5: Commit**

```bash
git add core/reference_store_duckdb.py tests/test_reference_store_duckdb.py requirements.txt
git commit -m "feat: add DuckDB reference store — init_db and is_available"
```

---

## Task 3: Implementează _enrich_values_robust

**Files:**
- Modify: `core/reference_store_duckdb.py`
- Modify: `tests/test_reference_store_duckdb.py`

Această funcție rezolvă bug-ul din `loader.py` unde enrichment-ul se aplica doar dacă TOATĂ coloana `category_id` era goală.

- [ ] **Step 1: Adaugă testul pentru enrichment (failing)**

Adaugă în `tests/test_reference_store_duckdb.py`:

```python
def test_enrich_values_fills_missing_category_id():
    """Rows with null category_id but valid characteristic_id get filled."""
    from core.reference_store_duckdb import _enrich_values_robust

    chars = pd.DataFrame({
        "id":          ["10", "20"],
        "category_id": ["cat1", "cat2"],
        "name":        ["Culoare", "Marime"],
        "mandatory":   [True, False],
    })
    vals = pd.DataFrame({
        "category_id":        [None, "cat2", None],
        "characteristic_id":  ["10",  "20",  "10"],
        "characteristic_name": [None,  "Marime", None],
        "value":              ["Rosu", "M",   "Albastru"],
    })

    result = _enrich_values_robust(vals, chars)

    assert result.iloc[0]["category_id"] == "cat1"
    assert result.iloc[0]["characteristic_name"] == "Culoare"
    assert result.iloc[1]["category_id"] == "cat2"   # unchanged
    assert result.iloc[2]["category_id"] == "cat1"


def test_enrich_values_leaves_existing_category_id_intact():
    """Rows that already have category_id must not be overwritten."""
    from core.reference_store_duckdb import _enrich_values_robust

    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["catX"],
        "name":        ["Culoare"],
        "mandatory":   [True],
    })
    vals = pd.DataFrame({
        "category_id":        ["catY"],   # existing, different from chars
        "characteristic_id":  ["10"],
        "characteristic_name": ["Culoare"],
        "value":              ["Rosu"],
    })

    result = _enrich_values_robust(vals, chars)
    assert result.iloc[0]["category_id"] == "catY"  # must stay catY
```

- [ ] **Step 2: Rulează testele — verifică că eșuează**

```bash
python -m pytest tests/test_reference_store_duckdb.py::test_enrich_values_fills_missing_category_id -v
```
Expected: `ImportError` sau `AttributeError` — funcția nu există

- [ ] **Step 3: Implementează `_enrich_values_robust` în `core/reference_store_duckdb.py`**

Adaugă după `is_available()`:

```python
def _enrich_values_robust(
    vals: pd.DataFrame,
    chars: pd.DataFrame,
) -> pd.DataFrame:
    """
    Completează category_id și characteristic_name per-rând în vals,
    pentru rândurile unde category_id lipsește dar characteristic_id există.

    Fix față de _enrich_values_with_chars din loader.py care se activează
    doar când TOATĂ coloana category_id este goală.
    """
    vals = vals.copy()

    # Mask: rânduri care au nevoie de enrichment
    needs_enrich = vals["category_id"].isna() & vals["characteristic_id"].notna()
    if not needs_enrich.any():
        return vals

    # Tabela de lookup: characteristic_id -> (category_id, characteristic_name)
    lookup = chars[["id", "category_id", "name"]].copy()
    lookup = lookup.rename(columns={"id": "_char_id", "name": "_char_name"})
    lookup["_char_id"] = lookup["_char_id"].astype(str)

    vals["characteristic_id"] = vals["characteristic_id"].astype(str)

    # Join doar pe rândurile care au nevoie
    to_enrich = vals[needs_enrich].merge(
        lookup, left_on="characteristic_id", right_on="_char_id", how="left"
    )

    # Fill category_id din join
    vals.loc[needs_enrich, "category_id"] = to_enrich["category_id_y"].values

    # Fill characteristic_name unde lipsește
    char_name_missing = needs_enrich & vals["characteristic_name"].isna()
    if char_name_missing.any():
        to_fill = vals[char_name_missing].merge(
            lookup, left_on="characteristic_id", right_on="_char_id", how="left"
        )
        vals.loc[char_name_missing, "characteristic_name"] = to_fill["_char_name"].values

    return vals
```

- [ ] **Step 4: Rulează testele — verifică că trec**

```bash
python -m pytest tests/test_reference_store_duckdb.py -k "enrich" -v
```
Expected: toate PASS

- [ ] **Step 5: Commit**

```bash
git add core/reference_store_duckdb.py tests/test_reference_store_duckdb.py
git commit -m "feat: add robust per-row value enrichment for DuckDB import"
```

---

## Task 4: Implementează _validate_and_create_issues

**Files:**
- Modify: `core/reference_store_duckdb.py`
- Modify: `tests/test_reference_store_duckdb.py`

- [ ] **Step 1: Adaugă testele pentru validare (failing)**

Adaugă în `tests/test_reference_store_duckdb.py`:

```python
def test_validate_orphan_characteristic():
    """Characteristic cu category_id care nu există în categories → orphan_characteristic warning."""
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10", "20"],
        "category_id": ["cat1", "cat_MISSING"],
        "name":        ["Culoare", "Marime"],
        "mandatory":   [False, False],
    })
    vals  = pd.DataFrame(columns=["category_id", "characteristic_id", "characteristic_name", "value"])

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    types = [i["issue_type"] for i in issues]
    assert "orphan_characteristic" in types


def test_validate_mandatory_no_values():
    """Mandatory characteristic fără valori permise → mandatory_no_values warning."""
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["cat1"],
        "name":        ["Culoare"],
        "mandatory":   [True],
    })
    vals = pd.DataFrame(columns=["category_id", "characteristic_id", "characteristic_name", "value"])

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    types = [i["issue_type"] for i in issues]
    assert "mandatory_no_values" in types


def test_validate_empty_value():
    """Valori goale → error."""
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["cat1"],
        "name":        ["Culoare"],
        "mandatory":   [False],
    })
    vals = pd.DataFrame({
        "category_id":        ["cat1"],
        "characteristic_id":  ["10"],
        "characteristic_name":["Culoare"],
        "value":              ["   "],   # whitespace only = empty
    })

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    severities = {i["severity"] for i in issues}
    types = [i["issue_type"] for i in issues]
    assert "empty_value" in types
    assert "error" in severities


def test_validate_no_issues_on_clean_data():
    """Date curate → niciun issue."""
    from core.reference_store_duckdb import _validate_and_create_issues

    cats  = pd.DataFrame({"id": ["cat1"], "emag_id": ["cat1"], "name": ["Cat1"], "parent_id": [None]})
    chars = pd.DataFrame({
        "id":          ["10"],
        "category_id": ["cat1"],
        "name":        ["Culoare"],
        "mandatory":   [True],
    })
    vals = pd.DataFrame({
        "category_id":        ["cat1"],
        "characteristic_id":  ["10"],
        "characteristic_name":["Culoare"],
        "value":              ["Rosu"],
    })

    issues = _validate_and_create_issues("run1", "emag_hu", cats, chars, vals)
    # mandatory has value → no mandatory_no_values; no empty values; no orphans
    blocking = [i for i in issues if i["issue_type"] in ("empty_value", "orphan_characteristic")]
    assert len(blocking) == 0
```

- [ ] **Step 2: Rulează testele — verifică că eșuează**

```bash
python -m pytest tests/test_reference_store_duckdb.py -k "validate" -v
```
Expected: `ImportError` sau `AttributeError`

- [ ] **Step 3: Implementează `_validate_and_create_issues` în `core/reference_store_duckdb.py`**

Adaugă după `_enrich_values_robust()`:

```python
def _validate_and_create_issues(
    import_run_id: str,
    marketplace_id: str,
    cats: pd.DataFrame,
    chars: pd.DataFrame,
    vals: pd.DataFrame,
) -> list[dict]:
    """
    Validează datele importate și returnează lista de issues.
    Nu scrie în DB — insert-ul se face în import_emag_hu().
    """
    issues: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()
    cat_ids = set(cats["id"].astype(str).dropna())

    def _issue(severity, issue_type, entity_type=None, entity_id=None, message=""):
        return {
            "issue_id":       str(uuid.uuid4()),
            "import_run_id":  import_run_id,
            "marketplace_id": marketplace_id,
            "severity":       severity,
            "issue_type":     issue_type,
            "entity_type":    entity_type,
            "entity_id":      entity_id,
            "message":        message,
            "created_at":     now,
        }

    # 1. Orphan characteristics (category_id nu există în categories)
    if not chars.empty:
        chars_cat_ids = chars["category_id"].astype(str)
        orphan_chars = chars[~chars_cat_ids.isin(cat_ids)]
        for _, row in orphan_chars.iterrows():
            issues.append(_issue(
                "warning", "orphan_characteristic", "characteristic",
                str(row.get("id", "")),
                f"Characteristic '{row.get('name', '')}' are category_id='{row.get('category_id', '')}' "
                f"care nu există în categories."
            ))

    # 2. Duplicate categories (name sau id)
    if not cats.empty:
        dup_ids   = cats[cats["id"].duplicated(keep=False)]
        dup_names = cats[cats["name"].duplicated(keep=False)]
        seen_dup_ids   = set()
        seen_dup_names = set()
        for _, row in dup_ids.iterrows():
            key = str(row["id"])
            if key not in seen_dup_ids:
                seen_dup_ids.add(key)
                issues.append(_issue("warning", "duplicate_category", "category", key,
                                     f"Category id '{key}' duplicat."))
        for _, row in dup_names.iterrows():
            key = str(row["name"])
            if key not in seen_dup_names:
                seen_dup_names.add(key)
                issues.append(_issue("warning", "duplicate_category", "category", key,
                                     f"Category name '{key}' duplicat."))

    # 3. Duplicate characteristics (category_id + name)
    if not chars.empty:
        dup_key = chars[["category_id", "name"]].astype(str)
        dup_mask = dup_key.duplicated(keep=False)
        for _, row in chars[dup_mask].iterrows():
            issues.append(_issue("warning", "duplicate_characteristic", "characteristic",
                                 str(row.get("id", "")),
                                 f"Characteristic '{row.get('name', '')}' duplicat în "
                                 f"categoria '{row.get('category_id', '')}'."))

    # 4. Mandatory characteristics fără valori permise
    if not chars.empty:
        mandatory_mask = chars["mandatory"].astype(str).isin(["True", "true", "1", "1.0", "yes"])
        mandatory_chars = chars[mandatory_mask]
        for _, row in mandatory_chars.iterrows():
            cat_id  = str(row.get("category_id", ""))
            char_nm = str(row.get("name", ""))
            has_val = (
                not vals.empty
                and not vals[
                    (vals["category_id"].astype(str) == cat_id) &
                    (vals["characteristic_name"].astype(str) == char_nm)
                ].empty
            )
            if not has_val:
                issues.append(_issue("warning", "mandatory_no_values", "characteristic",
                                     str(row.get("id", "")),
                                     f"Caracteristica obligatorie '{char_nm}' (cat '{cat_id}') "
                                     f"nu are nicio valoare permisă."))

    # 5. Values cu characteristic_name null
    if not vals.empty:
        null_char_name = vals[vals["characteristic_name"].isna()]
        for idx, row in null_char_name.iterrows():
            issues.append(_issue("warning", "null_characteristic_name", "value",
                                 str(idx),
                                 f"Valoarea '{row.get('value', '')}' nu are characteristic_name "
                                 f"(va fi ignorată la indexare)."))

    # 6. Values goale
    if not vals.empty:
        empty_mask = vals["value"].isna() | (vals["value"].astype(str).str.strip() == "")
        for idx, row in vals[empty_mask].iterrows():
            issues.append(_issue("error", "empty_value", "value", str(idx),
                                 f"Valoare goală sau null la rândul {idx}."))

    # 7. Orphan values (characteristic_name care nu există în characteristics)
    if not vals.empty and not chars.empty:
        known_char_names = set(chars["name"].astype(str).dropna())
        orphan_vals = vals[
            vals["characteristic_name"].notna() &
            ~vals["characteristic_name"].astype(str).isin(known_char_names)
        ]
        for idx, row in orphan_vals.iterrows():
            issues.append(_issue("warning", "orphan_value", "value", str(idx),
                                 f"Valoarea '{row.get('value', '')}' are characteristic_name "
                                 f"'{row.get('characteristic_name', '')}' care nu există în characteristics."))

    return issues
```

- [ ] **Step 4: Rulează testele — verifică că trec**

```bash
python -m pytest tests/test_reference_store_duckdb.py -k "validate" -v
```
Expected: toate PASS

- [ ] **Step 5: Commit**

```bash
git add core/reference_store_duckdb.py tests/test_reference_store_duckdb.py
git commit -m "feat: add validation logic for DuckDB import"
```

---

## Task 5: Implementează import_emag_hu + get_import_summary + get_issues

**Files:**
- Modify: `core/reference_store_duckdb.py`
- Modify: `tests/test_reference_store_duckdb.py`

- [ ] **Step 1: Adaugă testele de import (failing)**

Adaugă în `tests/test_reference_store_duckdb.py`:

```python
@pytest.fixture
def sample_data():
    cats = pd.DataFrame({
        "id":        ["cat1", "cat2"],
        "emag_id":   ["cat1", "cat2"],
        "name":      ["Tricouri", "Pantaloni"],
        "parent_id": [None, None],
    })
    chars = pd.DataFrame({
        "id":          ["10", "20"],
        "category_id": ["cat1", "cat1"],
        "name":        ["Culoare", "Marime"],
        "mandatory":   [True, False],
    })
    vals = pd.DataFrame({
        "category_id":        ["cat1", "cat1", "cat1"],
        "characteristic_id":  ["10",   "10",   "20"],
        "characteristic_name":["Culoare", "Culoare", "Marime"],
        "value":              ["Rosu", "Albastru", "M"],
    })
    return cats, chars, vals


def test_import_emag_hu_returns_run_id(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu
    cats, chars, vals = sample_data
    init_db(tmp_db)
    run_id = import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    assert isinstance(run_id, str) and len(run_id) > 0


def test_import_emag_hu_sets_is_available(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu, is_available
    cats, chars, vals = sample_data
    init_db(tmp_db)
    assert not is_available("emag_hu", tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    assert is_available("emag_hu", tmp_db)


def test_import_emag_hu_stores_correct_counts(tmp_db, sample_data):
    import duckdb
    from core.reference_store_duckdb import init_db, import_emag_hu, get_import_summary
    cats, chars, vals = sample_data
    init_db(tmp_db)
    run_id = import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    summary = get_import_summary(run_id, db_path=tmp_db)
    assert summary["categories"] == 2
    assert summary["characteristics"] == 2
    assert summary["values"] == 3


def test_import_emag_hu_is_idempotent(tmp_db, sample_data):
    """Re-import should replace old data, not accumulate."""
    import duckdb
    from core.reference_store_duckdb import init_db, import_emag_hu
    cats, chars, vals = sample_data
    init_db(tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    with duckdb.connect(str(tmp_db), read_only=True) as con:
        count = con.execute("SELECT COUNT(*) FROM categories WHERE marketplace_id='emag_hu'").fetchone()[0]
    assert count == 2  # not 4


def test_get_issues_returns_list(tmp_db, sample_data):
    from core.reference_store_duckdb import init_db, import_emag_hu, get_issues
    cats, chars, vals = sample_data
    init_db(tmp_db)
    run_id = import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)
    issues = get_issues(run_id, db_path=tmp_db)
    assert isinstance(issues, list)
```

- [ ] **Step 2: Rulează testele — verifică că eșuează**

```bash
python -m pytest tests/test_reference_store_duckdb.py -k "import" -v
```
Expected: `ImportError` sau `AttributeError`

- [ ] **Step 3: Implementează `import_emag_hu`, `get_import_summary`, `get_issues` în `core/reference_store_duckdb.py`**

Adaugă după `_validate_and_create_issues()`:

```python
def import_emag_hu(
    cats_df: pd.DataFrame,
    chars_df: pd.DataFrame,
    vals_df: pd.DataFrame,
    source_type: str,
    sources: dict,
    db_path: Path = DB_PATH,
) -> str:
    """
    Importă date pentru eMAG HU în DuckDB.

    Pași:
    1. Creează import_run (status=started)
    2. Enrich values per-rând
    3. Validare → colectare issues
    4. BEGIN TRANSACTION: delete old data + insert new data + insert issues
    5. Update import_run → completed
    Pe excepție: update import_run → failed, re-raise.

    Returns: import_run_id
    """
    import_run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    con = duckdb.connect(str(db_path))
    try:
        # 1. Create import_run
        con.execute(
            """
            INSERT INTO import_runs
              (import_run_id, marketplace_id, source_type,
               categories_source, characteristics_source, values_source,
               status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'started', ?)
            """,
            [
                import_run_id, EMAG_HU_ID, source_type,
                sources.get("categories"), sources.get("characteristics"), sources.get("values"),
                now,
            ],
        )

        # 2. Enrich values per-rând
        vals_enriched = _enrich_values_robust(vals_df, chars_df)

        # 3. Validate (before touching existing data)
        issues = _validate_and_create_issues(
            import_run_id, EMAG_HU_ID, cats_df, chars_df, vals_enriched
        )
        log.info(
            "Import eMAG HU: %d categorii, %d caracteristici, %d valori, %d issues",
            len(cats_df), len(chars_df), len(vals_enriched), len(issues),
        )

        # 4. Transaction: delete old + insert new
        con.execute("BEGIN")
        try:
            # Delete old data
            for table in ("categories", "characteristics", "characteristic_values"):
                con.execute(f"DELETE FROM {table} WHERE marketplace_id=?", [EMAG_HU_ID])

            # Insert categories
            for _, row in cats_df.iterrows():
                con.execute(
                    """
                    INSERT INTO categories
                      (marketplace_id, category_id, emag_id, category_name, parent_category_id, import_run_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        EMAG_HU_ID,
                        str(row.get("id", "") or ""),
                        str(row.get("emag_id", "") or ""),
                        str(row.get("name", "") or ""),
                        str(row.get("parent_id", "") or "") or None,
                        import_run_id,
                    ],
                )

            # Insert characteristics
            mandatory_truthy = {"1", "true", "True", "yes", "1.0"}
            for _, row in chars_df.iterrows():
                mandatory = str(row.get("mandatory", "0")) in mandatory_truthy
                con.execute(
                    """
                    INSERT INTO characteristics
                      (marketplace_id, characteristic_id, category_id,
                       characteristic_name, mandatory, import_run_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        EMAG_HU_ID,
                        str(row.get("id", "") or ""),
                        str(row.get("category_id", "") or ""),
                        str(row.get("name", "") or ""),
                        mandatory,
                        import_run_id,
                    ],
                )

            # Insert values
            for _, row in vals_enriched.iterrows():
                value = str(row.get("value", "") or "").strip()
                if not value:
                    continue  # empty values skipped (already flagged in issues)
                con.execute(
                    """
                    INSERT INTO characteristic_values
                      (marketplace_id, category_id, characteristic_id,
                       characteristic_name, value, import_run_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        EMAG_HU_ID,
                        str(row.get("category_id", "") or "") or None,
                        str(row.get("characteristic_id", "") or "") or None,
                        str(row.get("characteristic_name", "") or "") or None,
                        value,
                        import_run_id,
                    ],
                )

            # Insert issues
            for iss in issues:
                con.execute(
                    """
                    INSERT INTO import_issues
                      (issue_id, import_run_id, marketplace_id, severity,
                       issue_type, entity_type, entity_id, message, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        iss["issue_id"], iss["import_run_id"], iss["marketplace_id"],
                        iss["severity"], iss["issue_type"], iss["entity_type"],
                        iss["entity_id"], iss["message"], iss["created_at"],
                    ],
                )

            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        # 5. Mark completed
        con.execute(
            "UPDATE import_runs SET status='completed', completed_at=? WHERE import_run_id=?",
            [datetime.now(timezone.utc), import_run_id],
        )
        log.info("Import eMAG HU completat. run_id=%s", import_run_id)
        return import_run_id

    except Exception as exc:
        try:
            con.execute(
                "UPDATE import_runs SET status='failed', notes=? WHERE import_run_id=?",
                [str(exc), import_run_id],
            )
        except Exception:
            pass
        log.error("Import eMAG HU eșuat: %s", exc, exc_info=True)
        raise
    finally:
        con.close()


def get_import_summary(import_run_id: str, db_path: Path = DB_PATH) -> dict:
    """Returnează statistici pentru un import_run."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        run = con.execute(
            "SELECT status, notes FROM import_runs WHERE import_run_id=?",
            [import_run_id]
        ).fetchone()
        cats  = con.execute(
            "SELECT COUNT(*) FROM categories WHERE import_run_id=?", [import_run_id]
        ).fetchone()[0]
        chars = con.execute(
            "SELECT COUNT(*) FROM characteristics WHERE import_run_id=?", [import_run_id]
        ).fetchone()[0]
        vals  = con.execute(
            "SELECT COUNT(*) FROM characteristic_values WHERE import_run_id=?", [import_run_id]
        ).fetchone()[0]
        warnings = con.execute(
            "SELECT COUNT(*) FROM import_issues WHERE import_run_id=? AND severity='warning'",
            [import_run_id]
        ).fetchone()[0]
        errors = con.execute(
            "SELECT COUNT(*) FROM import_issues WHERE import_run_id=? AND severity='error'",
            [import_run_id]
        ).fetchone()[0]
    return {
        "categories":      cats,
        "characteristics": chars,
        "values":          vals,
        "warnings":        warnings,
        "errors":          errors,
        "status":          run[0] if run else "unknown",
        "notes":           run[1] if run else None,
    }


def get_issues(import_run_id: str, db_path: Path = DB_PATH) -> list[dict]:
    """Returnează lista de issues pentru un import_run."""
    with duckdb.connect(str(db_path), read_only=True) as con:
        rows = con.execute(
            """
            SELECT issue_id, severity, issue_type, entity_type, entity_id, message
            FROM import_issues
            WHERE import_run_id=?
            ORDER BY severity DESC, issue_type
            """,
            [import_run_id],
        ).fetchall()
    return [
        {
            "issue_id":    r[0],
            "severity":    r[1],
            "issue_type":  r[2],
            "entity_type": r[3],
            "entity_id":   r[4],
            "message":     r[5],
        }
        for r in rows
    ]
```

- [ ] **Step 4: Rulează testele de import**

```bash
python -m pytest tests/test_reference_store_duckdb.py -k "import or summary or issues" -v
```
Expected: toate PASS

- [ ] **Step 5: Commit**

```bash
git add core/reference_store_duckdb.py tests/test_reference_store_duckdb.py
git commit -m "feat: add import_emag_hu, get_import_summary, get_issues"
```

---

## Task 6: Implementează load_marketplace_data

**Files:**
- Modify: `core/reference_store_duckdb.py`
- Modify: `tests/test_reference_store_duckdb.py`

Aceasta este funcția cea mai critică pentru corectitudine — aliasurile de coloane trebuie să fie exacte pentru compatibilitatea cu `_build_indexes()`.

- [ ] **Step 1: Adaugă testele de load (failing)**

Adaugă în `tests/test_reference_store_duckdb.py`:

```python
def test_load_marketplace_data_column_names(tmp_db, sample_data):
    """Coloanele returnate trebuie să fie exact cele așteptate de MarketplaceData.load_from_dataframes."""
    from core.reference_store_duckdb import init_db, import_emag_hu, load_marketplace_data
    cats, chars, vals = sample_data
    init_db(tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)

    cats_r, chars_r, vals_r = load_marketplace_data("emag_hu", tmp_db)

    # Categories: loader._build_indexes expects: id, emag_id, name, parent_id
    assert set(cats_r.columns) >= {"id", "emag_id", "name"}

    # Characteristics: _build_indexes expects: id, category_id, name, mandatory
    assert set(chars_r.columns) >= {"id", "category_id", "name", "mandatory"}

    # Values: _build_indexes expects: category_id, characteristic_name, value
    assert set(vals_r.columns) >= {"category_id", "characteristic_name", "value"}


def test_load_marketplace_data_integration_with_marketplace_data(tmp_db, sample_data):
    """End-to-end: după import + load, metodele publice ale MarketplaceData funcționează."""
    from core.reference_store_duckdb import init_db, import_emag_hu, load_marketplace_data
    from core.loader import MarketplaceData

    cats, chars, vals = sample_data
    init_db(tmp_db)
    import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp_db)

    cats_r, chars_r, vals_r = load_marketplace_data("emag_hu", tmp_db)
    mp = MarketplaceData("eMAG HU")
    mp.load_from_dataframes(cats_r, chars_r, vals_r)

    assert mp.is_loaded()
    assert mp.category_id("Tricouri") is not None
    assert mp.category_name(mp.category_id("Tricouri")) == "Tricouri"
    assert "Culoare" in mp.mandatory_chars(mp.category_id("Tricouri"))
    assert "Rosu" in mp.valid_values(mp.category_id("Tricouri"), "Culoare")
    assert mp.has_char(mp.category_id("Tricouri"), "Culoare")
    assert "Tricouri" in mp.category_list()
    stats = mp.stats()
    assert stats["categories"] == 2
```

- [ ] **Step 2: Rulează testele — verifică că eșuează**

```bash
python -m pytest tests/test_reference_store_duckdb.py -k "load" -v
```
Expected: `ImportError` sau `AttributeError`

- [ ] **Step 3: Implementează `load_marketplace_data` în `core/reference_store_duckdb.py`**

Adaugă după `get_issues()`:

```python
def load_marketplace_data(
    marketplace_id: str = EMAG_HU_ID,
    db_path: Path = DB_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Citește datele din DuckDB și le returnează în formatul așteptat de
    MarketplaceData.load_from_dataframes() → _build_indexes().

    Aliasuri obligatorii (coloana DB → coloana DataFrame):
      categories: category_name→name, parent_category_id→parent_id, category_id→id
      characteristics: characteristic_name→name, characteristic_id→id
      characteristic_values: coloane neschimbate
    """
    with duckdb.connect(str(db_path), read_only=True) as con:
        cats = con.execute(
            """
            SELECT
                category_id        AS id,
                emag_id,
                category_name      AS name,
                parent_category_id AS parent_id
            FROM categories
            WHERE marketplace_id = ?
            """,
            [marketplace_id],
        ).df()

        chars = con.execute(
            """
            SELECT
                characteristic_id   AS id,
                category_id,
                characteristic_name AS name,
                mandatory
            FROM characteristics
            WHERE marketplace_id = ?
            """,
            [marketplace_id],
        ).df()

        vals = con.execute(
            """
            SELECT
                category_id,
                characteristic_id,
                characteristic_name,
                value
            FROM characteristic_values
            WHERE marketplace_id = ?
            """,
            [marketplace_id],
        ).df()

    log.info(
        "Loaded from DuckDB [%s]: %d cats, %d chars, %d vals",
        marketplace_id, len(cats), len(chars), len(vals),
    )
    return cats, chars, vals
```

- [ ] **Step 4: Rulează toate testele**

```bash
python -m pytest tests/test_reference_store_duckdb.py -v
```
Expected: toate PASS

- [ ] **Step 5: Verificare syntax**

```bash
python -m py_compile core/reference_store_duckdb.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add core/reference_store_duckdb.py tests/test_reference_store_duckdb.py
git commit -m "feat: add load_marketplace_data with correct column aliases"
```

---

## Task 7: Modifică state.py — branching eMAG HU la init_state

**Files:**
- Modify: `core/state.py`

- [ ] **Step 1: Citește `core/state.py` pentru a înțelege structura curentă**

Citește liniile 125-156 (funcția `init_state()`).

- [ ] **Step 2: Adaugă `DUCKDB_MARKETPLACES` și modifică `init_state()`**

Localizează în `core/state.py` linia cu `PREDEFINED_MARKETPLACES = [...]` (linia ~48).
Adaugă imediat după:

```python
# Marketplace-uri care folosesc DuckDB ca backend de stocare (pilot controlat)
DUCKDB_MARKETPLACES = {"eMAG HU"}
```

Localizează bucla de auto-load în `init_state()` (liniile ~150-155):

```python
    # Auto-load any previously saved marketplace data
    for mp_name in PREDEFINED_MARKETPLACES + st.session_state.get("custom_mp_names", []):
        if mp_name not in st.session_state["marketplaces"]:
            mp = MarketplaceData(mp_name)
            folder = DATA_DIR / mp_name.replace(" ", "_")
            if mp.load_from_disk(folder):
                st.session_state["marketplaces"][mp_name] = mp
```

Înlocuiește cu:

```python
    # Auto-load any previously saved marketplace data
    for mp_name in PREDEFINED_MARKETPLACES + st.session_state.get("custom_mp_names", []):
        if mp_name not in st.session_state["marketplaces"]:
            if mp_name in DUCKDB_MARKETPLACES:
                # eMAG HU pilot: load din DuckDB în loc de Parquet
                try:
                    from core import reference_store_duckdb as _duckdb_store
                    if _duckdb_store.is_available(_duckdb_store.EMAG_HU_ID):
                        cats, chars, vals = _duckdb_store.load_marketplace_data(
                            _duckdb_store.EMAG_HU_ID
                        )
                        mp = MarketplaceData(mp_name)
                        mp.load_from_dataframes(cats, chars, vals)
                        st.session_state["marketplaces"][mp_name] = mp
                        log.info("Loaded %s from DuckDB", mp_name)
                except Exception as exc:
                    log.warning("DuckDB load failed for %s: %s", mp_name, exc)
                continue  # nu face load_from_disk parquet pentru eMAG HU
            mp = MarketplaceData(mp_name)
            folder = DATA_DIR / mp_name.replace(" ", "_")
            if mp.load_from_disk(folder):
                st.session_state["marketplaces"][mp_name] = mp
```

Notă: `log` există deja în `state.py`? Dacă nu, adaugă `from core.app_logger import get_logger` și `log = get_logger("marketplace.state")` în secțiunea de imports.

- [ ] **Step 3: Verifică că `state.py` compilează**

```bash
python -m py_compile core/state.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add core/state.py
git commit -m "feat: add DuckDB branching in init_state for eMAG HU"
```

---

## Task 8: Modifică setup.py — _do_save_duckdb + branching UI

**Files:**
- Modify: `pages/setup.py`

- [ ] **Step 1: Citește `pages/setup.py` pentru a înțelege structura curentă**

Citește întreg fișierul (liniile 1-268).

- [ ] **Step 2: Adaugă funcția `_do_save_duckdb` după `_do_save`**

Localizează funcția `_do_save` (liniile ~12-33) și adaugă imediat după ea:

```python
def _do_save_duckdb(selected: str, cat_src, char_src, val_src, source_type: str = "upload"):
    """
    Versiunea DuckDB a lui _do_save, folosită exclusiv pentru eMAG HU pilot.
    Parsează fișierele identic cu _do_save, dar persistă în DuckDB.
    """
    from core import reference_store_duckdb as duckdb_store
    from core.loader import load_categories, load_characteristics, load_values

    with st.spinner("Se procesează și se salvează în DuckDB..."):
        try:
            cats  = load_categories(cat_src)
            chars = load_characteristics(char_src)
            vals  = load_values(val_src)

            duckdb_store.init_db(duckdb_store.DB_PATH)

            sources = {
                "categories":      getattr(cat_src,  "name", str(cat_src)),
                "characteristics": getattr(char_src, "name", str(char_src)),
                "values":          getattr(val_src,  "name", str(val_src)),
            }
            run_id = duckdb_store.import_emag_hu(cats, chars, vals, source_type, sources)

            # Reload din DuckDB (sursa de adevăr după import)
            cats2, chars2, vals2 = duckdb_store.load_marketplace_data(duckdb_store.EMAG_HU_ID)
            mp_new = MarketplaceData(selected)
            mp_new.load_from_dataframes(cats2, chars2, vals2)

            # Set direct în session state (NU prin set_marketplace care scrie Parquet)
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

            if errors_list:
                for iss in errors_list:
                    st.error(f"❌ [{iss['issue_type']}] {iss['message']}")
            if warnings_list:
                with st.expander(f"⚠️ {len(warnings_list)} warning-uri la import"):
                    for iss in warnings_list:
                        st.warning(f"[{iss['issue_type']}] {iss['message']}")

            st.rerun()
        except Exception as e:
            st.error(f"Eroare la import DuckDB: {e}")
```

- [ ] **Step 3: Adaugă branching în `render()` — badge DuckDB + apel `_do_save_duckdb`**

Localizează secțiunea `if not (mp and mp.is_loaded()) or st.session_state.get(f"_reload_{selected}"):` (linia ~71).

Adaugă imediat înainte de aceasta (după blocul `if mp and mp.is_loaded():` care afișează status-ul), și înainte de `if not (mp and mp.is_loaded())...`:

```python
    # ── Badge DuckDB pilot (vizibil tot timpul pentru eMAG HU) ─────────────────
    if selected == "eMAG HU":
        st.info("🦆 **Pilot DuckDB** — datele pentru acest marketplace sunt stocate în DuckDB local (`data/reference_data.duckdb`).")
```

Localizează în `tab_upload` (linia ~137-140):
```python
            if cat_file and char_file and val_file:
                if st.button(f"💾 Salvează datele pentru {selected}", type="primary",
                             use_container_width=True, key=f"save_upload_{selected}"):
                    _do_save(selected, cat_file, char_file, val_file)
```

Înlocuiește cu:
```python
            if cat_file and char_file and val_file:
                if st.button(f"💾 Salvează datele pentru {selected}", type="primary",
                             use_container_width=True, key=f"save_upload_{selected}"):
                    if selected == "eMAG HU":
                        _do_save_duckdb(selected, cat_file, char_file, val_file, source_type="upload")
                    else:
                        _do_save(selected, cat_file, char_file, val_file)
```

Localizează în `tab_local` (linia ~182-184):
```python
            if all_paths_filled and paths_ok:
                if st.button(f"💾 Salvează datele pentru {selected}", type="primary",
                             use_container_width=True, key=f"save_local_{selected}"):
                    _do_save(selected, cat_path.strip(), char_path.strip(), val_path.strip())
```

Înlocuiește cu:
```python
            if all_paths_filled and paths_ok:
                if st.button(f"💾 Salvează datele pentru {selected}", type="primary",
                             use_container_width=True, key=f"save_local_{selected}"):
                    if selected == "eMAG HU":
                        _do_save_duckdb(selected, cat_path.strip(), char_path.strip(),
                                        val_path.strip(), source_type="local_path")
                    else:
                        _do_save(selected, cat_path.strip(), char_path.strip(), val_path.strip())
```

- [ ] **Step 4: Verifică că `setup.py` compilează**

```bash
python -m py_compile pages/setup.py && echo "OK"
```
Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add pages/setup.py
git commit -m "feat: add DuckDB save path and pilot UI for eMAG HU in setup"
```

---

## Task 9: Verificare finală + smoke test

**Files:**
- Verify: toate fișierele modificate

- [ ] **Step 1: Verifică syntax pe toate fișierele modificate**

```bash
python -m py_compile core/reference_store_duckdb.py core/state.py pages/setup.py && echo "All OK"
```
Expected: `All OK`

- [ ] **Step 2: Rulează toate testele**

```bash
python -m pytest tests/test_reference_store_duckdb.py -v
```
Expected: toate testele PASS, 0 failed

- [ ] **Step 3: Rulează smoke test end-to-end**

```bash
python - <<'EOF'
import sys, os
sys.path.insert(0, os.getcwd())

import pandas as pd
import tempfile
from pathlib import Path
from core.reference_store_duckdb import (
    init_db, import_emag_hu, load_marketplace_data,
    is_available, get_import_summary, EMAG_HU_ID
)
from core.loader import MarketplaceData

# Temp DB
import tempfile
tmp = Path(tempfile.mkdtemp()) / "test.duckdb"

# Sample data
cats  = pd.DataFrame({"id": ["1"], "emag_id": ["1"], "name": ["Tricouri"], "parent_id": [None]})
chars = pd.DataFrame({"id": ["10"], "category_id": ["1"], "name": ["Culoare"], "mandatory": [True]})
vals  = pd.DataFrame({"category_id": ["1"], "characteristic_id": ["10"], "characteristic_name": ["Culoare"], "value": ["Rosu"]})

# Init + import
init_db(tmp)
assert not is_available(EMAG_HU_ID, tmp)
run_id = import_emag_hu(cats, chars, vals, "upload", {}, db_path=tmp)
assert is_available(EMAG_HU_ID, tmp)

# Load + MarketplaceData
c, ch, v = load_marketplace_data(EMAG_HU_ID, tmp)
mp = MarketplaceData("eMAG HU")
mp.load_from_dataframes(c, ch, v)

assert mp.is_loaded(), "is_loaded() failed"
cat_id = mp.category_id("Tricouri")
assert cat_id is not None, "category_id() returned None"
assert mp.category_name(cat_id) == "Tricouri", "category_name() failed"
assert "Culoare" in mp.mandatory_chars(cat_id), "mandatory_chars() failed"
assert "Rosu" in mp.valid_values(cat_id, "Culoare"), "valid_values() failed"
assert mp.has_char(cat_id, "Culoare"), "has_char() failed"
assert "Tricouri" in mp.category_list(), "category_list() failed"
stats = mp.stats()
assert stats["categories"] == 1
assert stats["values"] == 1

summary = get_import_summary(run_id, db_path=tmp)
assert summary["categories"] == 1
assert summary["characteristics"] == 1

print("Smoke test PASSED - toate verificarile au trecut!")
EOF
```
Expected: `Smoke test PASSED - toate verificarile au trecut!`

- [ ] **Step 4: Verifică că celelalte marketplace-uri nu sunt afectate**

```bash
python - <<'EOF'
# Verifică că state.py și loader.py pot fi importate fără erori
import sys, os
sys.path.insert(0, os.getcwd())
from core.loader import MarketplaceData
from pathlib import Path

# eMAG Romania (parquet) trebuie să funcționeze în continuare
mp = MarketplaceData("eMAG Romania")
folder = Path("data/eMAG_Romania")
if folder.exists():
    ok = mp.load_from_disk(folder)
    print(f"eMAG Romania load_from_disk: {'OK' if ok else 'SKIP (no data)'}")
else:
    print("eMAG Romania: folder lipsă (skip)")

print("Import state.py: OK")
import importlib.util
spec = importlib.util.spec_from_file_location("state", "core/state.py")
print("state.py syntax: OK")
EOF
```
Expected: niciun error

- [ ] **Step 5: Commit final**

```bash
git add .
git commit -m "feat: DuckDB pilot eMAG HU — implementation complete

- core/reference_store_duckdb.py: init, import, validate, load
- core/state.py: lazy DuckDB branching for eMAG HU
- pages/setup.py: _do_save_duckdb + pilot UI badge
- tests/test_reference_store_duckdb.py: test suite

Pilot constraints respected:
- Only eMAG HU uses DuckDB
- Processing, AI enrichment, export unchanged
- MarketplaceData public API unchanged"
```

---

## Checklist final

- [ ] `duckdb>=0.10.0` adăugat în `requirements.txt`
- [ ] `core/reference_store_duckdb.py` creat cu toate funcțiile
- [ ] `core/state.py` — branching lazy pentru eMAG HU în `init_state()`
- [ ] `pages/setup.py` — `_do_save_duckdb()` + branching + badge DuckDB
- [ ] Toate testele din `tests/test_reference_store_duckdb.py` trec
- [ ] Smoke test end-to-end PASSED
- [ ] `core/processor.py` — NESCHIMBAT
- [ ] `pages/process.py` — NESCHIMBAT
- [ ] Celelalte marketplace-uri — funcționează cu Parquet ca înainte
