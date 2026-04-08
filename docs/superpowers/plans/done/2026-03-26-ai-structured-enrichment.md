# AI Structured Enrichment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade AI characteristic enrichment cu structured outputs (JSON Schema), cross-marketplace knowledge store și telemetry DuckDB pentru metrici accept/reject/cost.

**Architecture:** Adăugăm 2 tabele DuckDB (`product_knowledge`, `ai_run_log`) în store-ul existent, un `SchemaBuilder` care selectează mandatory + top-N characteristics, upgradăm `AnthropicProvider` cu tool_use pentru structured outputs, și conectăm totul în `ai_enricher.py` ca să consume knowledge store înainte de a apela LLM-ul.

**Tech Stack:** DuckDB, Anthropic SDK (tool_use), Python 3.11+, Streamlit (metrici dashboard)

---

## Context codebase (citește înainte de a scrie cod)

- `core/reference_store_duckdb.py` — DDL + CRUD pentru DuckDB. Adaugă tabele noi AICI.
- `core/ai_enricher.py` — logica principală de enrichment AI. Modificat pentru enrichment cross-marketplace.
- `core/ai_logger.py` — logging JSON în fișiere. Completat cu write în `ai_run_log` DuckDB.
- `core/providers/anthropic_provider.py` — provider Anthropic. Upgradeat cu tool_use.
- `core/char_validator.py` — gate de validare strict. NU se modifică.
- `core/llm_router.py` — router singleton. NU se modifică.
- `pages/diagnostic.py` — pagina de diagnostică Streamlit. Adăugăm tab de metrici AI.

---

## File Structure

```
core/
  reference_store_duckdb.py     MODIFY — adaugă DDL + CRUD pentru product_knowledge și ai_run_log
  schema_builder.py             CREATE — selectează mandatory + top-N characteristics pentru prompt
  ai_logger.py                  MODIFY — adaugă write_run_log() care scrie în DuckDB
  ai_enricher.py                MODIFY — consumă knowledge store + folosește structured outputs
  providers/
    anthropic_provider.py       MODIFY — adaugă complete_structured() cu tool_use
    base.py                     MODIFY — adaugă complete_structured() în interfața abstractă

pages/
  diagnostic.py                 MODIFY — adaugă tab "AI Metrics" cu accept/reject/cost

tests/
  test_schema_builder.py        CREATE
  test_product_knowledge.py     CREATE
  test_structured_provider.py   CREATE
```

---

## Task 1: Tabele DuckDB — `product_knowledge` și `ai_run_log`

**Files:**
- Modify: `core/reference_store_duckdb.py`
- Test: `tests/test_product_knowledge.py`

### Ce face fiecare tabel

`product_knowledge` — stochează atributele validate ale unui produs (keyed pe EAN sau brand+title). Folosit ca context pentru viitoarele enrichment-uri cross-marketplace.

`ai_run_log` — telemetry per apel AI: tokens, cost, accept/reject count, durată.

- [ ] **Step 1: Citește fișierul existent**

```bash
# Citești core/reference_store_duckdb.py integral — înțelege pattern-ul DDL existent
# Caută _DDL_STATEMENTS și metodele de CRUD existente
```

- [ ] **Step 2: Scrie testul pentru product_knowledge**

Adaugă fișierul `tests/test_product_knowledge.py`:

```python
"""Tests for product_knowledge table CRUD."""
import json
import pytest
import duckdb
from unittest.mock import patch
from pathlib import Path


@pytest.fixture
def tmp_db(tmp_path):
    """DuckDB in-memory via temp path pentru izolare teste."""
    db_path = tmp_path / "test.duckdb"
    with patch("core.reference_store_duckdb.DB_PATH", db_path):
        from core.reference_store_duckdb import ensure_schema, upsert_product_knowledge, get_product_knowledge
        ensure_schema()
        yield db_path, upsert_product_knowledge, get_product_knowledge


def test_upsert_and_retrieve_by_ean(tmp_db):
    db_path, upsert, get = tmp_db
    upsert(
        ean="5901234123457",
        brand="Samsung",
        normalized_title="samsung galaxy s24 128gb negru",
        marketplace="emag_hu",
        offer_id="OFF-001",
        category="Telefoane",
        final_attributes={"Culoare": "Negru", "Capacitate": "128GB"},
        confidence=0.95,
        run_id="run-test-001",
    )
    result = get(ean="5901234123457")
    assert result is not None
    assert result["brand"] == "Samsung"
    assert result["final_attributes"]["Culoare"] == "Negru"
    assert result["confidence"] == 0.95


def test_retrieve_by_brand_title_fallback(tmp_db):
    db_path, upsert, get = tmp_db
    upsert(
        ean=None,
        brand="Nike",
        normalized_title="nike air max 90 alb",
        marketplace="allegro",
        offer_id="OFF-002",
        category="Încălțăminte sport",
        final_attributes={"Culoare": "Alb", "Mărime": "42"},
        confidence=0.80,
        run_id="run-test-002",
    )
    result = get(ean=None, brand="Nike", normalized_title="nike air max 90 alb")
    assert result is not None
    assert result["final_attributes"]["Mărime"] == "42"


def test_upsert_updates_existing(tmp_db):
    db_path, upsert, get = tmp_db
    upsert(
        ean="1111111111111",
        brand="Sony",
        normalized_title="sony wh1000xm5",
        marketplace="emag_hu",
        offer_id="OFF-003",
        category="Căști",
        final_attributes={"Culoare": "Negru"},
        confidence=0.70,
        run_id="run-v1",
    )
    # Upsert cu date mai noi
    upsert(
        ean="1111111111111",
        brand="Sony",
        normalized_title="sony wh1000xm5",
        marketplace="allegro",
        offer_id="OFF-004",
        category="Căști",
        final_attributes={"Culoare": "Negru", "Conectivitate": "Bluetooth 5.2"},
        confidence=0.92,
        run_id="run-v2",
    )
    result = get(ean="1111111111111")
    assert result["confidence"] == 0.92
    assert "Conectivitate" in result["final_attributes"]


def test_no_result_for_unknown_ean(tmp_db):
    db_path, upsert, get = tmp_db
    result = get(ean="0000000000000")
    assert result is None
```

- [ ] **Step 3: Rulează testele — verifică că FAIL**

```bash
cd "C:\Users\Robert Cazacu\Desktop\Propel"
python -m pytest tests/test_product_knowledge.py -v 2>&1 | head -30
```
Expected: `ImportError` sau `AttributeError` (funcțiile nu există încă)

- [ ] **Step 4: Adaugă DDL și CRUD în `reference_store_duckdb.py`**

Adaugă în `_DDL_STATEMENTS` (la sfârșitul listei, înainte de `]`):

```python
    """
    CREATE TABLE IF NOT EXISTS product_knowledge (
        ean               VARCHAR,
        brand             VARCHAR,
        normalized_title  VARCHAR NOT NULL,
        marketplace       VARCHAR NOT NULL,
        offer_id          VARCHAR NOT NULL,
        category          VARCHAR NOT NULL,
        final_attributes  VARCHAR NOT NULL,
        confidence        DOUBLE NOT NULL DEFAULT 0.0,
        run_id            VARCHAR,
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_pk_ean
        ON product_knowledge(ean)
        WHERE ean IS NOT NULL
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_pk_brand_title
        ON product_knowledge(brand, normalized_title)
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_run_log (
        run_id            VARCHAR NOT NULL,
        ean               VARCHAR,
        offer_id          VARCHAR,
        marketplace       VARCHAR NOT NULL,
        model_used        VARCHAR NOT NULL,
        tokens_input      INTEGER NOT NULL DEFAULT 0,
        tokens_output     INTEGER NOT NULL DEFAULT 0,
        cost_usd          DOUBLE NOT NULL DEFAULT 0.0,
        fields_requested  INTEGER NOT NULL DEFAULT 0,
        fields_accepted   INTEGER NOT NULL DEFAULT 0,
        fields_rejected   INTEGER NOT NULL DEFAULT 0,
        retry_count       INTEGER NOT NULL DEFAULT 0,
        fallback_used     BOOLEAN NOT NULL DEFAULT FALSE,
        duration_ms       INTEGER NOT NULL DEFAULT 0,
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
```

Adaugă funcțiile CRUD la sfârșitul fișierului `reference_store_duckdb.py`:

```python
# ── product_knowledge CRUD ─────────────────────────────────────────────────────

def upsert_product_knowledge(
    *,
    ean: str | None,
    brand: str,
    normalized_title: str,
    marketplace: str,
    offer_id: str,
    category: str,
    final_attributes: dict,
    confidence: float,
    run_id: str,
) -> None:
    """Insert sau update în product_knowledge.

    Cheia de matching: EAN (dacă există) sau brand+normalized_title.
    DOAR valorile validate (care au trecut char_validator) trebuie salvate.
    """
    attrs_json = json.dumps(final_attributes, ensure_ascii=False)
    con = duckdb.connect(str(DB_PATH))
    try:
        if ean:
            # Upsert by EAN
            con.execute("""
                INSERT INTO product_knowledge
                    (ean, brand, normalized_title, marketplace, offer_id,
                     category, final_attributes, confidence, run_id, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT (ean) DO UPDATE SET
                    brand             = excluded.brand,
                    normalized_title  = excluded.normalized_title,
                    marketplace       = excluded.marketplace,
                    offer_id          = excluded.offer_id,
                    category          = excluded.category,
                    final_attributes  = excluded.final_attributes,
                    confidence        = excluded.confidence,
                    run_id            = excluded.run_id,
                    updated_at        = CURRENT_TIMESTAMP
            """, [ean, brand, normalized_title, marketplace, offer_id,
                  category, attrs_json, confidence, run_id])
        else:
            # Upsert by brand+normalized_title (DELETE + INSERT pentru simplitate)
            con.execute("""
                DELETE FROM product_knowledge
                WHERE ean IS NULL
                  AND brand = ?
                  AND normalized_title = ?
            """, [brand, normalized_title])
            con.execute("""
                INSERT INTO product_knowledge
                    (ean, brand, normalized_title, marketplace, offer_id,
                     category, final_attributes, confidence, run_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [None, brand, normalized_title, marketplace, offer_id,
                  category, attrs_json, confidence, run_id])
    finally:
        con.close()


def get_product_knowledge(
    *,
    ean: str | None = None,
    brand: str | None = None,
    normalized_title: str | None = None,
) -> dict | None:
    """Caută în knowledge store. Prioritate: EAN > brand+title.

    Returnează dict cu toate câmpurile sau None dacă nu există.
    final_attributes este deja decodat ca dict.
    """
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        if ean:
            row = con.execute("""
                SELECT ean, brand, normalized_title, marketplace, offer_id,
                       category, final_attributes, confidence, run_id, updated_at
                FROM product_knowledge WHERE ean = ? LIMIT 1
            """, [ean]).fetchone()
        elif brand and normalized_title:
            row = con.execute("""
                SELECT ean, brand, normalized_title, marketplace, offer_id,
                       category, final_attributes, confidence, run_id, updated_at
                FROM product_knowledge
                WHERE ean IS NULL AND brand = ? AND normalized_title = ?
                LIMIT 1
            """, [brand, normalized_title]).fetchone()
        else:
            return None

        if row is None:
            return None

        cols = ["ean", "brand", "normalized_title", "marketplace", "offer_id",
                "category", "final_attributes", "confidence", "run_id", "updated_at"]
        result = dict(zip(cols, row))
        result["final_attributes"] = json.loads(result["final_attributes"])
        return result
    finally:
        con.close()


# ── ai_run_log write ───────────────────────────────────────────────────────────

def write_ai_run_log(
    *,
    run_id: str,
    ean: str | None,
    offer_id: str | None,
    marketplace: str,
    model_used: str,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    fields_requested: int,
    fields_accepted: int,
    fields_rejected: int,
    retry_count: int,
    fallback_used: bool,
    duration_ms: int,
) -> None:
    """Scrie o intrare de telemetry în ai_run_log."""
    con = duckdb.connect(str(DB_PATH))
    try:
        con.execute("""
            INSERT INTO ai_run_log
                (run_id, ean, offer_id, marketplace, model_used,
                 tokens_input, tokens_output, cost_usd,
                 fields_requested, fields_accepted, fields_rejected,
                 retry_count, fallback_used, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [run_id, ean, offer_id, marketplace, model_used,
              tokens_input, tokens_output, cost_usd,
              fields_requested, fields_accepted, fields_rejected,
              retry_count, fallback_used, duration_ms])
    finally:
        con.close()
```

Adaugă `import json` la importurile din fișier dacă nu există deja.

- [ ] **Step 5: Rulează testele — verifică că PASS**

```bash
python -m pytest tests/test_product_knowledge.py -v
```
Expected: toate 4 teste PASS

- [ ] **Step 6: Rulează ensure_schema() să creeze tabelele**

```bash
python -c "from core.reference_store_duckdb import ensure_schema; ensure_schema(); print('OK')"
```
Expected: `OK` fără erori

- [ ] **Step 7: Commit**

```bash
git add core/reference_store_duckdb.py tests/test_product_knowledge.py
git commit -m "feat: add product_knowledge and ai_run_log DuckDB tables"
```

---

## Task 2: Schema Builder — mandatory + top-N characteristics

**Files:**
- Create: `core/schema_builder.py`
- Test: `tests/test_schema_builder.py`

### Ce face

Primește categoria + lista completă de caracteristici și returnează o sublistă optimă pentru prompt:
- TOATE caracteristicile mandatory
- Top-N opționale scorificate după: prezență în knowledge store + cuvinte cheie în descriere + enum mic

**Limita recomandată:** maxim 20 caracteristici totale în schema.

- [ ] **Step 1: Scrie testul**

Creează `tests/test_schema_builder.py`:

```python
"""Tests for SchemaBuilder — mandatory + top-N selection."""
import pytest
from core.schema_builder import SchemaBuilder, build_json_schema


@pytest.fixture
def chars():
    """Simulează lista de caracteristici dintr-o categorie."""
    return [
        {"name": "Culoare",      "is_mandatory": True,  "values": ["Negru", "Alb", "Roșu", "Albastru"]},
        {"name": "Material",     "is_mandatory": True,  "values": ["Bumbac", "Poliester", "Lână"]},
        {"name": "Mărime",       "is_mandatory": True,  "values": ["XS", "S", "M", "L", "XL", "XXL"]},
        {"name": "Stil",         "is_mandatory": False, "values": ["Casual", "Sport", "Elegant"]},
        {"name": "Sezon",        "is_mandatory": False, "values": ["Vară", "Iarnă", "Primăvară", "Toamnă"]},
        {"name": "Brand",        "is_mandatory": False, "values": []},
        {"name": "Descriere",    "is_mandatory": False, "values": []},
        {"name": "Greutate",     "is_mandatory": False, "values": []},
        {"name": "Origine",      "is_mandatory": False, "values": ["România", "China", "Turcia"]},
        {"name": "Certificări",  "is_mandatory": False, "values": ["CE", "ISO", "OEKO-TEX"]},
    ]


def test_mandatory_always_included(chars):
    builder = SchemaBuilder(max_total=6)
    selected = builder.select(chars, description="tricou negru bumbac", known_attrs={})
    names = [c["name"] for c in selected]
    assert "Culoare" in names
    assert "Material" in names
    assert "Mărime" in names


def test_max_total_respected(chars):
    builder = SchemaBuilder(max_total=5)
    selected = builder.select(chars, description="", known_attrs={})
    assert len(selected) <= 5


def test_known_attrs_boost_optional(chars):
    """Caracteristicile prezente în knowledge store au prioritate la opționale."""
    builder = SchemaBuilder(max_total=6)
    selected = builder.select(
        chars,
        description="tricou",
        known_attrs={"Sezon": "Vară"},  # Sezon cunoscut → prioritate
    )
    names = [c["name"] for c in selected]
    assert "Sezon" in names


def test_description_keyword_boost(chars):
    """Keyword match în descriere ridică scorul caracteristicii."""
    builder = SchemaBuilder(max_total=5)
    selected = builder.select(
        chars,
        description="produs de origine română, sezon vară",
        known_attrs={},
    )
    names = [c["name"] for c in selected]
    # "origine" și "sezon" apar în descriere — ar trebui să fie incluse dacă nu depășim limita
    # (3 mandatory + 2 opționale = 5)
    assert len(selected) <= 5


def test_build_json_schema(chars):
    builder = SchemaBuilder(max_total=20)
    selected = builder.select(chars, description="", known_attrs={})
    schema = build_json_schema(selected)
    assert schema["type"] == "object"
    assert "Culoare" in schema["properties"]
    assert schema["properties"]["Culoare"]["enum"] == ["Negru", "Alb", "Roșu", "Albastru"]
    assert "Culoare" in schema["required"]
    assert schema["properties"]["Brand"]["type"] == "string"
    assert "enum" not in schema["properties"]["Brand"]


def test_empty_characteristics(chars):
    builder = SchemaBuilder(max_total=20)
    selected = builder.select([], description="", known_attrs={})
    assert selected == []
```

- [ ] **Step 2: Rulează testele — verifică că FAIL**

```bash
python -m pytest tests/test_schema_builder.py -v 2>&1 | head -20
```
Expected: `ModuleNotFoundError: No module named 'core.schema_builder'`

- [ ] **Step 3: Implementează `core/schema_builder.py`**

```python
"""
Schema Builder — selectează mandatory + top-N characteristics pentru prompt AI.

Logică de scoring pentru opționale:
  +3  dacă atributul există în knowledge store (known_attrs)
  +2  dacă numele apare ca keyword în descriere
  +1  dacă enum-ul are sub 10 valori (ușor de procesat pentru model)

Limita implicită: max_total=20 caracteristici în schema finală.
"""
from __future__ import annotations


class SchemaBuilder:
    def __init__(self, max_total: int = 20):
        self.max_total = max_total

    def select(
        self,
        characteristics: list[dict],
        description: str,
        known_attrs: dict,
    ) -> list[dict]:
        """Selectează caracteristicile pentru prompt.

        Args:
            characteristics: lista completă din categorie.
                Fiecare element: {"name": str, "is_mandatory": bool, "values": list[str]}
            description: descrierea produsului (pentru keyword boost)
            known_attrs: dict de atribute deja cunoscute din knowledge store

        Returns:
            Sublistă ordonată: mandatory first, apoi opționale cu cel mai mare scor.
        """
        if not characteristics:
            return []

        mandatory = [c for c in characteristics if c.get("is_mandatory")]
        optional = [c for c in characteristics if not c.get("is_mandatory")]

        desc_lower = description.lower()
        known_lower = {k.lower() for k in known_attrs}

        scored_optional: list[tuple[int, dict]] = []
        for char in optional:
            score = 0
            name_lower = char["name"].lower()
            if name_lower in known_lower:
                score += 3
            if name_lower in desc_lower:
                score += 2
            values = char.get("values", [])
            if 0 < len(values) < 10:
                score += 1
            scored_optional.append((score, char))

        scored_optional.sort(key=lambda x: x[0], reverse=True)

        remaining_slots = max(0, self.max_total - len(mandatory))
        top_optional = [char for _, char in scored_optional[:remaining_slots]]

        return mandatory + top_optional


def build_json_schema(characteristics: list[dict]) -> dict:
    """Construiește JSON Schema din lista selectată de caracteristici.

    Caracteristici cu valori enum → "enum" constraint.
    Caracteristici freeform (values=[]) → plain "string".
    Toate sunt marcate ca "required".
    """
    if not characteristics:
        return {"type": "object", "properties": {}, "required": []}

    properties: dict = {}
    required: list[str] = []

    for char in characteristics:
        name = char["name"]
        values = char.get("values", [])
        required.append(name)

        if values:
            properties[name] = {"type": "string", "enum": values}
        else:
            properties[name] = {"type": "string"}

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
```

- [ ] **Step 4: Rulează testele — verifică că PASS**

```bash
python -m pytest tests/test_schema_builder.py -v
```
Expected: toate 6 teste PASS

- [ ] **Step 5: Commit**

```bash
git add core/schema_builder.py tests/test_schema_builder.py
git commit -m "feat: add SchemaBuilder for mandatory + top-N characteristic selection"
```

---

## Task 3: Structured Outputs — `AnthropicProvider.complete_structured()`

**Files:**
- Modify: `core/providers/base.py`
- Modify: `core/providers/anthropic_provider.py`
- Test: `tests/test_structured_provider.py`

### Ce face

Adaugă metodă `complete_structured(prompt, schema, system)` în provider care folosește Anthropic **tool_use** pentru a forța răspunsul să fie JSON conform schemei date. Modelul recomandat pentru structured outputs: **claude-sonnet-4-6** (nu Haiku).

### De ce tool_use, nu JSON mode?

Anthropic nu are "JSON mode" explicit — în schimb, definești un "tool" cu input_schema = schema ta, și ceri modelului să "apeleze" acel tool. Răspunsul vine garantat valid conform schemei.

- [ ] **Step 1: Scrie testul (cu mock)**

Creează `tests/test_structured_provider.py`:

```python
"""Tests for AnthropicProvider.complete_structured() via tool_use."""
import json
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture
def provider():
    with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "sk-ant-test-key"}):
        with patch("anthropic.Anthropic") as mock_anthropic_cls:
            from core.providers.anthropic_provider import AnthropicProvider
            p = AnthropicProvider.__new__(AnthropicProvider)
            p._client = mock_anthropic_cls.return_value
            yield p


def _make_tool_response(data: dict):
    """Simulează un răspuns Anthropic cu tool_use block."""
    block = MagicMock()
    block.type = "tool_use"
    block.input = data
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=150, output_tokens=50)
    return response


def test_complete_structured_returns_dict(provider):
    schema = {
        "type": "object",
        "properties": {"Culoare": {"type": "string", "enum": ["Negru", "Alb"]}},
        "required": ["Culoare"],
    }
    provider._client.messages.create.return_value = _make_tool_response({"Culoare": "Negru"})

    result = provider.complete_structured(
        prompt="Produs: tricou negru din bumbac",
        schema=schema,
    )
    assert result == {"Culoare": "Negru"}


def test_complete_structured_calls_tool_use(provider):
    schema = {"type": "object", "properties": {"X": {"type": "string"}}, "required": ["X"]}
    provider._client.messages.create.return_value = _make_tool_response({"X": "val"})

    provider.complete_structured(prompt="test", schema=schema)

    call_kwargs = provider._client.messages.create.call_args[1]
    assert "tools" in call_kwargs
    assert call_kwargs["tool_choice"]["type"] == "tool"


def test_complete_structured_returns_none_on_no_tool_block(provider):
    """Dacă modelul nu returnează tool_use block, returnăm None."""
    block = MagicMock()
    block.type = "text"
    block.text = "Nu am putut genera."
    response = MagicMock()
    response.content = [block]
    response.usage = MagicMock(input_tokens=50, output_tokens=10)
    provider._client.messages.create.return_value = response

    result = provider.complete_structured(prompt="test", schema={"type": "object", "properties": {}, "required": []})
    assert result is None
```

- [ ] **Step 2: Rulează testele — verifică că FAIL**

```bash
python -m pytest tests/test_structured_provider.py -v 2>&1 | head -20
```
Expected: `AttributeError: 'AnthropicProvider' object has no attribute 'complete_structured'`

- [ ] **Step 3: Adaugă `complete_structured` în `base.py`**

Citește `core/providers/base.py` și adaugă metoda abstractă:

```python
def complete_structured(
    self,
    prompt: str,
    schema: dict,
    system: str | None = None,
) -> dict | None:
    """Completare cu structured output conform JSON Schema.

    Returnează dict conform schemei sau None dacă modelul nu poate genera.
    Implementarea implicită face fallback la complete() + json.loads.
    Override în provideri care suportă tool_use nativ.
    """
    import json
    system_msg = system or "Returnează DOAR JSON valid, fără text suplimentar."
    raw = self.complete(prompt, max_tokens=500, system=system_msg)
    try:
        # Curăță markdown code blocks dacă există
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        return json.loads(text.strip())
    except Exception:
        return None
```

- [ ] **Step 4: Adaugă `complete_structured` în `anthropic_provider.py`**

Citește `core/providers/anthropic_provider.py` și adaugă după metoda `complete()`:

```python
_STRUCTURED_MODEL = "claude-sonnet-4-6"

def complete_structured(
    self,
    prompt: str,
    schema: dict,
    system: str | None = None,
) -> dict | None:
    """Structured output via Anthropic tool_use.

    Forțează modelul să returneze JSON conform schemei.
    Folosește claude-sonnet-4-6 indiferent de modelul default al providerului.

    Returns:
        dict conform schemei sau None dacă nu s-a obținut tool_use block.
    """
    tool_def = {
        "name": "fill_characteristics",
        "description": "Completează caracteristicile produsului cu valori corecte.",
        "input_schema": schema,
    }
    kwargs = dict(
        model=_STRUCTURED_MODEL,
        max_tokens=1024,
        tools=[tool_def],
        tool_choice={"type": "tool", "name": "fill_characteristics"},
        messages=[{"role": "user", "content": prompt}],
    )
    if system:
        kwargs["system"] = system

    msg = self._client.messages.create(**kwargs)

    for block in msg.content:
        if getattr(block, "type", None) == "tool_use":
            return block.input  # dict deja, nu string

    return None
```

- [ ] **Step 5: Rulează testele — verifică că PASS**

```bash
python -m pytest tests/test_structured_provider.py -v
```
Expected: toate 3 teste PASS

- [ ] **Step 6: Commit**

```bash
git add core/providers/base.py core/providers/anthropic_provider.py tests/test_structured_provider.py
git commit -m "feat: add complete_structured() with tool_use to AnthropicProvider"
```

---

## Task 4: Cross-marketplace enrichment în `ai_enricher.py`

**Files:**
- Modify: `core/ai_enricher.py`
- Modify: `core/ai_logger.py`

### Ce face

1. Înainte de a construi prompt-ul, caută în `product_knowledge` după EAN sau brand+title.
2. Dacă găsește date cunoscute, le adaugă ca context suplimentar în prompt.
3. După validare, salvează atributele acceptate în `product_knowledge`.
4. Loghează run-ul în `ai_run_log` prin `write_ai_run_log()`.

### Funcție helper de normalizare titlu

Titlurile trebuie normalizate consistent (lowercase, fără diacritice, fără semne de punctuație) pentru matching corect.

- [ ] **Step 1: Citește `core/ai_enricher.py` integral**

Caută funcția `enrich_with_ai()` — aceasta e punctul de intrare principal. Înțelege parametrii și return value înainte de a modifica.

- [ ] **Step 2: Adaugă helper `_normalize_title()` în `ai_enricher.py`**

Adaugă după importuri, înainte de prima funcție:

```python
import unicodedata

def _normalize_title(title: str) -> str:
    """Normalizează titlul pentru knowledge store matching.

    'Samsung Galaxy S24 128GB Negru!' → 'samsung galaxy s24 128gb negru'
    """
    # Lowercase
    s = title.lower().strip()
    # Remove diacritice
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    # Remove punctuatie, păstrează alfanumeric și spații
    s = re.sub(r"[^\w\s]", " ", s)
    # Collapse whitespace
    s = re.sub(r"\s+", " ", s).strip()
    return s
```

- [ ] **Step 3: Integrează knowledge store în `enrich_with_ai()`**

Localizează funcția `enrich_with_ai()` în `ai_enricher.py`. Adaugă la începutul funcției (după validările inițiale, înainte de construirea prompt-ului):

```python
from core.reference_store_duckdb import get_product_knowledge, upsert_product_knowledge, write_ai_run_log
from core.schema_builder import SchemaBuilder, build_json_schema

# ── Knowledge store lookup ──────────────────────────────────────────────────
_norm_title = _normalize_title(title)
_known = get_product_knowledge(ean=ean, brand=brand, normalized_title=_norm_title) if (ean or brand) else None
_known_attrs = _known["final_attributes"] if _known else {}

if _known_attrs:
    log.debug("Knowledge store hit: %d atribute cunoscute pentru '%s'", len(_known_attrs), title[:50])
```

Unde `ean` și `brand` sunt parametri ai funcției (adaugă-i dacă nu există: `ean: str | None = None, brand: str | None = None`).

Adaugă contextul în prompt (localizează unde se construiește prompt-ul și adaugă):

```python
if _known_attrs:
    known_context = "\n".join(f"  {k}: {v}" for k, v in _known_attrs.items())
    prompt += f"\n\nDate cunoscute din alte marketplace-uri (verificate):\n{known_context}"
```

- [ ] **Step 4: Salvează în knowledge store după validare**

La finalul `enrich_with_ai()`, după ce ai `validated_chars` (atributele care au trecut char_validator), adaugă:

```python
# ── Save to knowledge store (doar atribute validate) ───────────────────────
if validated_chars and (ean or brand):
    try:
        upsert_product_knowledge(
            ean=ean,
            brand=brand or "",
            normalized_title=_norm_title,
            marketplace=marketplace,
            offer_id=str(offer_id),
            category=str(category),
            final_attributes=validated_chars,
            confidence=round(len(validated_chars) / max(len(missing_chars), 1), 2),
            run_id=run_id or "unknown",
        )
    except Exception as e:
        log.warning("Nu s-a putut salva în knowledge store: %s", e)
```

- [ ] **Step 5: Adaugă telemetry write în `ai_logger.py`**

La finalul fișierului `core/ai_logger.py` adaugă:

```python
def write_run_to_duckdb(
    *,
    run_id: str,
    ean: str | None,
    offer_id: str | None,
    marketplace: str,
    model_used: str,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    fields_requested: int,
    fields_accepted: int,
    fields_rejected: int,
    retry_count: int,
    fallback_used: bool,
    duration_ms: int,
) -> None:
    """Scrie telemetry în ai_run_log DuckDB. Silent fail dacă DuckDB nu e disponibil."""
    try:
        from core.reference_store_duckdb import write_ai_run_log
        write_ai_run_log(
            run_id=run_id, ean=ean, offer_id=offer_id,
            marketplace=marketplace, model_used=model_used,
            tokens_input=tokens_input, tokens_output=tokens_output,
            cost_usd=cost_usd, fields_requested=fields_requested,
            fields_accepted=fields_accepted, fields_rejected=fields_rejected,
            retry_count=retry_count, fallback_used=fallback_used,
            duration_ms=duration_ms,
        )
    except Exception:
        pass  # Telemetry nu blochează niciodată procesarea
```

- [ ] **Step 6: Rulează testele existente să nu fie broken**

```bash
python -m pytest tests/ -v --tb=short 2>&1 | tail -20
```
Expected: toate testele existente PASS + cele noi adăugate de noi.

- [ ] **Step 7: Commit**

```bash
git add core/ai_enricher.py core/ai_logger.py
git commit -m "feat: cross-marketplace enrichment via product_knowledge + telemetry to DuckDB"
```

---

## Task 5: Metrics tab în `pages/diagnostic.py`

**Files:**
- Modify: `pages/diagnostic.py`

### Ce face

Adaugă un tab "AI Metrics" în pagina de diagnostică existentă cu:
- Accept rate (%) per marketplace
- Avg cost per offer ($)
- Retry rate (%)
- Fallback rate (%)
- Top 10 cele mai procesate categorii

- [ ] **Step 1: Citește `pages/diagnostic.py`**

Înțelege cum e structurată pagina (taburi, componente Streamlit). Caută pattern-ul de taburi existent.

- [ ] **Step 2: Adaugă tab-ul AI Metrics**

Localizează unde sunt definite taburile (`st.tabs([...])`) și adaugă `"AI Metrics"`.

În corpul tab-ului adaugă:

```python
with tab_ai_metrics:
    st.subheader("AI Run Metrics")

    try:
        import duckdb
        from core.reference_store_duckdb import DB_PATH

        con = duckdb.connect(str(DB_PATH), read_only=True)

        # ── Summary KPIs ────────────────────────────────────────────────
        summary = con.execute("""
            SELECT
                COUNT(*) AS total_runs,
                ROUND(AVG(CAST(fields_accepted AS DOUBLE) / NULLIF(fields_requested, 0)) * 100, 1) AS accept_rate_pct,
                ROUND(AVG(cost_usd), 6) AS avg_cost_usd,
                ROUND(SUM(CAST(retry_count > 0 AS INTEGER)) * 100.0 / NULLIF(COUNT(*), 0), 1) AS retry_rate_pct,
                ROUND(SUM(CAST(fallback_used AS INTEGER)) * 100.0 / NULLIF(COUNT(*), 0), 1) AS fallback_rate_pct
            FROM ai_run_log
        """).fetchone()

        if summary and summary[0] > 0:
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("Total Runs", f"{summary[0]:,}")
            col2.metric("Accept Rate", f"{summary[1] or 0:.1f}%", delta_color="normal")
            col3.metric("Avg Cost/Offer", f"${summary[2] or 0:.5f}")
            col4.metric("Retry Rate", f"{summary[3] or 0:.1f}%")

            # ── Per marketplace ────────────────────────────────────────
            st.markdown("#### Pe marketplace")
            df_mp = con.execute("""
                SELECT
                    marketplace,
                    COUNT(*) AS runs,
                    ROUND(AVG(CAST(fields_accepted AS DOUBLE) / NULLIF(fields_requested, 0)) * 100, 1) AS accept_rate,
                    ROUND(SUM(cost_usd), 4) AS total_cost_usd
                FROM ai_run_log
                GROUP BY marketplace
                ORDER BY runs DESC
                LIMIT 10
            """).df()
            st.dataframe(df_mp, use_container_width=True)

            # ── Knowledge store size ───────────────────────────────────
            pk_count = con.execute("SELECT COUNT(*) FROM product_knowledge").fetchone()[0]
            st.info(f"Knowledge store: **{pk_count:,}** produse indexate")
        else:
            st.info("Nu există date de telemetry încă. Procesează câteva oferte pentru a vedea metrici.")

        con.close()

    except Exception as e:
        st.warning(f"AI Metrics indisponibil: {e}")
```

- [ ] **Step 3: Verifică că pagina pornește fără erori**

```bash
python -c "import pages.diagnostic; print('OK')"
```
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add pages/diagnostic.py
git commit -m "feat: add AI Metrics tab to diagnostic page"
```

---

## Task 6: Verificare finală + cleanup

- [ ] **Step 1: Rulează toate testele**

```bash
python -m pytest tests/ -v --tb=short
```
Expected: toate PASS, 0 failures

- [ ] **Step 2: Verifică că nu există fișiere junk în root**

```bash
ls "C:/Users/Robert Cazacu/Desktop/Propel" | grep -v -E "(CLAUDE|README|app\.py|core|data|docs|pages|scripts|tests|requirements|start_all|fix_|package|\.git)"
```
Expected: niciun fișier necunoscut

- [ ] **Step 3: Build check**

```bash
python -c "
from core.reference_store_duckdb import ensure_schema, upsert_product_knowledge, get_product_knowledge, write_ai_run_log
from core.schema_builder import SchemaBuilder, build_json_schema
from core.ai_logger import write_run_to_duckdb
ensure_schema()
print('All imports OK')
"
```
Expected: `All imports OK`

- [ ] **Step 4: Commit final**

```bash
git add .
git commit -m "chore: final integration check — structured enrichment complete"
```

---

## Ordine de implementare recomandată

```
Task 1 → Task 2 → Task 3 → Task 4 → Task 5 → Task 6
  ↑           ↑        ↑        ↑
DuckDB    Schema   Provider  Enricher
tables   builder  upgrade   integration
```

Task 4 depinde de Task 1 (knowledge store) și Task 3 (structured provider).
Task 5 depinde de Task 1 (ai_run_log tabel).
Task 2 și Task 3 sunt independente — pot fi paralele.

---

## Referințe rapide

- Validator existent (NU modifica): `core/char_validator.py:validate_new_chars_strict()`
- Modul DuckDB: `core/reference_store_duckdb.py` — adaugă DDL în `_DDL_STATEMENTS`
- Modele Anthropic recomandate: Haiku pentru categorii, **Sonnet 4.6 pentru structured outputs**
- Cost estimat Sonnet 4.6: ~$0.003/1k input tokens, ~$0.015/1k output tokens
- Accept rate target: >80% | Retry rate target: <10%
