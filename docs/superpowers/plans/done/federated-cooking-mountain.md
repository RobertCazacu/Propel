# Plan: 6 Îmbunătățiri — Propel Streamlit + DuckDB

## Context
Utilizatorul dorește implementarea tuturor celor 6 îmbunătățiri identificate în analiza inițială. Modulul Vision este deja implementat și parțial conectat în `processor.py`, dar `fusion.py::fuse_category()` nu e apelat nicăieri și `ai_enricher.py` nu primește context vision. Sistemul de logging are 4 surse separate fără viewer unificat. `process_results` se pierde la restart. Shadow mode nu surfachează diff-urile în UI.

---

## Ordine de implementare (dependențe)

```
Phase 3 → Phase 5 → Phase 6 → Phase 4 → Phase 1
```

---

## Phase 3 — Deprecare Parquet Backend (3 linii, 2 fișiere)

**Fișiere:** `core/state.py`, `pages/setup.py`

### 3.1 `core/state.py` — `get_backend()`
În funcția `get_backend()`, după calculul valorii, adaugă:
```python
if backend == "parquet":
    log.warning(
        "DEPRECATION: REFERENCE_BACKEND=parquet este deprecat. "
        "Migreaza la DuckDB folosind scripts/migrate_parquet_to_duckdb.py"
    )
```

### 3.2 `pages/setup.py` — banner UI
La începutul funcției `render()`, adaugă:
```python
from core.state import get_backend
if get_backend() == "parquet":
    st.warning("Backend Parquet deprecat. Foloseste scripts/migrate_parquet_to_duckdb.py")
```

**Verificare:** Setează `REFERENCE_BACKEND=parquet`, deschide Setup page → banner apare.

---

## Phase 5 — Session State Persistent (4 fișiere)

**Fișiere:** `core/reference_store_duckdb.py`, `pages/process.py`, `pages/results.py`

### 5.1 `core/reference_store_duckdb.py` — DDL
Adaugă în `_DDL_STATEMENTS` (la final, înainte de `]`):
```sql
CREATE TABLE IF NOT EXISTS process_runs (
    run_id     VARCHAR NOT NULL,
    marketplace VARCHAR NOT NULL,
    run_ts     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    results    JSON NOT NULL
)
```

### 5.2 `core/reference_store_duckdb.py` — 2 funcții noi (append la final)
```python
def save_process_run(results: list, marketplace: str, db_path: Path = DB_PATH) -> None:
    import uuid as _uuid
    run_id = str(_uuid.uuid4())
    try:
        with duckdb.connect(str(db_path)) as con:
            con.execute(
                "INSERT INTO process_runs (run_id, marketplace, results) VALUES (?, ?, ?)",
                [run_id, marketplace, json.dumps(results, ensure_ascii=False, default=str)]
            )
    except Exception as exc:
        log.warning("save_process_run failed: %s", exc)

def load_last_process_run(marketplace: str, db_path: Path = DB_PATH) -> list:
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            row = con.execute(
                "SELECT results FROM process_runs WHERE marketplace = ? ORDER BY run_ts DESC LIMIT 1",
                [marketplace]
            ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception as exc:
        log.warning("load_last_process_run failed: %s", exc)
    return []
```

### 5.3 `pages/process.py` — salvare după procesare
Unde se face `st.session_state["process_results"] = results`, adaugă imediat după:
```python
try:
    from core.reference_store_duckdb import save_process_run
    save_process_run(results, marketplace)
except Exception:
    pass
```

### 5.4 `pages/results.py` — buton "Încarcă ultima sesiune"
Înlocuiește blocul `if not results: st.info(...); return` cu:
```python
results = st.session_state.get("process_results", [])
if not results:
    marketplace = st.session_state.get("active_mp", "")
    if marketplace:
        if st.button("Încarcă ultima sesiune"):
            from core.reference_store_duckdb import load_last_process_run
            loaded = load_last_process_run(marketplace)
            if loaded:
                st.session_state["process_results"] = loaded
                st.rerun()
            else:
                st.warning("Nu există sesiune salvată pentru acest marketplace.")
    st.info("Nu există rezultate. Mergi la 📁 Process Offers și rulează procesarea.")
    return
```

**Verificare:** Procesează un fișier, închide browserul, redeschide → Results → "Încarcă ultima sesiune" → date restaurate.

---

## Phase 6 — Shadow Mode Diff Surface (4 fișiere)

**Fișiere:** `core/reference_store_duckdb.py`, `core/ai_logger.py`, `core/ai_enricher.py`, `pages/diagnostic.py`

### 6.1 `core/reference_store_duckdb.py` — migrare coloană
Adaugă în `_MIGRATIONS` (lista existentă):
```python
"ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS shadow_diff JSON DEFAULT NULL",
```

### 6.2 `core/reference_store_duckdb.py` — `write_ai_run_log()`
- Adaugă parametru `shadow_diff: dict = None` la semnătură
- Adaugă `shadow_diff` în INSERT: `json.dumps(shadow_diff, default=str) if shadow_diff else None`

### 6.3 `core/ai_logger.py` — `write_run_to_duckdb()`
- Adaugă parametru `shadow_diff: dict = None` la semnătură
- Transmite-l mai departe la `write_ai_run_log(..., shadow_diff=shadow_diff)`

### 6.4 `core/ai_enricher.py` — computare diff în shadow branch
În blocul shadow (lângă linia ~827), după ce `structured_result` e disponibil:
```python
_shadow_diff = {
    "agree": sorted(set(structured_result.keys()) & set(validated.keys())),
    "only_structured": sorted(set(structured_result.keys()) - set(validated.keys())),
    "only_plain": sorted(set(validated.keys()) - set(structured_result.keys())),
    "value_conflicts": {
        k: {"structured": str(structured_result[k]), "plain": str(validated[k])}
        for k in set(structured_result.keys()) & set(validated.keys())
        if str(structured_result[k]).strip().lower() != str(validated[k]).strip().lower()
    }
}
```
Transmite `shadow_diff=_shadow_diff` la apelul `write_run_to_duckdb(...)`.

### 6.5 `pages/diagnostic.py` — sub-secțiune Shadow Comparison în tab AI Metrics
Adaugă după blocul structured output KPIs (după ~linia 210):
- Query DuckDB: `SELECT shadow_diff FROM ai_run_log WHERE structured_mode='shadow' AND shadow_diff IS NOT NULL ORDER BY created_at DESC LIMIT 100`
- Calculează agreement_rate = avg(len(agree) / max(len(all_keys), 1)) × 100
- Afișează: nr shadow runs, agreement rate %, expander cu exemple conflicte

**Verificare:** Setează `AI_STRUCTURED_MODE=shadow`, `AI_STRUCTURED_SAMPLE=1.0`. Procesează → AI Metrics tab → "Shadow Mode Comparison" apare cu statistici.

---

## Phase 4 — Logging Centralizat (1 fișier)

**Fișier:** `pages/diagnostic.py`

### 4.1 Extinde tabs list (linia ~47)
```python
tab1, tab2, tab3, tab_ai_metrics, tab_logs = st.tabs([
    "📂 Categorii nemapate",
    "🏷 Caracteristici nemapate",
    "🔎 Detalii per produs",
    "📊 AI Metrics",
    "📝 Logs Unificate",
])
```

### 4.2 Corp tab `with tab_logs:`
Adaugă după blocul `with tab_ai_metrics:`, implementând:

1. **Controale:** selectbox nivel (ALL/DEBUG/INFO/WARNING/ERROR), selectbox sursă (ALL/app/process/ai/vision), number_input max linii
2. **Citire app.log** (`data/logs/app.log`): parsează cu regex `(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) \[(\w+)\] (.+)` → entries cu `{ts, level, source="app", message}`
3. **Citire process logs** (`core.logger::list_logs()`): 5 cele mai recente → entry sumar per run
4. **Citire AI logs** (`core.ai_logger::list_ai_log_files()`): 3 cele mai recente → entries per request
5. **Citire vision JSONL** (`data/logs/vision_runs/*.jsonl`): 2 fișiere recente, 200 linii/fișier
6. **Sortare** descrescătoare după ts, aplică filtre, limitează la max_lines
7. **Afișare** `st.dataframe(df_logs, hide_index=True, height=500)`

**Imports necesare la top:** `import json`, `import re` (verifică dacă lipsesc din diagnostic.py)

**Verificare:** Deschide tab "Logs Unificate" după un run → înregistrări din toate 4 surse vizibile. Filtrare după source="ai" → doar AI logs.

---

## Phase 1 — Vision Integration / fuse_category() (2 fișiere)

**Fișiere:** `pages/process.py`, `core/ai_enricher.py`

### 1.1 Verificare prealabilă
Citește `core/vision/image_analyzer.py` și verifică exact câmpurile din `ImageAnalysisResult`:
- Câmpul pentru categoria sugerată: `category_candidate` sau `product_type_hint`?
- Câmpul pentru confidence: `category_confidence` sau `product_type_confidence`?

### 1.2 `pages/process.py` — apel `fuse_category()` după linia ~411
Înăuntrul blocului `if _img_any:`, imediat după ce `img_result` e obținut și înainte de `if not image_options.get("suggestion_only"):`:
```python
if getattr(img_result, "category_candidate", None):  # sau product_type_hint
    from core.vision.fusion import fuse_category, TextCategoryResult, ImageCategoryResult
    _fusion = fuse_category(
        TextCategoryResult(candidate=final_cat, confidence=0.9, source=cat_action),
        ImageCategoryResult(
            candidate=img_result.category_candidate,
            confidence=getattr(img_result, "category_confidence", 0.0),
            source="clip"
        ),
        rules={},
        run_logger=_run_logger,
        offer_id=str(prod.get("id", ""))
    )
    if _fusion.final_category and mp.category_id(_fusion.final_category):
        final_cat = _fusion.final_category
        result["new_category"] = final_cat
        result["fusion_reason"] = getattr(_fusion, "reason", "")
```

### 1.3 `core/ai_enricher.py` — `vision_chars` parameter
- Adaugă `vision_chars: dict = None` la semnătura `enrich_with_ai()` (sau funcția de intrare echivalentă)
- Înăuntrul funcției, înainte de calculul `missing_options`, dacă `vision_chars`:
  ```python
  for k, v in (vision_chars or {}).items():
      if k not in existing and v:
          existing = {**existing, k: v}
  ```

**Verificare:** Procesează un produs cu enable_product_hint=True. Verifică în logs că `fusion.py::fuse_category` e apelat și `fusion_reason` apare în result dict.

---

## Phase 2 — Source Tagging (SKIP — deja implementat)
`validate_new_chars_strict()` primește deja `source="rule"/"ai"/"image"/"gate"` în apelurile existente. Nicio modificare necesară.

---

## Fișiere critice

| Fișier | Phases | Rol |
|--------|--------|-----|
| `core/reference_store_duckdb.py` | 5, 6 | DDL, funcții persistență |
| `core/ai_enricher.py` | 1, 6 | vision_chars param, shadow diff |
| `core/ai_logger.py` | 6 | shadow_diff propagare |
| `pages/diagnostic.py` | 4, 6 | tab nou + shadow section |
| `pages/process.py` | 1, 5 | fusion call, save_process_run |
| `pages/results.py` | 5 | load last session button |
| `core/state.py` | 3 | deprecation warning |
| `pages/setup.py` | 3 | banner UI |
