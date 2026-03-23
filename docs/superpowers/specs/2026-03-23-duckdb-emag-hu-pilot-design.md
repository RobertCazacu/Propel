# DuckDB Pilot — eMAG HU Reference Store

**Date:** 2026-03-23
**Status:** Reviewed
**Scope:** Pilot controlat — DuckDB exclusiv pentru `eMAG HU`

---

## Obiectiv

Migrarea stratului de stocare a datelor de referință (categories, characteristics, values) pentru marketplace-ul `eMAG HU` de la fișiere Parquet la DuckDB local. Toate celelalte marketplace-uri rămân neschimbate. Procesarea produselor, AI enrichment, exportul Excel și logica de detecție nu sunt modificate.

---

## Constrângeri hard

- NU se modifică `core/processor.py`, `pages/process.py`, logica AI, exportul Excel
- Metodele publice ale `MarketplaceData` rămân intacte: `category_id`, `category_name`, `mandatory_chars`, `valid_values`, `has_char`, `category_list`, `stats`, `is_loaded`
- Doar `eMAG HU` folosește DuckDB; orice alt marketplace continuă cu sistemul Parquet existent
- Fișierul DB: `data/reference_data.duckdb` (path absolut anchored la `__file__`)

---

## Arhitectura

### Fișiere noi și modificate

```
core/
  reference_store_duckdb.py   ← NOU (modul principal DuckDB)

core/state.py                 ← modificare mică: branching eMAG HU la init_state()
pages/setup.py                ← modificare mică: _do_save_duckdb() + UI DuckDB

requirements.txt              ← adăugare duckdb>=0.10.0

data/
  reference_data.duckdb       ← creat automat la primul import eMAG HU
```

### Fișiere neschimbate

```
core/loader.py
core/processor.py
core/ai_enricher.py
core/exporter.py
pages/process.py
pages/results.py
```

---

## Schema DuckDB

### `marketplaces`
```sql
CREATE TABLE IF NOT EXISTS marketplaces (
    marketplace_id   VARCHAR PRIMARY KEY,
    marketplace_name VARCHAR NOT NULL,
    storage_backend  VARCHAR NOT NULL,   -- 'duckdb' | 'parquet'
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    -- nota: nu există updated_at (câmpul ar fi neactualizat la upsert și inducere în eroare)
)
```

### `categories`
```sql
CREATE TABLE IF NOT EXISTS categories (
    marketplace_id     VARCHAR NOT NULL,
    category_id        VARCHAR NOT NULL,
    emag_id            VARCHAR,
    category_name      VARCHAR NOT NULL,
    parent_category_id VARCHAR,
    import_run_id      VARCHAR,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```
Logical PK: `(marketplace_id, category_id)`

### `characteristics`
```sql
CREATE TABLE IF NOT EXISTS characteristics (
    marketplace_id      VARCHAR NOT NULL,
    characteristic_id   VARCHAR NOT NULL,
    category_id         VARCHAR NOT NULL,
    characteristic_name VARCHAR NOT NULL,
    mandatory           BOOLEAN NOT NULL DEFAULT FALSE,
    import_run_id       VARCHAR,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```
Logical PK: `(marketplace_id, characteristic_id, category_id)`

### `characteristic_values`
```sql
CREATE TABLE IF NOT EXISTS characteristic_values (
    marketplace_id      VARCHAR NOT NULL,
    category_id         VARCHAR,
    characteristic_id   VARCHAR,
    characteristic_name VARCHAR,        -- poate fi NULL; validat la import
    value               VARCHAR NOT NULL,
    import_run_id       VARCHAR,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### `import_runs`
```sql
CREATE TABLE IF NOT EXISTS import_runs (
    import_run_id          VARCHAR PRIMARY KEY,
    marketplace_id         VARCHAR NOT NULL,
    source_type            VARCHAR NOT NULL,   -- 'upload' | 'local_path'
    categories_source      VARCHAR,
    characteristics_source VARCHAR,
    values_source          VARCHAR,
    status                 VARCHAR NOT NULL,   -- 'started'|'completed'|'failed'
    created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at           TIMESTAMP,
    notes                  VARCHAR
)
```

### `import_issues`
```sql
CREATE TABLE IF NOT EXISTS import_issues (
    issue_id       VARCHAR PRIMARY KEY,
    import_run_id  VARCHAR NOT NULL,
    marketplace_id VARCHAR NOT NULL,
    severity       VARCHAR NOT NULL,   -- 'info'|'warning'|'error'
    issue_type     VARCHAR NOT NULL,
    entity_type    VARCHAR,            -- 'category'|'characteristic'|'value'
    entity_id      VARCHAR,
    message        VARCHAR NOT NULL,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

---

## API-ul `reference_store_duckdb.py`

### Constante
```python
EMAG_HU_ID   = "emag_hu"
EMAG_HU_NAME = "eMAG HU"

# Path absolut anchored la modul — nu relativ la cwd (fix față de bare Path("data/..."))
DB_PATH = Path(__file__).parent.parent / "data" / "reference_data.duckdb"
```

### Funcții publice

#### `init_db(db_path: Path) -> None`
Creează directorul `data/` dacă nu există, creează toate tabelele (IF NOT EXISTS), face upsert pe `marketplaces` pentru `emag_hu`.

Upsert pattern DuckDB (>=0.10.0):
```sql
INSERT INTO marketplaces (marketplace_id, marketplace_name, storage_backend, is_active)
VALUES ('emag_hu', 'eMAG HU', 'duckdb', TRUE)
ON CONFLICT (marketplace_id) DO UPDATE SET
    marketplace_name = excluded.marketplace_name,
    storage_backend  = excluded.storage_backend,
    is_active        = excluded.is_active
```

#### `import_emag_hu(cats_df, chars_df, vals_df, source_type, sources) -> str`

Pași în ordine (toți în același transaction DuckDB):
1. Creează `import_run` cu status `started`
2. Enrichment values per-rând (detaliat mai jos)
3. Validare date → colectare `import_issues`
4. (**Abia după validare**) Ștergere date vechi pentru `emag_hu`
5. Inserare în `categories`, `characteristics`, `characteristic_values`
6. Inserare `import_issues` în DB
7. Update `import_run` status → `completed`, `completed_at = now()`

Pe excepție (orice punct eșuează): update `import_run` status → `failed`, `notes = str(exc)`, re-raise.

Observație: ștergerea datelor vechi (pasul 4) se face **după validare** și **în același transaction** cu inserarea noilor date, pentru atomicitate. Dacă tranzacția eșuează, datele vechi sunt păstrate intacte.

Returnează `import_run_id`.

#### `load_marketplace_data(marketplace_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]`

Returnează `(cats_df, chars_df, vals_df)` cu coloanele redenumite exact pentru compatibilitate cu `MarketplaceData.load_from_dataframes()` → `_build_indexes()`.

**Aliasuri obligatorii:**

Categories SELECT:
```sql
SELECT
    category_id        AS id,
    emag_id,
    category_name      AS name,
    parent_category_id AS parent_id
FROM categories WHERE marketplace_id = ?
```
→ cols returnate: `id`, `emag_id`, `name`, `parent_id`

Characteristics SELECT:
```sql
SELECT
    characteristic_id   AS id,
    category_id,
    characteristic_name AS name,
    mandatory
FROM characteristics WHERE marketplace_id = ?
```
→ cols returnate: `id`, `category_id`, `name`, `mandatory`

Values SELECT:
```sql
SELECT
    category_id,
    characteristic_id,
    characteristic_name,
    value
FROM characteristic_values WHERE marketplace_id = ?
```
→ cols returnate: `category_id`, `characteristic_id`, `characteristic_name`, `value`

#### `is_available(marketplace_id: str) -> bool`
Returnează `True` dacă:
1. Fișierul DB există
2. Există cel puțin un `import_run` cu status `completed` pentru marketplace
3. Există cel puțin un rând în `categories` pentru marketplace (guard împotriva DB corupt/gol)

#### `get_import_summary(import_run_id: str) -> dict`
Returnează `{categories, characteristics, values, warnings, errors, notes}`.

#### `get_issues(import_run_id: str) -> list[dict]`
Returnează lista de issues pentru afișare în UI.

---

## Enrich values (robust — fix față de comportamentul actual din loader.py)

Comportamentul actual în `loader.py` (`load_from_files`, linia 177-181):
```python
if self.values["category_id"].isna().all() and ...:
    self.values = _enrich_values_with_chars(...)
```
Problema: se activează DOAR dacă **toată** coloana `category_id` e goală — nu per-rând.

Comportamentul nou în `import_emag_hu()`:
- Aplicăm enrichment per-rând: pentru fiecare rând din `vals_df` unde `category_id` este null/NaN
- Dacă `characteristic_id` există → căutare în `chars_df` → completare `category_id` și `characteristic_name`
- Restul rândurilor (cu `category_id` deja completat) nu sunt atinse

Implementare: se reutilizează logica din `_enrich_values_with_chars` din `loader.py` cu o adaptare:
```python
# În loc de condiția is().all():
needs_enrich = vals_df["category_id"].isna() & vals_df["characteristic_id"].notna()
if needs_enrich.any():
    # join pe rândurile care au nevoie, fill category_id și characteristic_name
```
Nu se duplică logica — se importă și se adaptează `_enrich_values_with_chars` din `core.loader`.

---

## Validări (`import_issues`)

| `issue_type`               | `severity` | Condiție                                                          |
|----------------------------|------------|-------------------------------------------------------------------|
| `orphan_characteristic`    | warning    | `category_id` din characteristics nu există în categories         |
| `orphan_value`             | warning    | value cu `characteristic_name` care nu există în characteristics  |
| `mandatory_no_values`      | warning    | char obligatorie fără nicio valoare permisă în values             |
| `duplicate_category`       | warning    | name sau id duplicat în categories                                |
| `duplicate_characteristic` | warning    | (category_id, characteristic_name) duplicat în characteristics   |
| `empty_value`              | error      | value NULL sau string gol după trim                               |
| `null_characteristic_name` | warning    | rând în values cu `characteristic_name` NULL (va fi ignorat la build_indexes) |

---

## Modificări `state.py`

### Import (lazy — nu hard dependency la top-level)
```python
# DUCKDB_MARKETPLACES definit la nivel de modul
DUCKDB_MARKETPLACES = {"eMAG HU"}
```

Import-ul modulului DuckDB se face **lazy**, în interiorul branch-ului `if`, pentru a evita ca `duckdb` să fie o dependență hard pentru toți utilizatorii care nu au eMAG HU configurat:

```python
# În init_state(), în bucla de auto-load:
for mp_name in PREDEFINED_MARKETPLACES + st.session_state.get("custom_mp_names", []):
    if mp_name in DUCKDB_MARKETPLACES:
        try:
            from core import reference_store_duckdb as duckdb_store
            if duckdb_store.is_available(duckdb_store.EMAG_HU_ID):
                cats, chars, vals = duckdb_store.load_marketplace_data(duckdb_store.EMAG_HU_ID)
                mp = MarketplaceData(mp_name)
                mp.load_from_dataframes(cats, chars, vals)
                st.session_state["marketplaces"][mp_name] = mp
        except Exception as exc:
            log.warning("DuckDB load failed for %s: %s", mp_name, exc)
        continue  # sare peste load_from_disk() parquet
    # comportament actual parquet (neschimbat)
    mp = MarketplaceData(mp_name)
    folder = DATA_DIR / mp_name.replace(" ", "_")
    if mp.load_from_disk(folder):
        st.session_state["marketplaces"][mp_name] = mp
```

### `set_marketplace()` — **NESCHIMBAT**
DuckDB-ul este scris direct din `setup.py` via `_do_save_duckdb()`, nu prin `set_marketplace()`.
Session state-ul este setat direct în `_do_save_duckdb()`.

---

## Modificări `setup.py`

### Funcție nouă `_do_save_duckdb(selected, cat_src, char_src, val_src, source_type)`

```python
def _do_save_duckdb(selected, cat_src, char_src, val_src, source_type="upload"):
    from core import reference_store_duckdb as duckdb_store
    from core.loader import load_categories, load_characteristics, load_values

    with st.spinner("Se procesează și se salvează în DuckDB..."):
        try:
            cats  = load_categories(cat_src)
            chars = load_characteristics(char_src)
            vals  = load_values(val_src)

            duckdb_store.init_db(duckdb_store.DB_PATH)
            run_id = duckdb_store.import_emag_hu(cats, chars, vals, source_type, {...})

            # Reload din DuckDB (sursa de adevăr după import)
            cats2, chars2, vals2 = duckdb_store.load_marketplace_data(duckdb_store.EMAG_HU_ID)
            mp_new = MarketplaceData(selected)
            mp_new.load_from_dataframes(cats2, chars2, vals2)

            # Set direct în session state (NU prin set_marketplace() care scrie parquet)
            st.session_state["marketplaces"][selected] = mp_new
            st.session_state.pop(f"_reload_{selected}", None)  # cleanup reload flag

            summary = duckdb_store.get_import_summary(run_id)
            issues  = duckdb_store.get_issues(run_id)

            # Afișare rezumat
            st.success(
                f"✅ Date salvate în DuckDB pentru **{selected}**: "
                f"{summary['categories']} categorii, "
                f"{summary['characteristics']} caracteristici, "
                f"{summary['values']:,} valori. "
                f"({summary['warnings']} warnings, {summary['errors']} errors)"
            )
            # Afișare issues
            errors   = [i for i in issues if i["severity"] == "error"]
            warnings = [i for i in issues if i["severity"] == "warning"]
            if errors:
                for iss in errors:
                    st.error(f"❌ {iss['issue_type']}: {iss['message']}")
            if warnings:
                with st.expander(f"⚠️ {len(warnings)} warning-uri"):
                    for iss in warnings:
                        st.warning(f"{iss['issue_type']}: {iss['message']}")

            st.rerun()
        except Exception as e:
            st.error(f"Eroare la import DuckDB: {e}")
```

### Branching în `render()`

```python
if selected == "eMAG HU":
    st.info("🦆 **Pilot DuckDB** — datele pentru acest marketplace sunt stocate în DuckDB local.")
    # Formularul de upload/cale locală este identic ca pentru celelalte marketplace-uri
    # Butonul de save apelează _do_save_duckdb() în loc de _do_save()
else:
    # comportament actual neschimbat — _do_save()
```

---

## Fluxul complet la import eMAG HU

```
setup.py
  → _do_save_duckdb(selected, cat_src, char_src, val_src)
      → loader.load_categories(cat_src)          # neschimbat
      → loader.load_characteristics(char_src)    # neschimbat
      → loader.load_values(val_src)              # neschimbat
      → duckdb_store.init_db(DB_PATH)
      → duckdb_store.import_emag_hu(cats, chars, vals, ...)
          → create import_run (status=started)
          → enrich values per-rând (robust)
          → validate → colectare import_issues
          → BEGIN TRANSACTION
              → DELETE FROM categories/characteristics/characteristic_values WHERE marketplace_id='emag_hu'
              → INSERT categories, characteristics, characteristic_values
              → INSERT import_issues
              → UPDATE import_run → completed
          → COMMIT
          → (pe excepție: UPDATE import_run → failed, re-raise)
      → duckdb_store.load_marketplace_data("emag_hu")   # cu aliasuri coloane
      → MarketplaceData.load_from_dataframes(cats2, chars2, vals2)
      → st.session_state["marketplaces"]["eMAG HU"] = mp_new
      → st.session_state.pop("_reload_eMAG HU", None)
      → afișare rezumat + issues
```

## Fluxul complet la startup

```
state.init_state()
  → pentru "eMAG HU" (din custom_mp_names):
      → lazy import reference_store_duckdb
      → duckdb_store.is_available("emag_hu") ?
          DA → load_marketplace_data() (cu aliasuri) → MarketplaceData.load_from_dataframes()
          NU → skip (marketplace neconfigurat)
      → continue (nu se mai face load_from_disk parquet)
  → pentru toți ceilalți:
      → load_from_disk(parquet) — neschimbat
```

---

## Dependențe noi

```
duckdb>=0.10.0
```

---

## Verificări post-implementare

1. `python -m py_compile core/reference_store_duckdb.py` — no syntax errors
2. Smoke test Python:
   ```python
   from core.reference_store_duckdb import init_db, import_emag_hu, load_marketplace_data, is_available, DB_PATH
   import pandas as pd
   init_db(DB_PATH)
   cats  = pd.DataFrame({"id": ["1"], "emag_id": ["1"], "name": ["Test Cat"], "parent_id": [None]})
   chars = pd.DataFrame({"id": ["10"], "category_id": ["1"], "name": ["Culoare"], "mandatory": [True]})
   vals  = pd.DataFrame({"category_id": ["1"], "characteristic_id": ["10"], "characteristic_name": ["Culoare"], "value": ["Rosu"]})
   run_id = import_emag_hu(cats, chars, vals, "upload", {})
   c, ch, v = load_marketplace_data("emag_hu")
   from core.loader import MarketplaceData
   mp = MarketplaceData("eMAG HU")
   mp.load_from_dataframes(c, ch, v)
   assert mp.is_loaded()
   assert mp.category_id("Test Cat") is not None
   assert mp.mandatory_chars("1") == ["Culoare"]
   assert "Rosu" in mp.valid_values("1", "Culoare")
   print("Smoke test OK")
   ```
3. Verificare că celelalte marketplace-uri nu sunt afectate (load din parquet rămâne funcțional)
4. Verificare că `process.py` funcționează fără modificări cu datele din DuckDB
