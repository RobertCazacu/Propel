# Marketplace Offer Processor

A Streamlit-based internal tool for automatically correcting product offer errors across multiple marketplaces — eMAG Romania, eMAG HU, eMAG BG, Trendyol, Trendyol BG, Trendyol GR, Allegro, Decathlon, Pepita, FashionDays, FashionDays BG, TemuRO and more.

---

## What it does

Takes an offer export file from a marketplace platform and automatically fixes:

| Error code | Marketplace | Meaning | Action |
|---|---|---|---|
| **1007** | eMAG RO/HU/BG | Missing or incorrect category | Resolved via keyword rules or AI batch classification |
| **1009** | eMAG RO/HU/BG | Missing mandatory characteristics | Filled via rules + AI enrichment |
| **1010** | eMAG RO/HU/BG | Invalid characteristic values | Removed and re-populated |
| **107** | eMAG HU/BG, FashionDays BG | Characteristic error variant | Same as 1009/1010 |
| **empty** | eMAG HU | Offer without error code | Processed as standard enrichment |
| **3111** | Trendyol / Trendyol BG / Trendyol GR | Missing characteristics | Filled via rules + AI enrichment |
| **3210** | FashionDays BG | Category / characteristic error | Resolved via rules + AI |
| **7119 / 7124 / 7127** | TemuRO | Missing or invalid characteristics | Filled via rules + AI enrichment |

---

## Features

- **Multi-marketplace support** — each marketplace has its own reference data (categories, characteristics, allowed values) stored in **DuckDB**
- **Rule-based category mapping** — keyword rules with AND logic, exclude terms, and specificity ordering; auto-learned from AI decisions
- **Fast rule pre-processing** — rules validated and sorted O(N_rules) once per run instead of O(N_products × N_rules)
- **AI batch classification** — up to 60 products classified per API call; fuzzy matching accepts near-identical category names (non-ASCII BG/HU supported)
- **AI characteristic enrichment** — fills mandatory missing fields; respects each marketplace's language (Romanian, Hungarian, Bulgarian, Polish, etc.)
- **Cross-marketplace knowledge store** — validated attributes from any marketplace saved in DuckDB `product_knowledge` table; re-used on subsequent runs at zero cost
- **AI structured output** — `complete_structured()` with JSON schema (off / shadow / on rollout modes)
- **Vision pipeline** — image-based attribute extraction using color detection, YOLO object detection, CLIP semantic validation, and vision LLM (Ollama llava-phi3)
- **Multi-provider LLM** — swap between Anthropic, Ollama, Gemini, Groq or Mistral from the UI or `.env`, no restart needed
- **Pre-processing cost estimate** — shows estimated token usage and USD cost before processing starts
- **Persistent AI cache** — processed products are cached; re-runs cost zero tokens
- **Auto-save export** — corrected Excel file generated and saved automatically after processing; download button appears immediately
- **Sleep prevention** — blocks Windows standby/hibernate during processing via `SetThreadExecutionState`; resets automatically when done
- **Rate limit resilience** — exponential backoff with jitter prevents thundering herd; 2 parallel batch workers
- **Color mapper** — maps detected colors to marketplace valid values via multilingual synonym clusters (18 canonical clusters for RO/HU/BG), fuzzy scoring (rapidfuzz token_set_ratio + Jaccard), and semantic reranking (sentence-transformers); auto-accepts at 0.82 confidence, soft-review at 0.68
- **Characteristic resolver V2.1** — 3-pass fallback for AI-suggested values that fail strict validation: Pass 1 fuzzy match on allowed values, Pass 2 Ollama local repair (budgeted at 2 calls/product), Pass 3 adaptive floor rescue; locale-aware prompts via `config/locale_registry.json`
- **UI "Needs Review" expander** — low-confidence fills surfaced with top-3 suggestions for manual selection
- **Per-attribute vision fusion** — `fusion_attrs.py` engine applies text+vision fusion per attribute with 5 decision cases and `find_valid()` as gate; controlled per-attribute via `visual_rules.json` `attribute_fusion_policy` table
- **Color-coded Excel export** — each type of change has a distinct color; non-mandatory color chars filled when category supports them; missing mandatory characteristics highlighted in red
- **Mandatory-no-values warning** — surfaces a dedicated warning when a mandatory characteristic has no valid values defined in reference data (data import issue), instead of silently skipping
- **Thread safety** — merge lock on learned rule deduplication, double-checked locking in LLM singleton, write lock on marketplace import
- **SSRF protection** — image fetcher blocks RFC1918 / loopback IPs before any download
- **DuckDB telemetry** — every AI run logged to `ai_run_log` table with token counts, latency, structured output metrics, vision signal and fusion action columns
- **Public access** — Cloudflare Tunnel + Telegram bot notification on startup

---

## Project structure

```
marketplace_tool/
├── app.py                          # Streamlit entry point
├── start_all.py                    # Auto-start: Streamlit + Cloudflare Tunnel + Telegram
├── .env.example                    # Environment variable template
├── requirements.txt
│
├── core/
│   ├── ai_enricher.py              # AI enrichment with cache, knowledge store, structured output
│   ├── llm_router.py               # Singleton LLM router — single access point for all AI calls
│   ├── loader.py                   # Loads categories / characteristics / values from DuckDB
│   ├── reference_store_duckdb.py   # DuckDB DDL + CRUD: marketplaces, product_knowledge, ai_run_log
│   ├── schema_builder.py           # SchemaBuilder — selects mandatory + top-N chars for structured output
│   ├── processor.py                # Rule-based + AI processing pipeline
│   ├── offers_parser.py            # Parses marketplace offer export files
│   ├── exporter.py                 # Generates color-coded Excel output
│   ├── state.py                    # Session state + persistent statistics
│   ├── templates.py                # Excel template generators for reference files
│   ├── app_logger.py               # Centralized logger
│   ├── logger.py                   # Processing logs with 7-day auto-cleanup
│   ├── ai_logger.py                # AI request/response logger + write_run_to_duckdb telemetry
│   ├── vision/
│   │   ├── __init__.py             # Exports analyze_product_image, ImageAnalysisResult
│   │   ├── image_analyzer.py       # Main orchestrator: color + YOLO + CLIP + vision LLM fusion
│   │   ├── color_analyzer.py       # Algorithmic color detection (PIL quantize, HSV classification)
│   │   ├── image_fetcher.py        # Image downloader with local cache + SSRF protection
│   │   ├── visual_provider.py      # Vision model providers (Ollama llava-phi3, Mock)
│   │   ├── visual_rules.py         # JSON rules engine + attribute_fusion_policy table
│   │   ├── yolo_detector.py        # YOLO object detection + crop pipeline (skip < 0.50 conf)
│   │   ├── clip_validator.py       # CLIP semantic category validation
│   │   ├── fusion_attrs.py         # Per-attribute text+vision fusion engine (5 decision cases)
│   │   ├── vision_attr_extractor.py # Structured cloud-only attribute extraction
│   │   └── vision_logger.py        # Per-run image analysis log
│   ├── color_mapper/
│   │   ├── __init__.py             # Public API: map_color()
│   │   ├── types.py                # ColorMappingResult, ColorCandidate
│   │   ├── normalize.py            # NFKD diacritics strip, separator normalization
│   │   ├── synonyms.py             # 18 canonical multilingual synonym clusters (RO/HU/BG)
│   │   ├── scoring.py              # Fuzzy + hybrid scoring + threshold logic
│   │   ├── embedder.py             # Lazy sentence-transformers singleton for semantic rerank
│   │   └── mapper.py               # Orchestrator: exact → synonym → fuzzy → semantic
│   ├── characteristic_resolver.py  # 3-pass resolver: fuzzy + Ollama repair + adaptive floor
│   └── providers/
│       ├── base.py                 # Abstract BaseLLMProvider
│       ├── anthropic_provider.py   # Anthropic Claude (SDK) — complete() + complete_structured()
│       ├── ollama_provider.py      # Ollama local models (REST)
│       ├── openai_provider.py      # OpenAI-compatible endpoint (REST)
│       ├── gemini_provider.py      # Google Gemini (REST)
│       ├── groq_provider.py        # Groq (REST, OpenAI-compatible)
│       └── mistral_provider.py     # Mistral AI (REST, OpenAI-compatible)
│
├── pages/
│   ├── dashboard.py                # Metrics dashboard + run history
│   ├── setup.py                    # Load marketplace reference files into DuckDB
│   ├── process.py                  # Process offers (rules + AI + image); auto-saves export
│   ├── results.py                  # View results + export Excel (format original / model import)
│   ├── diagnostic.py               # System diagnostic + AI Metrics tab (DuckDB telemetry)
│   └── llm_providers.py            # AI provider management (switch, configure, test)
│
├── config/
│   └── locale_registry.json        # Marketplace → ISO language code mapping (RO/HU/BG/PL/…)
│
└── data/
    ├── reference_data.duckdb       # All marketplace reference data + knowledge store + telemetry
    ├── ai_cache.json               # Persistent AI cache (gitignored)
    ├── dashboard_stats.json        # Cumulative statistics (gitignored)
    ├── visual_rules.json           # Image analysis rules (color thresholds, per-category overrides)
    ├── exports/                    # Auto-saved processed Excel files, 24h retention (gitignored)
    ├── logs/                       # Processing logs, 7-day retention (gitignored)
    ├── ai_logs/                    # AI request/response logs, 24h retention (gitignored)
    └── image_cache/                # Downloaded product images, persistent (gitignored)
```

---

## Installation

```bash
git clone https://github.com/your-username/marketplace-tool.git
cd marketplace-tool

pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys

streamlit run app.py
```

App opens at `http://localhost:8501`.

---

## Usage

### Step 1 — Setup Marketplace

1. Go to **⚙️ Setup Marketplace**
2. Select or add a marketplace
3. Upload the 3 reference Excel files:
   - **Categories** (`emag_categories.xlsx`) — columns: `id`, `name`, `parent_id`
   - **Characteristics** (`emag_characteristics.xlsx`) — columns: `id`, `category_id`, `name`, `mandatory`
   - **Allowed values** (`characteristic_values.xlsx`) — columns: `emag_characteristic_id`, `value`
4. Click **Save** — data is stored in DuckDB (`data/reference_data.duckdb`), no need to re-upload

### Step 2 — Configure AI Provider (optional)

1. Go to **🤖 LLM Providers**
2. Choose a provider and enter the API key
3. Click **Activate** — takes effect immediately, no restart required

Without an AI provider the tool still works using rules only.

### Step 3 — Process Offers

1. Go to **📁 Process Offers**
2. Select the configured marketplace (active marketplace banner shows stats)
3. Upload the offer export file from the marketplace platform
4. (Optional) Add or edit category mapping rules
5. (Optional) Enable image analysis (color detection, YOLO, CLIP)
6. Expand **AI Cost Estimate** to preview token usage and USD cost
7. Click **Start processing for [Marketplace]**

When processing finishes the corrected Excel file is **generated and saved automatically** — a download button appears immediately, no need to navigate to Results.

### Step 4 — Results & Export (optional)

Go to **📊 Results** for detailed review, filtering, and alternative export formats (original format or model import format).

---

## Excel export colors

| Color | Meaning |
|---|---|
| Green | New characteristic added automatically |
| Blue | Category assigned (error 1007) |
| Orange | Category corrected |
| Red | Invalid value removed **or** mandatory characteristic still missing after processing |
| Yellow | Requires manual completion |

---

## AI — Multi-Provider System

The app supports 5 AI providers with a unified interface. All providers accept the same prompt format — no other code changes required when switching.

### Available providers

| Provider | Default model | Notes |
|---|---|---|
| **anthropic** | `claude-haiku-4-5-20251001` | Recommended — best quality/cost ratio; supports structured output |
| **ollama** | `qwen2.5:14b` | Free, runs locally — requires `ollama serve`; used for Pass 2 resolver repair |
| **openai** | `gpt-4o-mini` | OpenAI or any OpenAI-compatible endpoint |
| **gemini** | `gemini-2.0-flash` | Google — free tier available |
| **groq** | `llama-3.3-70b-versatile` | Free with rate limits, very fast |
| **mistral** | `mistral-small-latest` | Mistral AI |

### Switching providers

**Via UI:** Go to **🤖 LLM Providers** → click **Activate** on any provider.

**Via `.env`** (requires restart):
```env
ACTIVE_PROVIDER=groq
```

**At runtime** (no restart):
```python
from core.llm_router import switch_provider
switch_provider("groq")
```

### Marketplace language support

The AI prompt automatically includes the correct language context per marketplace:

| Marketplace | AI language |
|---|---|
| eMAG Romania | Romanian |
| eMAG HU | Hungarian |
| eMAG BG | Bulgarian |
| Allegro | Polish |
| Trendyol / Trendyol BG / Trendyol GR | neutral |
| Decathlon / Pepita / FashionDays | Romanian |
| FashionDays BG | Bulgarian |
| TemuRO | Romanian |

### Structured output (Anthropic only)

The tool supports JSON schema-constrained responses via `complete_structured()`:

| Mode | Behavior |
|---|---|
| `off` | Plain text enrichment only (default) |
| `shadow` | Both paths run; results compared but not used |
| `on` | Structured output replaces plain text; falls back on failure |

Configure from **⚙️ Setup** → Structured Output toggle, or via env:
```env
AI_STRUCTURED_MODE=shadow
AI_STRUCTURED_SAMPLE=0.10
```

### Cross-marketplace knowledge store

Validated attributes are stored in `product_knowledge` (DuckDB) keyed by EAN + brand + normalized title. On subsequent runs, AI is skipped for known products — zero cost.

### AI Request/Response Logging

Every AI call is saved to `data/ai_logs/YYYY-MM-DD.json` (auto-deleted after 24h) and to the `ai_run_log` DuckDB table (permanent telemetry).

Each entry contains:

| Field | Description |
|---|---|
| `timestamp` | ISO timestamp with milliseconds |
| `type` | `category_batch`, `char_enrichment` or `image_analysis` |
| `provider` / `model` | Active provider and model name |
| `marketplace` | Marketplace being processed |
| `duration_ms` | Response time in milliseconds |
| `request.offer_id(s)` | Offer ID(s) involved |
| `request.prompt` | Full prompt sent to the AI |
| `response.raw` | Raw text response from the AI |
| `results` | Final accepted values per offer |
| `stats.accepted` / `stats.rejected` | Count of accepted/rejected values |

View telemetry in **🔧 Diagnostic** → AI Metrics tab.

### Token optimizations

| Optimization | Saving |
|---|---|
| Batch classification (N products = 1 API call) | ~97% |
| Persistent cache (seen product = 0 tokens) | 100% on re-runs |
| Knowledge store (known EAN/brand = 0 tokens) | 100% cross-marketplace |
| AI only for missing mandatory fields | ~50% |
| Auto-learned rules from AI decisions | 100% over time |

### Pre-processing cost estimate

Before starting, expand **AI Cost Estimate** to see:
- Number of products requiring AI for category
- Number of batches
- Number of products requiring AI for characteristics
- **Estimated total cost in USD**

Based on `claude-haiku-4-5-20251001` pricing: $0.80/MTok input, $4.00/MTok output.

---

## Category mapping rules

Rules are evaluated before AI — matching a rule costs zero tokens.

```json
{"keywords": "sneaker, men", "exclude": "kids", "category": "Men's Sport Shoes"}
```

| Field | Description |
|---|---|
| `keywords` | Comma-separated words — **all** must appear in the product title (AND logic, case-insensitive) |
| `exclude` | Words that must **not** appear in the title (optional) |
| `category` | Exact category name from the marketplace reference data |

Rules with more keywords are checked first (more specific = higher priority).
Rules are pre-processed once per run (O(N_rules)) rather than per product — significant speedup for large batches.
Backward-compatible with old format: `{"prefix": "...", "category": "..."}`.

---

## Image analysis

Optional per-product image analysis via the **🖼️ Image Analysis** section in Process Offers:

| Option | Description |
|---|---|
| Color detection | Algorithmic color extraction (PIL quantize, HSV families) — no ML required |
| YOLO object detection | Crops the main product object before color/vision analysis |
| CLIP semantic validation | Validates that detected category matches image content |
| Vision LLM hint | Ollama llava-phi3 suggests product type from image |

Image results are merged into `new_chars` without overwriting text-based enrichment. Supports `first_only`, `best_confidence`, and `aggregate_vote` strategies when multiple images are present.

---

## Public access — Cloudflare Tunnel + Telegram

The app can be exposed publicly without a VPN using a free Cloudflare Tunnel.

### How it works

1. PC starts → Windows Task Scheduler runs `start_all.py` at logon
2. `start_all.py` starts Streamlit + Cloudflare Tunnel
3. The new public URL is sent automatically via **Telegram** within ~15 seconds
4. Accessible from anywhere — no VPN required

### Configuration (`.env`)

```env
TELEGRAM_TOKEN=your-bot-token       # from @BotFather on Telegram
TELEGRAM_CHAT_ID=your-chat-id       # from api.telegram.org/botTOKEN/getUpdates
STREAMLIT_PORT=8501                  # optional, default 8501
```

### Install Task Scheduler (PowerShell as Administrator)

```powershell
$action = New-ScheduledTaskAction `
    -Execute "python" `
    -Argument "C:\path\to\marketplace_tool\start_all.py" `
    -WorkingDirectory "C:\path\to\marketplace_tool"

$trigger = New-ScheduledTaskTrigger -AtLogon

$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Hours 0) `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)

Register-ScheduledTask `
    -TaskName "MarketplaceTool" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force
```

> **Note:** The Cloudflare URL changes on every restart — you receive the new URL on Telegram automatically.

---

## Environment variables reference

| Variable | Required | Default | Description |
|---|---|---|---|
| `ACTIVE_PROVIDER` | No | `anthropic` | Active LLM provider |
| `ANTHROPIC_API_KEY` | If using Anthropic | — | Anthropic API key |
| `ANTHROPIC_MODEL` | No | `claude-haiku-4-5-20251001` | Claude model |
| `OLLAMA_BASE_URL` | If using Ollama | `http://localhost:11434` | Ollama server URL |
| `OLLAMA_MODEL` | No | `qwen2.5:14b` | Ollama model name |
| `GEMINI_API_KEY` | If using Gemini | — | Google Gemini API key |
| `GEMINI_MODEL` | No | `gemini-2.0-flash` | Gemini model |
| `GROQ_API_KEY` | If using Groq | — | Groq API key |
| `GROQ_MODEL` | No | `llama-3.3-70b-versatile` | Groq model |
| `MISTRAL_API_KEY` | If using Mistral | — | Mistral API key |
| `MISTRAL_MODEL` | No | `mistral-small-latest` | Mistral model |
| `AI_STRUCTURED_MODE` | No | `off` | Structured output mode: `off` / `shadow` / `on` |
| `AI_STRUCTURED_SAMPLE` | No | `0.10` | Fraction of requests to run structured output on |
| `TELEGRAM_TOKEN` | No | — | Telegram bot token |
| `TELEGRAM_CHAT_ID` | No | — | Telegram chat ID |
| `STREAMLIT_PORT` | No | `8501` | Streamlit port |

---

## Useful commands

```bash
# Normal start
streamlit run app.py

# Full start (Streamlit + Tunnel + Telegram notification)
python start_all.py

# Migrate existing Parquet data to DuckDB
python scripts/migrate_parquet_to_duckdb.py
```

---

## Changelog

### v10 — 2026-04-21

- **Red highlight for missing mandatory chars** — after processing, any mandatory characteristic still unresolved is highlighted in red in the Excel export (previously only yellow for "needs manual"); makes mandatory gaps immediately visible without opening the results page
- **Mandatory-no-values warning** — when a mandatory characteristic has an empty valid-values list in reference data, a dedicated warning is logged and surfaced in the UI instead of silently failing; helps diagnose incomplete marketplace imports before assuming the product is unfillable
- **Detector fix: empty valid-values guard** — `detect_material`, `detect_sport`, and `detect_sistem_inchidere` now return `None` (skip) when the allowed-values set is empty rather than returning a hardcoded value that would fail validation; prevents false positives on marketplaces where those characteristics exist but have no values defined yet
- **AI array coercion** — when the AI returns a JSON array instead of a scalar string for a characteristic value (e.g. `["Piele", "Textil"]`), the system now coerces it to the best single value: finds the array element that matches an allowed value, or takes the first element; prevents silent rejection of otherwise correct AI responses
- **AI P2 prompt hardening** — system prompt rule P2 now explicitly instructs the model to return a single string value and `NEVER` return a JSON array; reduces frequency of array responses before coercion is needed
- **`_ai_cat_id` guard** — `_ai_cat_id` is verified non-None before being used in array coercion and value resolution logic; prevents `AttributeError` when category lookup fails mid-enrichment
- **New marketplaces in error code config** — `Trendyol BG`, `Trendyol GR`, `FashionDays BG`, `TemuRO` added to `error_codes_config.json` with their specific processable error codes
- **Exporter quality** — `None` cell values handled cleanly in Excel writer; `is_missing_mandatory` logic simplified; test coverage gap closed

### v9 — 2026-04-09

- **Color mapper** — new `core/color_mapper/` package; maps detected color strings to marketplace-valid values via: exact match → multilingual synonym clusters (18 canonical clusters, RO/HU/BG) → fuzzy scoring (rapidfuzz token_set_ratio + Jaccard) → semantic reranking (sentence-transformers); thresholds AUTO_ACCEPT=0.82, SOFT_REVIEW=0.68; replaces ad-hoc `_map_to_valid` in `image_analyzer.py`
- **Characteristic resolver V2.1** — `core/characteristic_resolver.py`; 3-pass fallback for values that fail strict `find_valid()` validation: Pass 1 fuzzy match on allowed values, Pass 2 Ollama local repair (budgeted 2 calls/product), Pass 3 adaptive floor rescue with near-tie guard; integrated into `ai_enricher.py` validation loop; structured resolver log saved per run (`*_ollama_resolver.json`)
- **Locale registry** — `config/locale_registry.json` maps each marketplace to an ISO language code; `_get_language_code()` in `ai_enricher.py` injects `Output language: Hungarian/Bulgarian/…` into system prompts; removes default gender-to-Romanian bias in batch prompts
- **UI "Needs Review" expander** — low-confidence fills in Process Offers show top-3 alternative suggestions for manual review; YOLO and vision provider warnings surfaced in UI
- **Per-attribute vision fusion** — `core/vision/fusion_attrs.py` implements a 5-case decision engine (text-only, vision-only, agree, text-wins, conflict); `attribute_fusion_policy` table in `visual_rules.json` controls `vision_eligible`, `min_conf`, and `conflict_action` per attribute; `vision_attr_extractor.py` handles structured cloud-only extraction with JSON-strict parsing
- **OpenAI provider** — `core/providers/openai_provider.py`; supports any OpenAI-compatible endpoint
- **Prompt pipeline v2** — `_reasoning` field replaced by `_src` audit field; description truncation raised 400 → 700 chars; 20 ordered fields in enrichment prompt (12 mandatory first); brand-to-material hints (e.g. Dri-FIT→Polyester); R1–R7 signal hierarchy; few-shot batch examples with multilingual gender keywords
- **Fill color for non-mandatory chars** — `_missing_color_char` now fills optional color characteristics (e.g. `Szín:`) when the char exists in the category's valid-values list, not only when mandatory; fixes silent skips on eMAG HU
- **ID mismatch fix** — `core/loader.py` builds `_internal_to_join` mapping (internal `category_id` → `emag_id`); all char-based indexes keyed consistently; `mandatory_chars()` and `valid_values()` now return correct results for eMAG HU/BG marketplaces
- **Security & thread-safety audit (P01–P30)** — `_merge_lock` protects learned rule deduplication; double-checked locking in `LLMRouter` singleton; `_WRITE_LOCK` guards `import_marketplace()`; SSRF protection in `image_fetcher` blocks RFC1918/loopback IPs; `_sanitize_for_prompt()` escapes injection chars + truncates to 300 chars; `WeakKeyDictionary` replaces `id(data)` cache key; DB indexes on categories, characteristics, characteristic_values; YOLO crop skipped when confidence < 0.50; regex-based JSON extraction in `complete_structured()`
- **`ai_run_log` schema migration** — adds `vision_signal`, `vision_confidence`, `fusion_action`, `conflict_flag` columns; `_run_migrations()` guards RENAME COLUMN idempotently

### v8 — 2026-03-27

- **Auto-save export** — corrected Excel (format model import) generated and saved automatically after processing completes; download button appears immediately on Process Offers page; no need to navigate to Results
- **Sleep prevention** — `_prevent_sleep()` calls `SetThreadExecutionState(ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED)` during processing; Windows cannot enter standby or hibernate; reset automatically when processing finishes
- **Fast rule pre-processing** — `_preprocess_rules()` validates categories, sorts, and pre-computes keywords O(N_rules) once before any per-product loop; eliminates O(N_products × N_rules) `category_id()` + `difflib` calls; ~1000× fewer lookups for typical batches
- **Rate limit jitter** — exponential backoff now adds ±50% random jitter per worker; prevents thundering herd when multiple batch workers hit rate limit simultaneously
- **Parallel batch workers reduced 5 → 2** — less simultaneous API pressure; combined with jitter, rate limit retries drop significantly
- **Fuzzy category matching** — `_match_category()` in `_process_batch`: exact → normalized (lowercase/strip) → `difflib` with cutoff 0.88; resolves non-ASCII category mismatches (Bulgarian, Hungarian) where AI response differs by 1–2 characters

### v7 — 2026-03-24

- **AI structured output** — `complete_structured()` added to `AnthropicProvider` using `tool_use` pattern; `SchemaBuilder` selects mandatory + top-N optional characteristics and builds JSON schema; three rollout modes: `off` (default), `shadow` (compare only), `on` (replace text path); UI toggle in Process Offers; configurable via `AI_STRUCTURED_MODE` / `AI_STRUCTURED_SAMPLE` env vars
- **Cross-marketplace knowledge store** — `product_knowledge` DuckDB table stores validated attributes keyed by EAN + brand + normalized title; `get_product_knowledge()` / `upsert_product_knowledge()` in `reference_store_duckdb.py`; `enrich_with_ai()` reads known attributes as AI context and saves validated results back
- **DuckDB telemetry** — `ai_run_log` table added; `write_run_to_duckdb()` called from `enrich_with_ai()` after every enrichment; stores tokens, cost, latency, structured output fields, shadow diff
- **Diagnostic → AI Metrics tab** — queries `ai_run_log` for per-marketplace stats, acceptance rates, structured output comparison
- **AI prompt metadata context** — EAN, SKU, weight, warranty from offer file included in enrichment prompts; improves quality for products with incomplete titles
- **`done_map` cache** — tracks which mandatory characteristics have been resolved per product; skips AI entirely when all mandatory fields already done

### v6 — 2026-03-23

- **DuckDB migration complete** — all marketplaces migrated from Parquet to DuckDB (`data/reference_data.duckdb`); `REFERENCE_BACKEND` flag removed (DuckDB is the only backend); `migrate_parquet_to_duckdb.py` utility for one-time migration
- **Bulk insert + vectorized validation** — values table import rewritten with bulk DuckDB insert; 1 million rows: 35 min → 6 seconds
- **`characteristic_id` stable key** — characteristics indexed by numeric ID in addition to name; resolves ambiguity when two characteristics share display names across categories
- **Restrictive flag** — characteristics marked as restrictive (fixed value list) vs non-restrictive (freeform); AI freeform values accepted for non-restrictive fields without list validation
- **Fuzzy characteristic matching** — `find_valid()` in `MarketplaceData` tries: exact → normalized → numeric EU format → diacritics normalization → difflib fuzzy; reduces AI rejection rate for minor spelling variants
- **Marketplace fallback values** — `marketplace_fallback_values()` aggregates valid values for a characteristic across all categories; used when category-level values are missing
- **Auto-save exports** — `_auto_save_export()` in `results.py` saves generated Excel to `data/exports/` with timestamp + marketplace slug; auto-cleans files older than 24h
- **Local path + CSV input** — Setup page accepts local file paths and CSV files in addition to `.xlsx` upload; useful for large marketplace files
- **Tests** — universal marketplace tests covering numeric + alphanumeric IDs, DuckDB isolation, end-to-end pipeline

### v5 — 2026-03-22

- **Image-based color detection** — new `core/vision/` package; analyzes product images algorithmically (Pillow + PIL quantize, no ML required)
  - `image_fetcher.py` — downloads + caches images by URL hash in `data/image_cache/`
  - `color_analyzer.py` — corner-based background removal, PIL FASTOCTREE quantize, neutral avoidance, HSV color family classification, white-product shortcut for Photoroom images
  - `visual_provider.py` — optional Ollama vision model integration (`llava-phi3`) for product type hints
  - `visual_rules.py` — JSON-based rules engine (`data/visual_rules.json`), per-category overrides
  - `image_analyzer.py` — main orchestrator; merges image results into `new_chars` without modifying existing text-based flow
- **YOLO + CLIP pipeline** — `yolo_detector.py` for object detection + crop; `clip_validator.py` for semantic category validation; fusion strategy (first_only / best_confidence / aggregate_vote)
- **Image analysis UI** — checkboxes in Process Offers: color detection, YOLO, CLIP, vision LLM; advanced settings expander
- **Image analysis logging** — `log_image_analysis()` added to `ai_logger.py`; every image analysis logged as `image_analysis` type entry
- **Fix: marketplace language context** — `_mp_ctx()` rewritten with `_MP_ALIASES` list supporting full permissive matching: `BG`, `bg`, `Bulgaria`, `BGN`, `HU`, `Hungary`, `Ungaria`, `HUF`, `PL`, `Polonia`, `Allegro`, `FashionDays BG/HU`, etc.
- **Fix: white product detection** — Photoroom images on white background correctly detected as "Alb"
- **Fix: comma-separated image URLs** — `Imagini` column may contain multiple URLs; only first URL used for analysis

### v4 — 2026-03-22

- **AI Request/Response Logger** — every AI call saved to `data/ai_logs/YYYY-MM-DD.json` with 24h retention; captures full prompt, raw response, parsed result, duration_ms, offer_id, provider, model, accepted/rejected counts
- `core/ai_logger.py` — new module: `log_category_batch()`, `log_char_enrichment()`, `AICallTimer`, `list_ai_log_files()`, `read_ai_log()`; auto-cleanup on every write
- `ai_enricher.py` — integrated timing and logging after each API call
- `processor.py` — `process_product()` accepts `offer_id` parameter

### v3 — 2026-03-21

- **Fix: eMAG HU / BG used Romanian context in AI** — `_mp_ctx()` rewritten with alias-based matching
- **UI: Active marketplace banner** in Process Offers — shows name + stats
- **AI cost estimator** — pre-processing estimate of token batches and USD cost
- **Multi-provider LLM architecture** — `core/providers/` with abstract `BaseLLMProvider` + 5 providers; `core/llm_router.py` singleton
- **LLM Providers page** — full UI for configuring, switching and testing all providers
- **`start_all.py` secured** — Telegram credentials moved to `.env`

### v2 — 2026-03-20

- Dynamic marketplace context in AI prompts (`_mp_ctx()`)
- All categories sent to AI (removed 250-category limit)
- Auto-learn brand filter (excludes ALL CAPS tokens and model codes from keyword rules)
- Public access via Cloudflare Tunnel + Telegram notification at startup
- Windows Task Scheduler auto-start

### v1 — initial release

- Core processing pipeline: category mapping, characteristic enrichment, value validation
- Rule-based engine with keyword AND logic
- Claude AI integration with persistent cache and batch classification
- Color-coded Excel export
- Dashboard with persistent cumulative statistics
