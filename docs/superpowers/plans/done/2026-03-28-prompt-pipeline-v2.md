# Prompt Pipeline V2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade all AI prompts (text enrichment, batch category, vision) and add per-attribute fusion engine with strict validation-as-gate, preserving 100% backwards compatibility.

**Architecture:** Phase 1 parallel (5 swarm agents) — prompt fixes + new components. Phase 2 serial — integration in image_analyzer.py. Phase 3 parallel — tests + DuckDB migration. Vision structured extraction is cloud-only (feature flag off by default), Ollama stays for product_type_hint only.

**Tech Stack:** Python 3.11+, DuckDB, Streamlit, PIL, Ollama (local), OpenAI/Anthropic (cloud optional), pytest

---

## SWARM SETUP (run first, before any task)

```bash
npx @claude-flow/cli@latest swarm init \
  --topology hierarchical \
  --max-agents 8 \
  --strategy specialized \
  --name "prompt-pipeline-v2"
```

Spawn these 5 agents in ONE message (parallel Phase 1):

```bash
# Agent 1 — text prompt engineer
npx @claude-flow/cli@latest agent spawn -t coder --name "text-prompt-engineer"

# Agent 2 — policy rules author
npx @claude-flow/cli@latest agent spawn -t coder --name "policy-rules-author"

# Agent 3 — fusion engine builder
npx @claude-flow/cli@latest agent spawn -t coder --name "fusion-engine-builder"

# Agent 4 — vision extractor builder
npx @claude-flow/cli@latest agent spawn -t coder --name "vision-extractor-builder"

# Agent 5 — vision provider patcher
npx @claude-flow/cli@latest agent spawn -t coder --name "vision-provider-patcher"
```

After Phase 1 agents complete, spawn Phase 2+3 agents.

---

## Critical Decisions (read before implementing)

| Decision | Rule |
|---|---|
| Confidence from model | NEVER use model-reported confidence as gate. Validation against allowed values list is the ONLY gate. |
| Ollama usage | Ollama = ONLY for `product_type_hint` (3-5 words, existing behavior). NOT for structured JSON extraction. |
| Cloud vision | gpt-4o-mini or claude-haiku. Feature flag `enable_structured_vision=False` default. |
| Stage order | KEEP existing order: rules → AI text → vision. Vision gets ONLY fields still empty after AI text. |
| Override text | NEVER override a field that text already filled (unless policy says otherwise). |
| Fallback on vision error | Always return `{}`, never parse free text from vision model. |
| `_src` field | Replace `_reasoning` in system prompt. Shorter, more useful for audit. `_parse_json` already strips it. |
| Description length | 700 chars (was 400). |
| Field limits | 20 fields (was 15), 25 values per field (was 20). Mandatory fields FIRST. |

---

## File Map

| File | Action | Agent |
|---|---|---|
| `core/ai_enricher.py` | Modify: `_build_char_system_prompt`, `_build_prompt`, `_build_batch_system_prompt` | 1 |
| `core/vision/visual_rules.py` | Modify: add `attribute_fusion_policy` to `DEFAULT_RULES` | 2 |
| `core/vision/fusion_attrs.py` | CREATE NEW | 3 |
| `core/vision/vision_attr_extractor.py` | CREATE NEW | 4 |
| `core/vision/visual_provider.py` | Modify: `num_predict 80→200` | 5 |
| `core/vision/image_analyzer.py` | Modify: integrate extractor + fusion | 6 (Phase 2) |
| `core/reference_store_duckdb.py` | Modify: schema migration (ADD COLUMN IF NOT EXISTS) | 7 (Phase 3) |
| `tests/test_fusion_attrs.py` | CREATE NEW | 8 (Phase 3) |
| `tests/test_vision_pipeline.py` | Modify: add policy table + extractor tests | 8 (Phase 3) |

---

## Phase 1 — Parallel Tasks (Agents 1-5)

---

### Task 1: Text Enrichment Prompts [Agent 1 — text-prompt-engineer]

**Files:**
- Modify: `core/ai_enricher.py` lines 234-247 (`_build_char_system_prompt`)
- Modify: `core/ai_enricher.py` lines 487-556 (`_build_prompt`)
- Modify: `core/ai_enricher.py` lines 250-264 (`_build_batch_system_prompt`)

#### Step 1.1: Replace `_build_char_system_prompt`

- [ ] Read `core/ai_enricher.py` lines 234-247
- [ ] Replace the entire function body with:

```python
def _build_char_system_prompt(marketplace: str) -> str:
    """Prompt de sistem static pentru enrichment caracteristici.

    Înlocuiește vechiul prompt cu _reasoning (tokeni irosiți).
    Noul câmp _src capturează semnalul cheie folosit — util pentru audit log.
    """
    return (
        f"Ești un expert în catalogul de produse pentru marketplace-uri.\n"
        f"Marketplace: {_mp_ctx(marketplace)}\n\n"

        "MISIUNEA TA: completează caracteristicile lipsă extragând informații din semnalele "
        "produsului (titlu, brand, descriere, metadata).\n\n"

        "FORMAT RĂSPUNS — JSON strict, fără text în afara lui:\n"
        '{"_src": "<semnal cheie>", "Caracteristica": "valoare", ...}\n'
        "  _src = semnalul principal folosit, ex: \"titlu:Dri-FIT→Poliester\" sau \"brand:Nike→Alergare\"\n\n"

        "REGULI (în ordinea asta):\n"
        "R1. Câmpuri marcate [OBLIGATORIU] → completezi cu prioritate maximă, indiferent de dificultate.\n"
        "R2. Câmpuri cu listă de valori → copiezi EXACT o valoare din lista dată "
        "(respecti majuscule, diacritice, spații).\n"
        "R3. Câmpuri freeform (fără listă) → valoarea în limba marketplace-ului, concisă.\n"
        "R4. Brand knowledge: Nike/Adidas/Puma=sport, Dri-FIT/Climalite/Climacool=Poliester, "
        "Fleece/Polar=Fleece, Jordan=Baschet, Air Max/React/Zoom=pantofi alergare, "
        "Merino=Lana, DWR=rezistent apa.\n"
        "R5. Dacă nu poți determina cu certitudine → OMITE câmpul (nu ghici).\n"
        "R6. Nu inventa valori în afara listei pentru câmpuri restrictive.\n"
        "R7. Zero text în afara JSON-ului. Fără markdown, fără explicații.\n\n"

        "IERARHIA SEMNALELOR (cel mai fiabil primul):\n"
        "  1. Titlu produs\n"
        "  2. Brand + model cunoscut\n"
        "  3. Descriere\n"
        "  4. Metadata (EAN, greutate, garanție)\n"
        "  5. Date cross-marketplace (la finalul promptului dacă există)"
    )
```

- [ ] Run: `python -c "from core.ai_enricher import _build_char_system_prompt; print(_build_char_system_prompt('emag'))"` — verifică că nu aruncă erori

#### Step 1.2: Replace `_build_prompt`

- [ ] Read `core/ai_enricher.py` lines 487-556
- [ ] Replace funcția cu versiunea de mai jos. **Atenție**: semnătura `(title, description, category, existing, char_options, marketplace, mandatory_set, product_meta)` rămâne IDENTICĂ — zero breaking changes.

```python
def _build_prompt(title: str, description: str, category: str,
                  existing: dict, char_options: dict, marketplace: str = "",
                  mandatory_set: set = None, product_meta: dict = None) -> str:
    """
    Construiește promptul pentru AI enrichment.

    Îmbunătățiri față de v1:
    - Descriere 700 chars (era 400)
    - 20 câmpuri max (era 15), 25 valori (era 20)
    - Obligatorii garantat primele
    - existing_chars condensat (key="val" nu JSON întreg)
    - Instrucțiune finală clară
    """
    mandatory_set = mandatory_set or set()
    meta = product_meta or {}

    # Brand: prioritate meta > existing chars
    brand = (
        str(meta["brand"]).strip()
        if meta.get("brand") and str(meta["brand"]).strip() not in ("", "nan")
        else next(
            (str(v).strip() for k, v in existing.items()
             if k.lower().rstrip(":").strip() in _BRAND_KEYS and v and str(v).strip()),
            None,
        )
    )

    # Descriere curățată — 700 chars (era 400)
    desc_clean = re.sub(r"<[^>]+>", " ", description or "")
    desc_clean = re.sub(r"\s+", " ", desc_clean).strip()[:700]

    # Existing: doar cheile cu valori, fără chei interne "_"
    existing_display = {k: v for k, v in existing.items()
                        if not k.startswith("_") and v}

    # Câmpuri: obligatorii primele, opționale la final
    # Max: 12 obligatorii + 8 opționale = 20 total (era 15 fără ordine garantată)
    mandatory_lines = []
    optional_lines = []

    for ch_name, values in char_options.items():
        req_tag = " [OBLIGATORIU]" if ch_name in mandatory_set else ""
        if values:
            vals_sample = json.dumps(sorted(values)[:25], ensure_ascii=False)
            line = f'  "{ch_name}"{req_tag}: {vals_sample}'
        else:
            line = f'  "{ch_name}"{req_tag}: <text liber>'

        if ch_name in mandatory_set:
            mandatory_lines.append(line)
        else:
            optional_lines.append(line)

    all_lines = mandatory_lines[:12] + optional_lines[:8]

    # Construiește prompt-ul
    parts = [f"PRODUS: {title}"]

    if brand:
        parts.append(f"BRAND: {brand}")

    # Metadata pe un rând (economie de tokeni)
    meta_parts = []
    for key, label in (("ean", "EAN"), ("sku", "SKU"),
                       ("weight", "Greutate(g)"), ("warranty", "Garantie")):
        val = meta.get(key)
        if val and str(val).strip() not in ("", "nan"):
            meta_parts.append(f"{label}:{str(val).strip()}")
    if meta_parts:
        parts.append("META: " + " | ".join(meta_parts))

    parts.append(f"CATEGORIE: {category}")

    if desc_clean:
        parts.append(f"DESCRIERE: {desc_clean}")

    if existing_display:
        # Condensat: key="val" în loc de JSON întreg
        filled = ", ".join(
            f'{k}="{v}"' for k, v in list(existing_display.items())[:8]
        )
        parts.append(f"COMPLETATE DEJA: {filled}")

    parts.append("")
    parts.append("CAMPURI DE COMPLETAT:")
    parts.extend(all_lines)
    parts.append("")
    parts.append(
        "Completează în JSON. Obligatorii mai întâi. "
        "Valori EXACTE din lista pentru câmpuri restrictive. "
        "Omite câmpul dacă nu ești sigur."
    )

    return "\n".join(parts)
```

- [ ] Run: `python -c "from core.ai_enricher import _build_prompt; print(_build_prompt('Nike Dri-FIT - L', 'Tricou sport', 'Tricouri', {}, {'Culoare de baza': {'Negru','Alb'}}, 'emag', {'Culoare de baza'}, {}))"` — verifică output corect

#### Step 1.3: Replace `_build_batch_system_prompt`

- [ ] Read `core/ai_enricher.py` lines 250-264
- [ ] Replace cu:

```python
def _build_batch_system_prompt(marketplace: str, category_list: list[str]) -> str:
    """Prompt de sistem pentru clasificare batch categorii.

    Îmbunătățiri față de v1:
    - Few-shot examples pentru ancorare comportament
    - Reguli disambiguation pentru titluri ambigue
    - Keywords gen în 5 limbi (RO/HU/BG/PL/EN)
    """
    cats_list = "\n".join(f"  {c}" for c in category_list)
    mp_ctx = _mp_ctx(marketplace)

    return (
        f"Ești un expert în catalogul de produse pentru marketplace-uri.\n"
        f"Marketplace: {mp_ctx}\n\n"

        "CATEGORII DISPONIBILE:\n"
        f"{cats_list}\n\n"

        "REGULĂ UNICĂ — răspunzi EXCLUSIV cu JSON:\n"
        '{"1": "Categorie exacta", "2": "Categorie exacta", "3": null, ...}\n\n'

        "REGULI:\n"
        "1. Copiezi EXACT numele categoriei din lista de mai sus (majuscule, diacritice).\n"
        "2. Titlul poate fi în orice limbă — clasifici după TIPUL produsului, nu după limbă.\n"
        "3. Categorie ambiguă (gen neclar) → alege genul indicat în titlu; dacă lipsește → bărbați.\n"
        "4. Nicio categorie potrivită → null.\n"
        "5. Zero text în afara JSON-ului.\n\n"

        "SEMNALE GEN:\n"
        "  Bărbați: men, barbati, férfi, мъже, mężczyźni, homme, masculin, herren\n"
        "  Femei: women, femei, női, жени, kobiety, femme, femenino, damen\n"
        "  Copii: kids, copii, gyerek, деца, dzieci, enfants, kinder, junior\n\n"

        "SEMNALE TIP PRODUS:\n"
        "  hoodie/sweatshirt → hanorac | jacket/geaca → geaca\n"
        "  tights/leggings/colanti → colanti | shorts/sort → pantaloni scurti\n"
        "  sneakers/shoes/pantofi → pantofi | t-shirt/tricou/tee → tricou\n"
        "  backpack/rucsac → rucsacuri | cap/sapca/hat → sapca\n\n"

        "EXEMPLE:\n"
        '  "Nike Dri-FIT T-Shirt Men" → Tricouri sport barbati\n'
        '  "Adidas Essentials Hoodie Femei" → Hanorace sport femei\n'
        '  "Jordan Sneakers Kids" → Pantofi sport copii\n'
        '  "Rucsac Nike 20L" → Rucsacuri sport\n'
        '  "Minge fotbal Adidas" → Mingi fotbal\n'
        '  "Sosete Nike 3-pack" → Sosete sport'
    )
```

- [ ] Run: `python -c "from core.ai_enricher import _build_batch_system_prompt; print(_build_batch_system_prompt('emag', ['Tricouri sport barbati', 'Hanorace femei']))"` — verifică output

#### Step 1.4: Commit Task 1

- [ ] Run tests: `python -m pytest tests/ -x -q 2>&1 | head -30`
- [ ] Verifică că niciun test existent nu a picat
- [ ] Commit:
```bash
git add core/ai_enricher.py
git commit -m "feat: upgrade text enrichment prompts v2 — 700chars, 20fields, _src audit, brand hints, batch few-shot"
```

---

### Task 2: Policy Table în visual_rules.py [Agent 2 — policy-rules-author]

**Files:**
- Modify: `core/vision/visual_rules.py` — adaugă `attribute_fusion_policy` în `DEFAULT_RULES`

#### Step 2.1: Adaugă policy table

- [ ] Read `core/vision/visual_rules.py` liniile 26-107
- [ ] Localizează `"default": {` și adaugă blocul de mai jos **la finalul dict-ului default**, înainte de `}` de închidere (după `"auto_enable_color_if_mandatory": True`):

```python
        # ── Attribute fusion policy ──────────────────────────────────────────
        # Per-attribute rules for text vs vision fusion.
        # vision_eligible: False = skip vision entirely for this attribute
        # override_text_if_filled: ALWAYS False (conservative default)
        # min_vision_confidence: threshold for accepting a vision suggestion
        #   (used as soft pre-filter; primary gate = data.find_valid())
        # conflict_action: "prefer_text" | "review"
        # allowed_sources: which vision extraction methods can fill this attr
        "attribute_fusion_policy": {
            # ── Color fields (all marketplaces) ──────────────────────────────
            "Culoare de baza": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Culoare:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            # Hungarian
            "Szín:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            # Bulgarian
            "Цвят:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },

            # ── Visual attributes (cloud vision only) ─────────────────────
            "Imprimeu:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.70,
                "conflict_action": "review",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Lungime maneca:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.65,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Tip produs:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.75,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Stil:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.70,
                "conflict_action": "review",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Sistem inchidere:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.75,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Tip inchidere:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.75,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Pentru:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.80,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },

            # ── NON-visual (never use vision for these) ───────────────────
            "Material:": {
                "vision_eligible": False,
                "reason": "Material cannot be reliably determined visually",
            },
            "Anyag:": {"vision_eligible": False, "reason": "same — HU"},
            "Материал:": {"vision_eligible": False, "reason": "same — BG"},
            "Sezon:": {
                "vision_eligible": False,
                "reason": "Season cannot be reliably determined visually",
            },
            "Marime:": {"vision_eligible": False, "reason": "Size not visual"},
            "Méret:": {"vision_eligible": False, "reason": "Size not visual — HU"},
            "Размер:": {"vision_eligible": False, "reason": "Size not visual — BG"},
            "Sport:": {"vision_eligible": False, "reason": "Sport context is in text, not image"},
            "Varsta:": {"vision_eligible": False, "reason": "Age not visual"},
            "Instructiuni ingrijire:": {"vision_eligible": False, "reason": "Not visual"},
        },
```

#### Step 2.2: Adaugă helper `get_attr_fusion_policy`

- [ ] La finalul fișierului `visual_rules.py`, după funcția `get_category_rules`, adaugă:

```python
def get_attr_fusion_policy(attr_name: str, rules: dict = None) -> dict:
    """
    Return the fusion policy for a specific attribute name.
    Falls back to {"vision_eligible": False} if not found.

    Uses canonical name lookup via policy table keys (exact match first,
    then case-insensitive). This avoids fragile substring matching.
    """
    if rules is None:
        rules = load_rules()
    policy_table = rules.get("default", {}).get("attribute_fusion_policy", {})

    # Exact match
    if attr_name in policy_table:
        return policy_table[attr_name]

    # Case-insensitive fallback
    normalized = attr_name.strip().casefold()
    for key, val in policy_table.items():
        if key.strip().casefold() == normalized:
            return val

    # Default: not eligible
    return {"vision_eligible": False, "reason": "not in policy table"}


def is_vision_eligible(attr_name: str, rules: dict = None) -> bool:
    """Quick check: can vision fill this attribute?"""
    return get_attr_fusion_policy(attr_name, rules).get("vision_eligible", False)
```

#### Step 2.3: Test policy table

- [ ] Run:
```python
python -c "
from core.vision.visual_rules import get_attr_fusion_policy, is_vision_eligible, load_rules
rules = load_rules()
assert is_vision_eligible('Culoare de baza', rules) is True
assert is_vision_eligible('Material:', rules) is False
assert is_vision_eligible('Szín:', rules) is True
assert is_vision_eligible('Marime:', rules) is False
p = get_attr_fusion_policy('Imprimeu:', rules)
assert p['conflict_action'] == 'review'
print('ALL POLICY TESTS PASS')
"
```
- [ ] Expected output: `ALL POLICY TESTS PASS`

#### Step 2.4: Commit Task 2

```bash
git add core/vision/visual_rules.py
git commit -m "feat: add attribute_fusion_policy to visual_rules — per-attr vision eligibility + conflict rules"
```

---

### Task 3: Fusion Engine — `fusion_attrs.py` [Agent 3 — fusion-engine-builder]

**Files:**
- Create: `core/vision/fusion_attrs.py`

#### Step 3.1: Write failing tests first (TDD)

- [ ] Creează `tests/test_fusion_attrs.py`:

```python
"""Tests for per-attribute fusion engine (fusion_attrs.py)."""
import pytest
from unittest.mock import MagicMock


def _make_data(valid_values: dict = None, restrictive: bool = True):
    """Mock MarketplaceData."""
    data = MagicMock()
    vv = valid_values or {}

    def find_valid(val, cat_id, char_name):
        allowed = vv.get(char_name, set())
        if not allowed:
            return val if not restrictive else None
        # Simple exact + casefold match
        for v in allowed:
            if v.casefold() == val.strip().casefold():
                return v
        return None

    data.find_valid.side_effect = find_valid
    data.valid_values.side_effect = lambda cat_id, char: vv.get(char, set())
    data.is_restrictive.return_value = restrictive
    return data


class TestFuseAttribute:

    def test_vision_ineligible_attr_returns_text(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": False}
        data = _make_data({"Material:": {"Bumbac", "Poliester"}})
        result = fuse_attribute("Material:", ("Bumbac", 0.95, "rule"),
                                ("Poliester", 0.99, "vision"), policy, data, "cat1")
        assert result.action == "keep_text"
        assert result.final_value == "Bumbac"

    def test_vision_fills_empty_field(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Culoare de baza": {"Negru", "Alb"}})
        result = fuse_attribute("Culoare de baza", None,
                                ("Negru", 0.85, "color_algorithm"), policy, data, "cat1")
        assert result.action == "use_vision"
        assert result.final_value == "Negru"
        assert result.needs_review is False

    def test_text_wins_when_field_filled(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Culoare de baza": {"Negru", "Alb"}})
        result = fuse_attribute("Culoare de baza", ("Negru", 0.95, "rule"),
                                ("Alb", 0.90, "color_algorithm"), policy, data, "cat1")
        assert result.action == "keep_text"
        assert result.final_value == "Negru"
        assert result.needs_review is False

    def test_conflict_both_strong_review_flag(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "review"}
        data = _make_data({"Imprimeu:": {"Logo", "Uni", "Grafic"}})
        result = fuse_attribute("Imprimeu:", ("Uni", 0.80, "rule"),
                                ("Logo", 0.85, "vision_llm_cloud"), policy, data, "cat1")
        assert result.action == "conflict_review"
        assert result.final_value == "Uni"   # text wins conservator
        assert result.needs_review is True

    def test_vision_below_threshold_skipped(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.70, "conflict_action": "prefer_text"}
        data = _make_data({"Imprimeu:": {"Logo", "Uni"}})
        result = fuse_attribute("Imprimeu:", None,
                                ("Logo", 0.50, "vision_llm_cloud"), policy, data, "cat1")
        assert result.action == "skip"
        assert result.final_value is None

    def test_invalid_vision_value_rejected(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Stil:": {"Profil inalt", "Profil jos"}}, restrictive=True)
        result = fuse_attribute("Stil:", None,
                                ("X-tra High", 0.92, "vision_llm_cloud"), policy, data, "cat1")
        # "X-tra High" not in valid values → rejected
        assert result.action == "skip"
        assert result.final_value is None

    def test_freeform_vision_accepted(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({}, restrictive=False)  # no valid values = freeform
        result = fuse_attribute("Culoare de baza", None,
                                ("Albastru royal", 0.75, "color_algorithm"), policy, data, "cat1")
        assert result.action == "use_vision"
        assert result.final_value == "Albastru royal"

    def test_no_signals_returns_skip(self):
        from core.vision.fusion_attrs import fuse_attribute
        policy = {"vision_eligible": True, "override_text_if_filled": False,
                  "min_vision_confidence": 0.60, "conflict_action": "prefer_text"}
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = fuse_attribute("Culoare de baza", None, None, policy, data, "cat1")
        assert result.action == "skip"
        assert result.final_value is None


class TestFuseAllAttributes:

    def test_fuse_all_respects_policy(self):
        from core.vision.fusion_attrs import fuse_all_attributes
        rules = {
            "default": {
                "attribute_fusion_policy": {
                    "Culoare de baza": {
                        "vision_eligible": True,
                        "override_text_if_filled": False,
                        "min_vision_confidence": 0.60,
                        "conflict_action": "prefer_text",
                    },
                    "Material:": {"vision_eligible": False},
                }
            }
        }
        text_chars = {"Material:": "Bumbac"}  # text filled this
        vision_attrs = {
            "Culoare de baza": ("Negru", 0.85, "color_algorithm"),
            "Material:": ("Poliester", 0.90, "vision_llm_cloud"),  # should be ignored
        }
        data = _make_data({
            "Culoare de baza": {"Negru", "Alb"},
            "Material:": {"Bumbac", "Poliester"},
        })
        result = fuse_all_attributes(text_chars, vision_attrs, rules, data, "cat1")
        assert result["Culoare de baza"].action == "use_vision"
        assert result["Culoare de baza"].final_value == "Negru"
        # Material not in result because it's not vision_eligible
        assert "Material:" not in result
```

- [ ] Run: `python -m pytest tests/test_fusion_attrs.py -v` — Expected: **ImportError** (module doesn't exist yet)

#### Step 3.2: Implement `fusion_attrs.py`

- [ ] Creează `core/vision/fusion_attrs.py`:

```python
"""
Per-attribute text + vision fusion engine.

Echivalentul fusion.py (pentru categorie) dar aplicat fiecărui atribut în parte.
Gate-ul primar = data.find_valid() (validare contra listei permise).
Confidence-ul din vision model NU este folosit ca gate (numerele generate nu sunt calibrate).
Confidence-ul din policy (min_vision_confidence) este un soft pre-filtru extern, nu al modelului.

Public API:
    fuse_attribute(char_name, text_signal, vision_signal, policy, data, cat_id) -> AttrFusionResult
    fuse_all_attributes(text_chars, vision_attrs, rules, data, cat_id) -> dict[str, AttrFusionResult]
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple
from core.app_logger import get_logger

log = get_logger("marketplace.vision.fusion_attrs")

# Type aliases
Signal = Optional[Tuple[str, float, str]]  # (value, confidence, source)


@dataclass
class AttrFusionResult:
    char_name:        str
    final_value:      Optional[str]
    final_confidence: float
    source:           str    # "text_rule"|"text_ai"|"color_algorithm"|"vision_llm_cloud"|"review"|"skip"
    action:           str    # "keep_text"|"use_vision"|"conflict_review"|"skip"
    reason:           str
    needs_review:     bool   = False


def fuse_attribute(
    char_name: str,
    text_signal: Signal,        # (value, confidence, source) or None
    vision_signal: Signal,      # (value, confidence, source) or None
    policy: dict,
    data,                       # MarketplaceData
    cat_id,
) -> AttrFusionResult:
    """
    Decide valoarea finală pentru un atribut pe baza semnalelor text + vision.

    Reguli (în ordine):
    1. Dacă vision_eligible=False → keep_text (sau skip dacă text absent)
    2. Dacă text e completat și override_text_if_filled=False → keep_text
       (cu conflict_review dacă vision contrazice și e puternic)
    3. Dacă text absent și vision validat → use_vision
    4. Dacă vision sub prag sau invalidă → skip
    """
    text_val, text_conf, text_src = text_signal if text_signal else (None, 0.0, "")
    vis_val, vis_conf, vis_src = vision_signal if vision_signal else (None, 0.0, "")

    # ── Case 0: Vision ineligibilă pentru acest atribut ───────────────────────
    if not policy.get("vision_eligible", False):
        if text_val:
            return AttrFusionResult(
                char_name, text_val, text_conf, text_src,
                "keep_text",
                f"Vision ineligibilă pentru '{char_name}' (policy).",
            )
        return AttrFusionResult(
            char_name, None, 0.0, "", "skip",
            f"Vision ineligibilă și text absent pentru '{char_name}'.",
        )

    min_vis_conf = policy.get("min_vision_confidence", 0.65)
    override_if_filled = policy.get("override_text_if_filled", False)
    conflict_action = policy.get("conflict_action", "prefer_text")

    # ── Validare vision contra listei permise (gate primar) ───────────────────
    # Acesta este singurul gate fiabil — nu confidence-ul din model.
    vis_validated = None
    if vis_val:
        vis_validated = data.find_valid(vis_val, cat_id, char_name)
        if vis_validated is None:
            valid_set = data.valid_values(cat_id, char_name)
            if not valid_set and not data.is_restrictive(cat_id, char_name):
                # Câmp freeform — acceptă valoarea direct
                vis_validated = vis_val
            else:
                log.debug(
                    "fusion_attrs: vision value %r invalid for [%s] — rejected by find_valid",
                    vis_val, char_name,
                )

    # ── Case 1: Text completat + override_if_filled=False (conservator) ───────
    if text_val and not override_if_filled:
        if vis_validated and vis_validated.lower() != text_val.lower() and vis_conf >= min_vis_conf:
            # Vision contrazice textul și e deasupra pragului
            if conflict_action == "review":
                return AttrFusionResult(
                    char_name, text_val, text_conf * 0.9, text_src,
                    "conflict_review",
                    f"Conflict: text='{text_val}'({text_conf:.2f}) vs vision='{vis_validated}'({vis_conf:.2f}). "
                    f"Text ales conservator, marcat review.",
                    needs_review=True,
                )
        # Text rămâne — cu bonus dacă vision confirmă
        confirmed = (
            vis_validated is not None
            and vis_validated.lower() == text_val.lower()
        )
        reason = (
            f"Text ales (conf={text_conf:.2f}), confirmat vizual."
            if confirmed
            else f"Text ales (conf={text_conf:.2f}), vision absent/ignorat."
        )
        return AttrFusionResult(
            char_name, text_val, min(text_conf + (0.05 if confirmed else 0.0), 1.0),
            text_src, "keep_text", reason,
        )

    # ── Case 2: Text absent → vision poate completa ───────────────────────────
    if not text_val:
        if vis_validated and vis_conf >= min_vis_conf:
            return AttrFusionResult(
                char_name, vis_validated, vis_conf, vis_src,
                "use_vision",
                f"Text absent. Vision: '{vis_validated}' (conf={vis_conf:.2f}) acceptat.",
            )
        if vis_validated and vis_conf < min_vis_conf:
            log.debug(
                "fusion_attrs: vision conf too low %.2f < %.2f for [%s] '%s'",
                vis_conf, min_vis_conf, char_name, vis_val,
            )
        return AttrFusionResult(
            char_name, None, 0.0, "", "skip",
            f"Text absent. Vision absent sau sub prag ({vis_conf:.2f} < {min_vis_conf}).",
        )

    # ── Case 3: Fallthrough ───────────────────────────────────────────────────
    return AttrFusionResult(
        char_name, None, 0.0, "", "skip", "No valid signal.",
    )


def fuse_all_attributes(
    text_chars: dict,          # {char_name: value} — din rules + AI text
    vision_attrs: dict,        # {char_name: (value, confidence, source)} — din vision
    rules: dict,
    data,                      # MarketplaceData
    cat_id,
) -> dict[str, AttrFusionResult]:
    """
    Aplică fuse_attribute pentru fiecare atribut relevant.

    Returnează {char_name: AttrFusionResult} NUMAI pentru atributele
    unde vision a contribuit (use_vision, conflict_review).
    Atributele unde textul a câștigat fără contestație NU sunt incluse
    (nu e nevoie să le procesezi din nou — sunt deja în text_chars).
    """
    policy_table = rules.get("default", {}).get("attribute_fusion_policy", {})
    results: dict[str, AttrFusionResult] = {}

    # Procesează NUMAI atributele pentru care există un semnal vision
    for char_name, vis_signal in vision_attrs.items():
        policy = policy_table.get(char_name, {"vision_eligible": False})

        text_val = text_chars.get(char_name)
        text_signal = (text_val, 0.95, "text") if text_val else None

        result = fuse_attribute(char_name, text_signal, vis_signal, policy, data, cat_id)

        if result.action in ("use_vision", "conflict_review"):
            results[char_name] = result
            log.debug(
                "fuse_all: [%s] action=%s value=%r needs_review=%s",
                char_name, result.action, result.final_value, result.needs_review,
            )

    return results
```

#### Step 3.3: Run tests

- [ ] `python -m pytest tests/test_fusion_attrs.py -v`
- [ ] Expected: **ALL PASS**

#### Step 3.4: Commit Task 3

```bash
git add core/vision/fusion_attrs.py tests/test_fusion_attrs.py
git commit -m "feat: add per-attribute fusion engine (fusion_attrs.py) — validation-as-gate, conservative default"
```

---

### Task 4: Vision Structured Extractor [Agent 4 — vision-extractor-builder]

**Files:**
- Create: `core/vision/vision_attr_extractor.py`

> **IMPORTANT**: Acest modul este pentru **cloud providers NUMAI** (gpt-4o-mini, claude-haiku).
> Ollama rămâne pentru `product_type_hint` (3-5 words) — comportament neschimbat.
> Feature flag `enable_structured_vision=False` implicit.

#### Step 4.1: Write failing tests

- [ ] Adaugă în `tests/test_vision_pipeline.py` (sau creează `tests/test_vision_attr_extractor.py`):

```python
"""Tests for vision structured attribute extractor."""
import pytest
from unittest.mock import MagicMock, patch


def _make_data(valid_values: dict = None, restrictive: bool = True):
    data = MagicMock()
    vv = valid_values or {}
    def find_valid(val, cat_id, char_name):
        allowed = vv.get(char_name, set())
        if not allowed:
            return val if not restrictive else None
        for v in allowed:
            if v.casefold() == val.strip().casefold():
                return v
        return None
    data.find_valid.side_effect = find_valid
    data.valid_values.side_effect = lambda cat_id, char: vv.get(char, set())
    data.is_restrictive.return_value = restrictive
    return data


class TestParseVisionResponse:

    def test_parses_valid_json(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Negru", "Imprimeu:": "Logo"}'
        data = _make_data({"Culoare de baza": {"Negru"}, "Imprimeu:": {"Logo"}})
        result = _parse_vision_response(raw, {"Culoare de baza", "Imprimeu:"}, data, "cat1")
        assert result["Culoare de baza"] == "Negru"
        assert result["Imprimeu:"] == "Logo"

    def test_rejects_hallucinated_attr(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Negru", "Inventat:": "ceva"}'
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert "Inventat:" not in result

    def test_rejects_value_not_in_list(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Mov electric"}'  # not in valid values
        data = _make_data({"Culoare de baza": {"Negru", "Alb", "Rosu"}}, restrictive=True)
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert "Culoare de baza" not in result

    def test_fallback_returns_empty_on_invalid_json(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = "The product is a black t-shirt with logo print"  # plain text, not JSON
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert result == {}  # NEVER parse free text

    def test_strips_markdown_fence(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '```json\n{"Culoare de baza": "Negru"}\n```'
        data = _make_data({"Culoare de baza": {"Negru"}})
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert result["Culoare de baza"] == "Negru"

    def test_accepts_freeform_when_no_valid_values(self):
        from core.vision.vision_attr_extractor import _parse_vision_response
        raw = '{"Culoare de baza": "Albastru royal"}'
        data = _make_data({}, restrictive=False)
        result = _parse_vision_response(raw, {"Culoare de baza"}, data, "cat1")
        assert result["Culoare de baza"] == "Albastru royal"


class TestBuildVisionPrompt:

    def test_prompt_excludes_already_filled(self):
        from core.vision.vision_attr_extractor import _build_vision_extraction_prompt
        eligible = {"Culoare de baza": {"Negru", "Alb"}, "Imprimeu:": {"Logo", "Uni"}}
        existing = {"Culoare de baza": "Negru"}  # already filled
        prompt = _build_vision_extraction_prompt("Tricouri", "emag", eligible, existing)
        assert "Culoare de baza" not in prompt   # filled → excluded
        assert "Imprimeu:" in prompt

    def test_prompt_has_no_confidence_request(self):
        from core.vision.vision_attr_extractor import _build_vision_extraction_prompt
        eligible = {"Culoare de baza": {"Negru"}}
        prompt = _build_vision_extraction_prompt("Tricouri", "emag", eligible, {})
        assert "confidence" not in prompt.lower()   # never ask model for confidence

    def test_non_eligible_attrs_excluded(self):
        from core.vision.vision_attr_extractor import _build_vision_extraction_prompt
        # Material is non-visual — should be excluded from prompt
        eligible = {"Culoare de baza": {"Negru"}}  # Material never passed as eligible
        prompt = _build_vision_extraction_prompt("Tricouri", "emag", eligible, {})
        assert "Material" not in prompt
```

- [ ] Run: `python -m pytest tests/test_vision_attr_extractor.py -v 2>&1 | head -20` — Expected: **ImportError**

#### Step 4.2: Implement `vision_attr_extractor.py`

- [ ] Creează `core/vision/vision_attr_extractor.py`:

```python
"""
Cloud vision structured attribute extractor.

Trimite imaginea la un cloud vision provider (OpenAI gpt-4o-mini sau Anthropic claude-haiku)
și returnează atribute validate contra listei permise a marketplace-ului.

DESIGN DECISIONS:
- Ollama NU este folosit aici (llava-phi3 nu face JSON structurat fiabil cu policy tables).
- Confidence auto-raportat de model NU este cerut sau folosit. Gate-ul primar = data.find_valid().
- Fallback la {} întotdeauna (niciodată nu parsăm text liber din vision).
- Feature flag: enable_structured_vision=False implicit.
"""
from __future__ import annotations
import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional
from PIL import Image
from core.app_logger import get_logger

log = get_logger("marketplace.vision.extractor")

_VISION_SYSTEM = (
    "You are a visual product attribute extractor for e-commerce catalogs.\n"
    "Analyze the product image and identify ONLY clearly visible attributes.\n\n"
    "You can detect: color, print/pattern, sleeve length, shoe profile (low/mid/high-top), "
    "product type (t-shirt/hoodie/jacket/pants/shoes), closure type (laces/velcro/zipper/slip-on), "
    "gender target (if clearly visible on image).\n\n"
    "You DO NOT guess: material, season, size, age, or sport type.\n\n"
    "Return ONLY valid JSON: {\"Attribute Name\": \"exact value from list\"}\n"
    "If unsure about any attribute → OMIT it.\n"
    "Zero text outside JSON. No markdown. No confidence scores."
)


@dataclass
class VisionExtractionResult:
    extracted_attrs: dict       = field(default_factory=dict)
    # e.g. {"Culoare de baza": "Negru", "Imprimeu:": "Logo"}
    raw_response: str           = ""
    provider_used: str          = ""
    latency_ms: int             = 0
    success: bool               = False
    error: str                  = ""
    attrs_requested: int        = 0
    attrs_accepted: int         = 0


def _build_vision_extraction_prompt(
    category: str,
    marketplace: str,
    eligible_attrs: dict,        # {char_name: set_of_allowed_values} — ONLY vision-eligible
    existing_chars: dict,        # already filled — exclude from prompt
    yolo_label: str = "",
    clip_label: str = "",
) -> str:
    """
    Construiește promptul pentru extracție structurată vision.

    NU cere confidence din model (numerele generate nu sunt calibrate).
    NU include atribute deja completate din text.
    NU include atribute non-vizuale (Material, Sezon, Marime etc).
    """
    # Filtrează câmpurile deja completate
    to_extract = {
        ch: vals for ch, vals in eligible_attrs.items()
        if not existing_chars.get(ch)
    }
    if not to_extract:
        return ""

    # Context vizual
    ctx_parts = [f"Category: {category}", f"Marketplace: {marketplace}"]
    if yolo_label:
        ctx_parts.append(f"YOLO detected: {yolo_label}")
    if clip_label:
        ctx_parts.append(f"CLIP label: {clip_label}")

    # Blocul de atribute cu valorile permise
    attr_lines = []
    for ch_name, vals in to_extract.items():
        if vals:
            sample = json.dumps(sorted(vals)[:15], ensure_ascii=False)
            attr_lines.append(f'  "{ch_name}": choose from {sample}')
        else:
            attr_lines.append(f'  "{ch_name}": (free text — describe briefly)')

    return (
        " | ".join(ctx_parts) + "\n\n"
        "Extract ONLY these attributes from the image (visually determinable only):\n"
        + "\n".join(attr_lines) + "\n\n"
        "Return JSON only: {\"attr_name\": \"exact_value\", ...}\n"
        "Omit any attribute you cannot clearly see. No confidence scores needed."
    )


def _parse_vision_response(
    raw: str,
    eligible_char_names: set,   # set of char names that were requested
    data,                       # MarketplaceData
    cat_id,
) -> dict:
    """
    Parsează răspunsul vision și validează contra listei permise.

    IMPORTANT: Dacă JSON-ul nu se parsează → returnează {} (NICIODATĂ text liber).
    Valorile validate contra data.find_valid() — singurul gate fiabil.
    """
    if not raw or not raw.strip():
        return {}

    # Strip markdown fences
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    # Parse JSON — fallback {} (nu parsăm text liber)
    parsed = None
    try:
        parsed = json.loads(text)
    except Exception:
        m = re.search(r"\{[^{}]*\}", text)
        if m:
            try:
                parsed = json.loads(m.group())
            except Exception:
                pass

    if not isinstance(parsed, dict):
        log.warning("vision_extractor: răspuns non-JSON ignorat (len=%d)", len(raw))
        return {}

    result = {}
    for ch_name, ch_val in parsed.items():
        # Reject hallucinated attribute names
        if ch_name not in eligible_char_names:
            log.debug("vision_extractor: atribut necerut '%s' ignorat (hallucination)", ch_name)
            continue

        val_str = str(ch_val).strip()
        if not val_str:
            continue

        # Gate primar: validare contra listei permise
        mapped = data.find_valid(val_str, cat_id, ch_name)
        if mapped is not None:
            result[ch_name] = mapped
            log.debug("vision_extractor: [%s]=%r validat → %r", ch_name, ch_val, mapped)
        else:
            valid_set = data.valid_values(cat_id, ch_name)
            if not valid_set and not data.is_restrictive(cat_id, ch_name):
                # Freeform câmp — acceptă direct
                result[ch_name] = val_str
                log.debug("vision_extractor: [%s]=%r acceptat freeform", ch_name, ch_val)
            else:
                log.warning(
                    "vision_extractor: [%s]=%r REJECTED — nu e în lista permisă",
                    ch_name, ch_val,
                )

    return result


def extract_attrs_cloud(
    img: Image.Image,
    category: str,
    marketplace: str,
    eligible_attrs: dict,        # {char_name: set_of_allowed_values}
    existing_chars: dict,
    data,
    cat_id,
    vision_provider,             # BaseVisionProvider — MUST be cloud (openai/anthropic)
    yolo_label: str = "",
    clip_label: str = "",
    timeout_s: int = 15,
) -> VisionExtractionResult:
    """
    Extrage atribute structurate din imagine via cloud vision provider.

    Apelul este gated de feature flag în image_analyzer.py.
    Nu folosește Ollama (llava-phi3 nu e fiabil pentru structured JSON).
    """
    result = VisionExtractionResult()

    if not eligible_attrs:
        result.error = "No eligible attributes"
        return result

    prompt = _build_vision_extraction_prompt(
        category, marketplace, eligible_attrs, existing_chars, yolo_label, clip_label,
    )
    if not prompt:
        result.error = "All eligible attrs already filled"
        return result

    result.attrs_requested = len([
        ch for ch in eligible_attrs if not existing_chars.get(ch)
    ])

    t_start = time.perf_counter()
    try:
        if not vision_provider.is_available():
            result.error = "Vision provider unavailable"
            return result

        raw = vision_provider.analyze(img, prompt)
        result.raw_response = raw
        result.latency_ms = round((time.perf_counter() - t_start) * 1000)
        result.provider_used = getattr(vision_provider, "name", "unknown")

        eligible_names = set(eligible_attrs.keys())
        extracted = _parse_vision_response(raw, eligible_names, data, cat_id)
        result.extracted_attrs = extracted
        result.attrs_accepted = len(extracted)
        result.success = True

        log.info(
            "vision_extractor: %d/%d atribute extrase (provider=%s latency=%dms)",
            result.attrs_accepted, result.attrs_requested,
            result.provider_used, result.latency_ms,
        )

    except Exception as exc:
        result.latency_ms = round((time.perf_counter() - t_start) * 1000)
        result.error = str(exc)[:200]
        result.success = False
        log.error("vision_extractor: eroare %s", exc, exc_info=True)

    return result
```

#### Step 4.3: Run tests

- [ ] `python -m pytest tests/test_vision_attr_extractor.py -v`
- [ ] Expected: **ALL PASS**

#### Step 4.4: Commit Task 4

```bash
git add core/vision/vision_attr_extractor.py tests/test_vision_attr_extractor.py
git commit -m "feat: add cloud vision structured extractor — validation-as-gate, no model confidence, {} fallback"
```

---

### Task 5: Fix Ollama num_predict [Agent 5 — vision-provider-patcher]

**Files:**
- Modify: `core/vision/visual_provider.py` line ~83

#### Step 5.1: Update num_predict

- [ ] Read `core/vision/visual_provider.py` liniile 76-92
- [ ] Găsește:
```python
"options": {"temperature": 0.05, "num_predict": 80},
```
- [ ] Înlocuiește cu:
```python
"options": {"temperature": 0.05, "num_predict": 200},
```

- [ ] Verifică: `python -c "from core.vision.visual_provider import OllamaVisionProvider; p = OllamaVisionProvider(); print('OK')"` — no errors

#### Step 5.2: Commit Task 5

```bash
git add core/vision/visual_provider.py
git commit -m "fix: increase Ollama num_predict 80→200 for complete product_type_hint responses"
```

---

## Phase 2 — Integration (Agent 6, după ce Phase 1 e completă)

### Task 6: Integrează extractor + fusion în image_analyzer.py

**Files:**
- Modify: `core/vision/image_analyzer.py`

#### Step 6.1: Adaugă `enable_structured_vision` param și imports

- [ ] Read `core/vision/image_analyzer.py` liniile 148-171 (semnătura `analyze_product_image`)
- [ ] Adaugă parametrul `enable_structured_vision: bool = False` la semnătură (după `save_debug`):

```python
def analyze_product_image(
    image_url: str,
    category: str,
    existing_chars: dict,
    valid_values_for_cat: dict,
    mandatory_chars: list,
    marketplace: str = "",
    offer_id: str = "",
    enable_color: bool = True,
    enable_product_hint: bool = False,
    vision_provider=None,
    sku: str = "",
    enable_yolo: bool = False,
    enable_clip: bool = False,
    yolo_model: str = "yolov8n.pt",
    clip_model: str = "ViT-B-32",
    yolo_conf: float = 0.35,
    clip_conf: float = 0.25,
    suggestion_only: bool = False,
    save_debug: bool = False,
    run_logger=None,
    # ── Structured vision (cloud only, default off) ────────────────────────
    enable_structured_vision: bool = False,   # NEW — feature flag
    cloud_vision_provider=None,               # NEW — cloud provider instance
    data=None,                                # NEW — MarketplaceData pentru validare
    cat_id=None,                              # NEW — category id pentru find_valid
) -> ImageAnalysisResult:
```

#### Step 6.2: Adaugă blocul de extracție structurată

- [ ] Read `core/vision/image_analyzer.py` liniile 393-432 (zona după CLIP, înainte de product_hint)
- [ ] Adaugă DUPĂ blocul CLIP și ÎNAINTEA blocului product_hint:

```python
    # ── Structured vision extraction (cloud only) ─────────────────────────
    if enable_structured_vision and cloud_vision_provider is not None and data is not None:
        try:
            from core.vision.visual_rules import load_rules, is_vision_eligible
            from core.vision.vision_attr_extractor import extract_attrs_cloud

            _rules = load_rules()

            # Construiește dict de atribute eligibile (NUMAI vizuale, din policy table)
            # Exclude câmpuri deja completate din text + cele non-eligibile
            _eligible = {
                ch: vals
                for ch, vals in valid_values_for_cat.items()
                if is_vision_eligible(ch, _rules)
                and not existing_chars.get(ch)  # text nu le-a completat deja
            }

            if _eligible and cat_id is not None:
                _extr = extract_attrs_cloud(
                    img=working_img,
                    category=category,
                    marketplace=marketplace,
                    eligible_attrs=_eligible,
                    existing_chars=existing_chars,
                    data=data,
                    cat_id=cat_id,
                    vision_provider=cloud_vision_provider,
                    yolo_label=result.detected_object,
                    clip_label=result.clip_best_label,
                )

                if _extr.success and _extr.extracted_attrs:
                    # Aplică CONSERVATOR: numai câmpuri goale
                    for ch, val in _extr.extracted_attrs.items():
                        if not result.suggested_attributes.get(ch):
                            result.suggested_attributes[ch] = val
                            result.used_for_attribute_fill = True

                    log.info(
                        "[Vision] Structured extracted %d attrs for offer=%s: %s",
                        _extr.attrs_accepted, offer_id,
                        list(_extr.extracted_attrs.keys()),
                    )
                    if run_logger:
                        run_logger.log(
                            "structured_vision", "extracted",
                            offer_id=offer_id, image_url=image_url,
                            status="ok", duration_ms=_extr.latency_ms,
                            level="INFO",
                            data={
                                "provider": _extr.provider_used,
                                "attrs_requested": _extr.attrs_requested,
                                "attrs_accepted": _extr.attrs_accepted,
                                "extracted": _extr.extracted_attrs,
                            },
                        )

        except Exception as e:
            log.error("[Vision] Structured extraction error offer=%s: %s", offer_id, e)
```

#### Step 6.3: Test integrare backwards-compat

- [ ] Run:
```python
python -c "
from core.vision.image_analyzer import analyze_product_image
# Fără parametrii noi — comportament identic cu v1
from unittest.mock import MagicMock
result = analyze_product_image(
    image_url='',
    category='Tricouri',
    existing_chars={},
    valid_values_for_cat={},
    mandatory_chars=[],
)
assert result.skipped_reason  # URL invalid → skip
print('Backwards compat OK')
"
```
- [ ] Run full tests: `python -m pytest tests/test_vision_pipeline.py -v`

#### Step 6.4: Commit Task 6

```bash
git add core/vision/image_analyzer.py
git commit -m "feat: integrate structured vision extraction in image_analyzer — feature flag off by default, cloud only"
```

---

## Phase 3 — Observability + Tests (Parallel, Agent 7+8)

### Task 7: DuckDB Schema Migration

**Files:**
- Modify: `core/reference_store_duckdb.py`

#### Step 7.1: Adaugă coloane fusion în schema

- [ ] Read `core/reference_store_duckdb.py` — găsește funcția `ensure_schema()`
- [ ] Adaugă la finalul listei de migrări idempotente:

```python
# ── v2 prompt pipeline additions ─────────────────────────────────────────
"ALTER TABLE char_source_detail ADD COLUMN IF NOT EXISTS vision_signal TEXT",
"ALTER TABLE char_source_detail ADD COLUMN IF NOT EXISTS vision_confidence FLOAT",
"ALTER TABLE char_source_detail ADD COLUMN IF NOT EXISTS fusion_action TEXT",
"ALTER TABLE char_source_detail ADD COLUMN IF NOT EXISTS conflict_flag BOOLEAN DEFAULT FALSE",
```

- [ ] Test migrare idempotentă:
```bash
python -c "from core.reference_store_duckdb import ensure_schema; ensure_schema(); ensure_schema(); print('Migration OK (idempotent)')"
```

#### Step 7.2: Commit Task 7

```bash
git add core/reference_store_duckdb.py
git commit -m "feat: add fusion observability columns to char_source_detail — vision_signal, fusion_action, conflict_flag"
```

---

### Task 8: Tests Finale + Regression

#### Step 8.1: Run full test suite

- [ ] `python -m pytest tests/ -v --tb=short 2>&1 | tail -30`
- [ ] Expected: zero failures pe testele existente
- [ ] Verifică că există teste pentru:
  - `test_fusion_attrs.py` — 8+ teste ✓
  - `test_vision_attr_extractor.py` — 6+ teste ✓
  - `test_vision_pipeline.py` — policy table tests ✓
  - `test_char_validation.py` — nemodificat, trebuie să treacă ✓

#### Step 8.2: Smoke test prompt-uri

```python
python -c "
from core.ai_enricher import _build_char_system_prompt, _build_prompt, _build_batch_system_prompt

# System prompt
sp = _build_char_system_prompt('emag')
assert '_src' in sp
assert '_reasoning' not in sp
assert 'Dri-FIT' in sp  # brand hints
print('System prompt OK')

# User prompt
up = _build_prompt(
    title='Nike Dri-FIT Running Tee - Barbati - XL',
    description='Tricou din material Dri-FIT pentru alergare. Culoare neagra. Material poliester 100%.',
    category='Tricouri sport barbati',
    existing={},
    char_options={
        'Culoare de baza': {'Negru', 'Alb', 'Rosu'},
        'Material:': {'Bumbac', 'Poliester', 'Fleece'},
        'Pentru:': {'Barbati', 'Femei'},
    },
    marketplace='emag',
    mandatory_set={'Culoare de baza'},
    product_meta={'brand': 'Nike', 'ean': '5059808284765'},
)
assert 'PRODUS:' in up
assert 'BRAND: Nike' in up
assert 'EAN:5059808284765' in up or 'EAN: 5059808284765' in up
assert '[OBLIGATORIU]' in up
assert len(up) > 100
print('User prompt OK')

# Batch prompt
bp = _build_batch_system_prompt('emag', ['Tricouri sport barbati', 'Hanorace femei'])
assert 'Tricouri sport barbati' in bp
assert 'Nike Dri-FIT' in bp  # examples
print('Batch prompt OK')

print('ALL SMOKE TESTS PASS')
"
```

#### Step 8.3: Final commit

```bash
git add .
git commit -m "test: final regression + smoke tests for prompt pipeline v2"
```

---

## Verificare finală (run după toate task-urile)

```bash
# 1. Toate testele trec
python -m pytest tests/ -v --tb=short

# 2. Nicio junk file în root
ls *.py 2>/dev/null || echo "No junk .py files in root"

# 3. Feature flags verificate (structured vision off by default)
python -c "
import inspect
from core.vision.image_analyzer import analyze_product_image
sig = inspect.signature(analyze_product_image)
assert sig.parameters['enable_structured_vision'].default is False
print('Feature flag OFF by default — OK')
"

# 4. Policy table loaded corect
python -c "
from core.vision.visual_rules import is_vision_eligible, load_rules
rules = load_rules()
assert is_vision_eligible('Culoare de baza', rules)
assert not is_vision_eligible('Material:', rules)
assert not is_vision_eligible('Marime:', rules)
print('Policy table OK')
"
```

---

## Rollback Plan

Fiecare task are commit separat. Rollback granular:

```bash
# Rollback un singur task
git revert <commit-hash> --no-edit

# Rollback complet la starea anterioară
git revert HEAD~8..HEAD --no-edit  # ajustează numărul de commits
```

Feature flag pentru structured vision:
```bash
# Dezactivare completă structured vision fără code change
# Simplu nu pasezi enable_structured_vision=True în process.py
```

---

## Cost Estimate (corect, post-feedback senior)

| Operație | Provider | Cost/produs | 10k produse |
|---|---|---|---|
| Text enrichment | orice LLM | ~$0.0001 | ~$1 |
| Structured vision | gpt-4o-mini | ~$0.0003 | ~$3 |
| Structured vision | claude-haiku | ~$0.0004 | ~$4 |
| Ollama (local) | product_hint | $0 | $0 |

> **Nu** $0.01-0.03/imagine — aceea era prețul modelelor mari (GPT-4o full) sau prețuri vechi (2023).
