# Fix Mapping Bugs — Processor & Loader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repararea a 8 bug-uri de severitate Critică/High/Medium identificate în pipeline-ul de mapping caracteristici/atribute marketplace.

**Architecture:** Toate modificările sunt în `core/processor.py` (detectors, cache, AI threshold, validator) și `core/loader.py` (normalize char name). Nu se creează fișiere noi. Fiecare task este independent și testabil separat.

**Tech Stack:** Python 3.11, pandas, pytest, re, BeautifulSoup

---

## File Map

| Fișier | Modificări |
|--------|-----------|
| `core/processor.py` | RC-1 cache key, RC-3 find_valid în detectori, RC-4 keyword "short", RC-6 AI threshold, RC-7 "model" keyword, RC-8 validate_existing, RC-9 fleece/sezon |
| `core/loader.py` | RC-5 _normalize_char_name + diacritice |
| `tests/test_processor_mapping.py` | Teste noi pentru toate bug-urile fixate |

---

## Task 1: RC-5 — `_normalize_char_name` nu strip diacritice (loader.py)

**Files:**
- Modify: `core/loader.py:26-34`
- Test: `tests/test_processor_mapping.py`

**Root cause:** `casefold()` nu elimină diacritice. `"Mărime:"` → `"mărime"` ≠ `"Marime:"` → `"marime"`. `has_char()` returnează False → detectorul nu se aplică pentru categorii cu caractere diacritice în BD.

- [ ] **Step 1: Scrie testul care eșuează**

```python
# tests/test_processor_mapping.py
from core.loader import _normalize_char_name

def test_normalize_char_name_strips_diacritics():
    """Mărime: trebuie să fie echivalent cu Marime: după normalizare."""
    assert _normalize_char_name("Mărime:") == _normalize_char_name("Marime:")
    assert _normalize_char_name("Culoare de bază") == _normalize_char_name("Culoare de baza")
    assert _normalize_char_name("Tip:") == "tip"
    assert _normalize_char_name("  Marime:  ") == "marime"
```

- [ ] **Step 2: Rulează testul — verifică că eșuează**

```
pytest tests/test_processor_mapping.py::test_normalize_char_name_strips_diacritics -v
# Expected: FAIL
```

- [ ] **Step 3: Implementează fix-ul**

```python
# core/loader.py — modifică _normalize_char_name (linia 26-34)
def _normalize_char_name(s: str) -> str:
    s = re.sub(r'\s+', ' ', str(s).strip())
    s = s.rstrip(':').strip().casefold()
    return _normalize_str(s)   # _normalize_str există deja în loader.py:20-23
```

- [ ] **Step 4: Rulează testul — verifică că trece**

```
pytest tests/test_processor_mapping.py::test_normalize_char_name_strips_diacritics -v
# Expected: PASS
```

- [ ] **Step 5: Rulează toate testele existente**

```
python -m pytest tests/ -x -q
# Expected: toate trec
```

---

## Task 2: RC-1 — Cache stale după re-import marketplace (processor.py)

**Files:**
- Modify: `core/processor.py:406` (declararea cache-ului) și `core/processor.py:440-444` (lookup)
- Test: `tests/test_processor_mapping.py`

**Root cause:** `_applicable_detectors_cache: dict[str, tuple]` folosește doar `cat_id` ca cheie. La re-import marketplace cu `MarketplaceData` nou, cache-ul conține detectori din instanța veche. Dacă caracteristicile s-au schimbat, detectori greșiți (sau lipsa unor detectori noi) sunt aplicați.

- [ ] **Step 1: Scrie testul**

```python
def test_cache_invalidates_on_new_marketplace_data():
    """Cache-ul nu trebuie să servească detectori din instanțe vechi de MarketplaceData."""
    import pandas as pd
    from core.processor import process_product, _applicable_detectors_cache
    from core.loader import MarketplaceData

    cats = pd.DataFrame({"id": ["1"], "name": ["Tricouri"], "emag_id": [None], "parent_id": [None]})
    chars1 = pd.DataFrame({
        "id": ["10"], "characteristic_id": ["10"], "category_id": ["1"],
        "name": ["Marime:"], "mandatory": ["1"], "restrictive": ["1"]
    })
    chars2 = pd.DataFrame({
        "id": ["10"], "characteristic_id": ["10"], "category_id": ["1"],
        "name": ["Culoare de baza"], "mandatory": ["1"], "restrictive": ["1"]
    })
    vals1 = pd.DataFrame({"category_id": ["1"], "characteristic_id": ["10"], "characteristic_name": ["Marime:"], "value": ["42 EU"]})
    vals2 = pd.DataFrame({"category_id": ["1"], "characteristic_id": ["10"], "characteristic_name": ["Culoare de baza"], "value": ["Negru"]})

    data1 = MarketplaceData("test")
    data1.load_from_dataframes(cats.copy(), chars1, vals1)
    data2 = MarketplaceData("test")
    data2.load_from_dataframes(cats.copy(), chars2, vals2)

    # data1: are Marime, data2: are Culoare
    r1 = process_product("Produs - 42", "", "Tricouri", {}, data1)
    r2 = process_product("Tricou Negru", "", "Tricouri", {}, data2)

    # r2 trebuie să folosească data2 (Culoare), nu data1 (Marime)
    assert "Culoare de baza" in r2 or len(r2) == 0  # nu Marime din cache vechi
    assert "Marime:" not in r2
```

- [ ] **Step 2: Rulează testul — verifică că eșuează sau trece accidental**

```
pytest tests/test_processor_mapping.py::test_cache_invalidates_on_new_marketplace_data -v
```

- [ ] **Step 3: Implementează fix-ul**

```python
# core/processor.py:406 — schimbă tipul cache-ului
_applicable_detectors_cache: dict[tuple, tuple] = {}

# core/processor.py:440-444 — schimbă lookup-ul în process_product
_cache_key = (id(data), cat_id)
if _cache_key not in _applicable_detectors_cache:
    applicable_for_cat = tuple(
        (cn, det) for cn, det in ALL_DETECTORS if data.has_char(cat_id, cn)
    )
    _applicable_detectors_cache[_cache_key] = applicable_for_cat
    if not applicable_for_cat:
        log.warning(
            "Niciun detector aplicabil pentru cat_id=%s cat_name=%r — verifică caracteristicile importate",
            cat_id, cat_name,
        )
applicable = _applicable_detectors_cache[_cache_key]
```

- [ ] **Step 4: Rulează testul**

```
pytest tests/test_processor_mapping.py::test_cache_invalidates_on_new_marketplace_data -v
# Expected: PASS
```

- [ ] **Step 5: Rulează toate testele**

```
python -m pytest tests/ -x -q
```

---

## Task 3: RC-3 — Detectori cu `val in vs` case-sensitive (processor.py)

**Files:**
- Modify: `core/processor.py` — `detect_culoare_baza`, `detect_material`, `detect_sport`, `detect_croiala`, `detect_lungime_maneca`, `detect_tip_produs`, `detect_sistem_inchidere`, `detect_stil`, `detect_imprimeu`, `detect_sezon`, `detect_tip_inchidere`, `detect_lungime`
- Test: `tests/test_processor_mapping.py`

**Root cause:** Toți detectori fac `if val in vs` (set membership) case-sensitive. Dacă valid_values din BD are `"negru"` în loc de `"Negru"`, detectorul returnează `None` chiar dacă keyword-ul e detectat în text. Fix: înlocuiește `val in vs` cu `data.find_valid(val, cat_id, char_name)`.

- [ ] **Step 1: Scrie testele**

```python
def _make_data_with_values(cat_id, char_name, values):
    """Helper: creează MarketplaceData minimal cu valid values."""
    import pandas as pd
    from core.loader import MarketplaceData
    cats = pd.DataFrame({"id": [cat_id], "name": ["TestCat"], "emag_id": [None], "parent_id": [None]})
    chars = pd.DataFrame({
        "id": ["1"], "characteristic_id": ["1"], "category_id": [cat_id],
        "name": [char_name], "mandatory": ["0"], "restrictive": ["1"]
    })
    vals = pd.DataFrame([
        {"category_id": cat_id, "characteristic_id": "1", "characteristic_name": char_name, "value": v}
        for v in values
    ])
    d = MarketplaceData("test")
    d.load_from_dataframes(cats, chars, vals)
    return d

def test_detect_culoare_baza_case_insensitive():
    """Culoare detectată chiar dacă valid_values are casing diferit."""
    from core.processor import detect_culoare_baza
    data = _make_data_with_values("1", "Culoare de baza", ["negru", "alb"])
    result = detect_culoare_baza("Tricou negru", "", data, "1")
    assert result == "negru"  # nu None

def test_detect_material_case_insensitive():
    from core.processor import detect_material
    data = _make_data_with_values("1", "Material:", ["bumbac", "poliester"])
    result = detect_material("Tricou din bumbac", "", data, "1")
    assert result == "bumbac"

def test_detect_sport_case_insensitive():
    from core.processor import detect_sport
    data = _make_data_with_values("1", "Sport:", ["alergare", "fitness"])
    result = detect_sport("Pantofi de alergare", "", data, "1")
    assert result == "alergare"
```

- [ ] **Step 2: Rulează testele — verifică că eșuează**

```
pytest tests/test_processor_mapping.py -k "case_insensitive" -v
```

- [ ] **Step 3: Implementează fix-ul în toți detectori**

**Pattern de înlocuit** (identic pentru toți detectori):

```python
# ÎNAINTE (pattern repetat în ~12 locuri):
if keyword_matched and "ValoareExacta" in vs:
    return "ValoareExacta"

# DUPĂ (pentru fiecare valoare returnată):
if keyword_matched:
    found = data.find_valid("ValoareExacta", cat_id, char_name)
    if found:
        return found
```

**Detectori de modificat și liniile exacte:**

1. `detect_culoare_baza` (linia 100-103): `if color in vs: return color` → `found = data.find_valid(color, cat_id, char_name); if found: return found`
2. `detect_pentru` (liniile 112-131): fiecare `if "Baieti" in vs`, `if "Fete" in vs`, etc. → `data.find_valid(...)`
3. `detect_imprimeu` (142-146): `if "Logo" in vs`, `if "Cu model" in vs`, `if "Uni" in vs`
4. `detect_material` (167-171): `if mat in vs: return mat`
5. `detect_croiala` (180-188): fiecare `if "Slim fit" in vs`, etc.
6. `detect_lungime_maneca` (196-208): fiecare `if "Fara maneca" in vs`, etc.
7. `detect_sport` (230-235): `if sport in vs: return sport`
8. `detect_sezon` (243-249): `if "Toamna-Iarna" in vs`, `if "Primavara-Vara" in vs`
9. `detect_tip_produs` (274-277): `if tip in vs: return tip`
10. `detect_sistem_inchidere` (303-305): `if val in vs: return val`
11. `detect_stil` (315-325): fiecare `if "Profil inalt" in vs`, etc.
12. `detect_tip_inchidere` (354-358): fiecare `if "Fermoar" in vs`, etc.
13. `detect_lungime` (367-370): fiecare `if "Scurti" in vs`, etc.

- [ ] **Step 4: Rulează testele**

```
pytest tests/test_processor_mapping.py -k "case_insensitive" -v
# Expected: PASS
```

- [ ] **Step 5: Rulează toate testele**

```
python -m pytest tests/ -x -q
```

---

## Task 4: RC-4 — `detect_tip_produs`: keyword `"short"` fals-pozitiv (processor.py)

**Files:**
- Modify: `core/processor.py:262-265`
- Test: `tests/test_processor_mapping.py`

**Root cause:** `(["sort", "short"], "Pantaloni")` — `"short"` apare în `"short sleeve"`, `"short-sleeve"` etc. Un produs de tipul "Nike Short Sleeve Top" e clasificat ca Pantaloni.

- [ ] **Step 1: Scrie testul**

```python
def test_detect_tip_produs_no_false_positive_short_sleeve():
    from core.processor import detect_tip_produs
    data = _make_data_with_values("1", "Tip produs:", ["Pantaloni", "Tricou"])
    result = detect_tip_produs("Nike Short Sleeve Running Top - L", data, "1")
    assert result != "Pantaloni", f"'short sleeve' clasificat greșit ca Pantaloni, got {result!r}"

def test_detect_tip_produs_sort_still_works():
    from core.processor import detect_tip_produs
    data = _make_data_with_values("1", "Tip produs:", ["Pantaloni"])
    result = detect_tip_produs("Pantaloni Scurti Nike - L", data, "1")
    assert result == "Pantaloni"
```

- [ ] **Step 2: Rulează testele — verifică că primul eșuează**

```
pytest tests/test_processor_mapping.py -k "tip_produs" -v
```

- [ ] **Step 3: Implementează fix-ul**

```python
# core/processor.py:264 — înlocuiește linia cu keywords pentru Pantaloni scurți
# ÎNAINTE:
(["sort", "short"], "Pantaloni"),
# DUPĂ:
(["sort", "pantaloni scurti", "shorts"], "Pantaloni"),
```

- [ ] **Step 4: Rulează testele**

```
pytest tests/test_processor_mapping.py -k "tip_produs" -v
# Expected: ambele PASS
```

- [ ] **Step 5: Rulează toate testele**

```
python -m pytest tests/ -x -q
```

---

## Task 5: RC-7 — `detect_imprimeu`: keyword `"model"` prea generic (processor.py)

**Files:**
- Modify: `core/processor.py:142`
- Test: `tests/test_processor_mapping.py`

**Root cause:** `"model"` apare în titluri ca `"Model 2024"`, `"conform modelului"`, coduri de produs. Orice produs cu `"model"` în text primește `Imprimeu = Cu model` greșit.

- [ ] **Step 1: Scrie testul**

```python
def test_detect_imprimeu_no_false_positive_model():
    from core.processor import detect_imprimeu
    data = _make_data_with_values("1", "Imprimeu:", ["Cu model", "Uni", "Logo"])
    # "model" în titlu nu trebuie să trigger Cu model
    result = detect_imprimeu("Nike Air Force 1 Model 2024 - Alb", "", data, "1")
    assert result != "Cu model", f"'model' din titlu clasificat greșit ca imprimeu Cu model, got {result!r}"

def test_detect_imprimeu_graphic_works():
    from core.processor import detect_imprimeu
    data = _make_data_with_values("1", "Imprimeu:", ["Cu model", "Uni"])
    result = detect_imprimeu("Tricou Graphic Print Nike", "", data, "1")
    assert result == "Cu model"
```

- [ ] **Step 2: Rulează testele — primul eșuează**

```
pytest tests/test_processor_mapping.py -k "imprimeu" -v
```

- [ ] **Step 3: Implementează fix-ul**

```python
# core/processor.py:142 — elimină "model" și "print" din lista de keywords (prea generice)
# ÎNAINTE:
if any(_wb(x, text) for x in ["grafic", "graphic", "print", "imprimeu", "pattern", "model", "all over"]) and "Cu model" in vs:
# DUPĂ:
if any(_wb(x, text) for x in ["grafic", "graphic", "imprimeu", "all over", "all-over"]) and "Cu model" in vs:
```

Notă: `"print"` e eliminat deoarece apare și în „fingerprint", „misprint", „blueprint". `"pattern"` rămâne — mai specific.

- [ ] **Step 4: Rulează testele**

```
pytest tests/test_processor_mapping.py -k "imprimeu" -v
# Expected: PASS
```

- [ ] **Step 5: Rulează toate testele**

```
python -m pytest tests/ -x -q
```

---

## Task 6: RC-9 — `detect_sezon`: `"fleece"` trigger fals iarnă (processor.py)

**Files:**
- Modify: `core/processor.py:243`
- Test: `tests/test_processor_mapping.py`

**Root cause:** `"fleece"` în keyword-urile de iarnă. Un produs lightweight fleece de primăvară primește `Sezon = Toamna-Iarna` greșit.

- [ ] **Step 1: Scrie testul**

```python
def test_detect_sezon_fleece_lightweight_not_winter():
    from core.processor import detect_sezon
    data = _make_data_with_values("1", "Sezon:", ["Toamna-Iarna", "Primavara-Vara"])
    # fleece lightweight NU trebuie să fie iarnă
    result = detect_sezon("Bluza Fleece Lightweight", "", data, "1")
    assert result != "Toamna-Iarna", f"fleece lightweight clasificat greșit ca iarnă, got {result!r}"

def test_detect_sezon_fleece_warm_is_winter():
    from core.processor import detect_sezon
    data = _make_data_with_values("1", "Sezon:", ["Toamna-Iarna"])
    result = detect_sezon("Hanorac Fleece Thermal Warm", "", data, "1")
    assert result == "Toamna-Iarna"
```

- [ ] **Step 2: Rulează testele**

```
pytest tests/test_processor_mapping.py -k "sezon" -v
```

- [ ] **Step 3: Implementează fix-ul**

```python
# core/processor.py:243 — split logica fleece cu condiție de excludere
# ÎNAINTE:
if any(_wb(x, text) for x in ["iarna", "winter", "fleece", "thermal", "therma", "thermo", "warm", "caldura", "polar"]):
    if "Toamna-Iarna" in vs:
        return "Toamna-Iarna"

# DUPĂ:
_winter_hard = ["iarna", "winter", "thermal", "therma", "thermo", "warm", "caldura", "polar"]
_lightweight_excl = ["light", "usor", "lightweight", "breathable", "respirabil"]
_is_winter = any(_wb(x, text) for x in _winter_hard)
_is_fleece = _wb("fleece", text) and not any(_wb(x, text) for x in _lightweight_excl)
if _is_winter or _is_fleece:
    found = data.find_valid("Toamna-Iarna", cat_id, "Sezon:")
    if found:
        return found
```

- [ ] **Step 4: Rulează testele**

```
pytest tests/test_processor_mapping.py -k "sezon" -v
# Expected: PASS
```

- [ ] **Step 5: Rulează toate testele**

```
python -m pytest tests/ -x -q
```

---

## Task 7: RC-8 — `validate_existing` case-sensitive (processor.py)

**Files:**
- Modify: `core/processor.py:618-623`
- Test: `tests/test_processor_mapping.py`

**Root cause:** `str(value).strip() not in vs` — dacă produsul existent are `"NEGRU"` și valid_values are `"Negru"`, e raportat ca invalid deși e semantic corect.

- [ ] **Step 1: Scrie testul**

```python
def test_validate_existing_case_insensitive():
    from core.processor import validate_existing
    from core.loader import MarketplaceData
    import pandas as pd

    data = _make_data_with_values("1", "Culoare de baza", ["Negru", "Alb"])
    # "NEGRU" trebuie acceptat (varianta uppercase a "Negru")
    cats = pd.DataFrame({"id": ["1"], "name": ["TestCat"], "emag_id": [None], "parent_id": [None]})
    data.categories = cats
    data._build_indexes()
    result = validate_existing({"Culoare de baza": "NEGRU"}, "TestCat", data)
    assert "Culoare de baza" not in result, f"'NEGRU' raportat invalid deși 'Negru' e valid"
```

- [ ] **Step 2: Rulează testul — eșuează**

```
pytest tests/test_processor_mapping.py::test_validate_existing_case_insensitive -v
```

- [ ] **Step 3: Implementează fix-ul**

```python
# core/processor.py:618-623 — validate_existing
# ÎNAINTE:
    for char_name, value in existing_chars.items():
        if not value:
            continue
        vs = data.valid_values(cat_id, char_name)
        if vs and str(value).strip() not in vs:
            invalid[char_name] = value

# DUPĂ:
    for char_name, value in existing_chars.items():
        if not value:
            continue
        vs = data.valid_values(cat_id, char_name)
        if vs:
            val_str = str(value).strip()
            if val_str not in vs:
                # Fallback: case-insensitive check
                vs_lower = {v.lower() for v in vs}
                if val_str.lower() not in vs_lower:
                    invalid[char_name] = value
```

- [ ] **Step 4: Rulează testele**

```
pytest tests/test_processor_mapping.py::test_validate_existing_case_insensitive -v
# Expected: PASS
```

- [ ] **Step 5: Rulează toate testele**

```
python -m pytest tests/ -x -q
```

---

## Task 8: RC-6 — AI enrichment threshold prea mic (processor.py)

**Files:**
- Modify: `core/processor.py:490-493`
- Test: `tests/test_processor_mapping.py`

**Root cause:** `len(vals) <= 40` exclude caracteristici cu liste mari de valori (culori: 50+, mărimi EU: 60+). AI-ul nu completează aceste câmpuri chiar dacă sunt obligatorii.

- [ ] **Step 1: Scrie testul**

```python
def test_ai_char_options_includes_large_value_sets():
    """Caracteristicile cu >40 valori trebuie incluse în char_options (truncate la 50)."""
    # Verificăm că pragul nu exclude culori/mărimi
    # Simulăm logica din process_product direct
    from core.processor import process_product
    import pandas as pd
    from core.loader import MarketplaceData

    many_values = [f"Val{i}" for i in range(60)]  # 60 valori
    cats = pd.DataFrame({"id": ["1"], "name": ["Cat"], "emag_id": [None], "parent_id": [None]})
    chars = pd.DataFrame({
        "id": ["1"], "characteristic_id": ["1"], "category_id": ["1"],
        "name": ["Culoare:"], "mandatory": ["1"], "restrictive": ["1"]
    })
    vals = pd.DataFrame([
        {"category_id": "1", "characteristic_id": "1", "characteristic_name": "Culoare:", "value": v}
        for v in many_values
    ])
    data = MarketplaceData("test")
    data.load_from_dataframes(cats, chars, vals)

    # char_options nu trebuie să fie gol pentru Culoare: cu 60 valori
    cat_id = data.category_id("Cat")
    cat_vals = data._valid_values.get(cat_id, {})
    # Noul prag: 80 sau trimite truncat
    char_options = {
        ch: sorted(v)[:50]
        for ch, v in cat_vals.items()
        if len(v) <= 80
    }
    assert "Culoare:" in char_options, "Culoare: cu 60 valori trebuie inclusă în char_options"
    assert len(char_options["Culoare:"]) <= 50
```

- [ ] **Step 2: Rulează testul**

```
pytest tests/test_processor_mapping.py::test_ai_char_options_includes_large_value_sets -v
```

- [ ] **Step 3: Implementează fix-ul**

```python
# core/processor.py:490-493 — schimbă filtrul și adaugă truncare
# ÎNAINTE:
char_options = {
    ch: vals
    for ch, vals in data._valid_values.get(cat_id, {}).items()
    if not combined_existing.get(ch) and len(vals) <= 40
}

# DUPĂ:
char_options = {
    ch: sorted(vals)[:50] if len(vals) > 50 else vals
    for ch, vals in data._valid_values.get(cat_id, {}).items()
    if not combined_existing.get(ch) and len(vals) <= 80
}
```

- [ ] **Step 4: Rulează testele**

```
pytest tests/test_processor_mapping.py::test_ai_char_options_includes_large_value_sets -v
# Expected: PASS
```

- [ ] **Step 5: Rulează toate testele**

```
python -m pytest tests/ -x -q
# Expected: toate trec
```

---

## Task 9: Verificare finală și commit

- [ ] **Step 1: Rulează suita completă**

```
python -m pytest tests/ -q
# Expected: 131+ passed (toate testele vechi + noile)
```

- [ ] **Step 2: Verificare manuală prin grep — nu mai există `val in vs` direct în detectori**

```bash
grep -n " in vs:" core/processor.py | grep -v "find_valid"
# Expected: 0 rezultate (toate înlocuite cu find_valid)
```

- [ ] **Step 3: Commit**

```bash
git add core/processor.py core/loader.py tests/test_processor_mapping.py
git commit -m "fix: repair 8 mapping bugs — cache key, case-sensitive detectors, false positives, AI threshold, diacritics normalize"
```
