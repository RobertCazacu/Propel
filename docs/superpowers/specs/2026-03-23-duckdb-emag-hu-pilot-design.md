# DuckDB Pilot — eMAG HU Reference Store

**Date:** 2026-03-23
**Status:** Approved
**Scope:** Pilot controlled — DuckDB exclusiv pentru `eMAG HU`

---

## Obiectiv

Migrarea stratului de stocare a datelor de referință (categories, characteristics, values) pentru marketplace-ul `eMAG HU` de la fișiere Parquet la DuckDB local. Toate celelalte marketplace-uri rămân neschimbate. Procesarea produselor, AI enrichment, exportul Excel și logica de detecție nu sunt modificate.

---

## Constrângeri hard

- NU se modifică `core/processor.py`, `pages/process.py`, logica AI, exportul Excel
- Metodele publice ale `MarketplaceData` rămân intacte: `category_id`, `category_name`, `mandatory_chars`, `valid_values`, `has_char`, `category_list`, `stats`, `is_loaded`
- Doar `eMAG HU` folosește DuckDB; orice alt marketplace continuă cu sistemul Parquet existent
- Fișierul DB: `data/reference_data.duckdb`

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
CREATE TABLE marketplaces (
    marketplace_id   VARCHAR PRIMARY KEY,
    marketplace_name VARCHAR NOT NULL,
    storage_backend  VARCHAR NOT NULL,   -- 'duckdb' | 'parquet'
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### `categories`
```sql
CREATE TABLE categories (
    marketplace_id   VARCHAR NOT NULL,
    category_id      VARCHAR NOT NULL,
    emag_id          VARCHAR,
    category_name    VARCHAR NOT NULL,
    parent_category_id VARCHAR,
    import_run_id    VARCHAR,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```
Logical PK: `(marketplace_id, category_id)`

### `characteristics`
```sql
CREATE TABLE characteristics (
    marketplace_id       VARCHAR NOT NULL,
    characteristic_id    VARCHAR NOT NULL,
    category_id          VARCHAR NOT NULL,
    characteristic_name  VARCHAR NOT NULL,
    mandatory            BOOLEAN NOT NULL DEFAULT FALSE,
    import_run_id        VARCHAR,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```
Logical PK: `(marketplace_id, characteristic_id, category_id)`

### `characteristic_values`
```sql
CREATE TABLE characteristic_values (
    marketplace_id       VARCHAR NOT NULL,
    category_id          VARCHAR,
    characteristic_id    VARCHAR,
    characteristic_name  VARCHAR,
    value                VARCHAR NOT NULL,
    import_run_id        VARCHAR,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
```

### `import_runs`
```sql
CREATE TABLE import_runs (
    import_run_id         VARCHAR PRIMARY KEY,
    marketplace_id        VARCHAR NOT NULL,
    source_type           VARCHAR NOT NULL,   -- 'upload' | 'local_path'
    categories_source     VARCHAR,
    characteristics_source VARCHAR,
    values_source         VARCHAR,
    status                VARCHAR NOT NULL,   -- 'started'|'validated'|'completed'|'failed'
    created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at          TIMESTAMP,
    notes                 VARCHAR
)
```

### `import_issues`
```sql
CREATE TABLE import_issues (
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
DB_PATH      = Path("data/reference_data.duckdb")
```

### Funcții publice

#### `init_db(db_path: Path) -> None`
Creează fișierul DB dacă nu există, creează toate tabelele (IF NOT EXISTS), face upsert pe `marketplaces` pentru `emag_hu`.

#### `import_emag_hu(cats_df, chars_df, vals_df, source_type, sources) -> str`
1. Creează `import_run` cu status `started`
2. Enrichment values per-rând (nu doar când TOATĂ coloana e goală — fix față de comportamentul actual)
3. Șterge datele vechi pentru `emag_hu` din toate tabelele de date
4. Inserează în `categories`, `characteristics`, `characteristic_values`
5. Rulează validările → inserează în `import_issues`
6. Update `import_run` status → `completed`
7. Returnează `import_run_id`

#### `load_marketplace_data(marketplace_id: str) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]`
Returnează `(cats_df, chars_df, vals_df)` în formatul exact așteptat de `MarketplaceData.load_from_dataframes()`.

#### `is_available(marketplace_id: str) -> bool`
Returnează `True` dacă DB-ul există și are cel puțin un `import_run` completed pentru marketplace.

#### `get_import_summary(import_run_id: str) -> dict`
Returnează `{categories, characteristics, values, warnings, errors, notes}`.

#### `get_issues(import_run_id: str) -> list[dict]`
Returnează lista de issues pentru afișare în UI.

---

## Enrich values (robust)

Îmbunătățire față de logica actuală: enrichment per-rând, nu condiționat de `category_id.isna().all()`.

```python
# Logica nouă:
# Pentru fiecare rând din vals_df unde category_id lipsește (null/NaN):
#   dacă characteristic_id există → join cu chars_df → completează category_id și characteristic_name
# Se aplică indiferent de celelalte rânduri
```

---

## Validări (`import_issues`)

| `issue_type`              | `severity` | Condiție                                               |
|---------------------------|------------|--------------------------------------------------------|
| `orphan_characteristic`   | warning    | `category_id` din characteristics nu există în categories |
| `orphan_value`            | warning    | value fără characteristic valid în chars               |
| `mandatory_no_values`     | warning    | char obligatorie fără nicio valoare permisă            |
| `duplicate_category`      | warning    | name sau id duplicat în categories                     |
| `duplicate_characteristic`| warning    | (category_id, characteristic_name) duplicat            |
| `empty_value`             | error      | value NULL sau string gol după trim                    |

---

## Modificări `state.py`

```python
DUCKDB_MARKETPLACES = {"eMAG HU"}

# În init_state(), în bucla de auto-load, înainte de load_from_disk():
if mp_name in DUCKDB_MARKETPLACES:
    if duckdb_store.is_available(EMAG_HU_ID):
        cats, chars, vals = duckdb_store.load_marketplace_data(EMAG_HU_ID)
        mp = MarketplaceData(mp_name)
        mp.load_from_dataframes(cats, chars, vals)
        st.session_state["marketplaces"][mp_name] = mp
    continue  # sare peste load_from_disk() parquet
```

`set_marketplace()` rămâne neschimbat — DuckDB-ul este scris direct din setup.py, nu prin state.

---

## Modificări `setup.py`

Adăugare funcție `_do_save_duckdb(selected, cat_src, char_src, val_src, source_type)`:
1. Parsează fișierele via `loader.py` (neschimbat)
2. Apelează `duckdb_store.import_emag_hu()`
3. Apelează `duckdb_store.load_marketplace_data()` → `MarketplaceData.load_from_dataframes()`
4. Setează direct `st.session_state["marketplaces"][selected] = mp`
5. Afișează rezumat + issues

Branching în `render()`:
```python
if selected == "eMAG HU":
    # badge DuckDB pilot
    # _do_save_duckdb() în loc de _do_save()
else:
    # comportament actual neschimbat
```

---

## UI eMAG HU (setup.py)

- Badge: `🦆 Pilot DuckDB — datele sunt stocate în DuckDB local`
- După import: rezumat `{categorii, caracteristici, valori, warnings, errors}`
- Dacă există issues `warning`: afișate sumar într-un expander
- Dacă există issues `error`: afișate cu `st.error`
- Stilul paginii rămâne similar cu restul marketplace-urilor

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
          → enrich values per-rând
          → delete old data for emag_hu
          → insert categories, characteristics, characteristic_values
          → validate → import_issues
          → update import_run → completed
      → duckdb_store.load_marketplace_data("emag_hu")
      → MarketplaceData.load_from_dataframes(cats, chars, vals)
      → st.session_state["marketplaces"]["eMAG HU"] = mp
      → afișare rezumat + issues
```

## Fluxul complet la startup

```
state.init_state()
  → pentru "eMAG HU":
      → duckdb_store.is_available("emag_hu") ?
          DA → load_marketplace_data() → MarketplaceData.load_from_dataframes()
          NU → skip (marketplace neconfigurat, ca oricare altul)
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

1. `python -m py_compile core/reference_store_duckdb.py`
2. Smoke test: init DB → import cu DataFrames mock → load → verificare metode publice MarketplaceData
3. Verificare că celelalte marketplace-uri nu sunt afectate (load din parquet rămâne funcțional)
4. Verificare că `process.py` funcționează fără modificări cu datele din DuckDB
