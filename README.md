# Marketplace Offer Processor

A Streamlit-based internal tool for automatically correcting product offer errors across multiple marketplaces — eMAG Romania, eMAG HU, eMAG BG, Trendyol, Allegro, Decathlon, Pepita, FashionDays and more.

---

## What it does

Takes an offer export file from a marketplace platform and automatically fixes:

| Error code | Meaning | Action |
|---|---|---|
| **1007** | Missing or incorrect category | Resolved via keyword rules or AI batch classification |
| **1009** | Missing mandatory characteristics | Filled via rules + AI enrichment |
| **1010** | Invalid characteristic values | Removed and re-populated |

---

## Features

- **Multi-marketplace support** — each marketplace has its own reference data (categories, characteristics, allowed values) stored in Parquet format
- **Rule-based category mapping** — keyword rules with AND logic, exclude terms, and specificity ordering
- **AI batch classification** — up to 60 products classified per API call; auto-learns rules from AI decisions
- **AI characteristic enrichment** — fills mandatory missing fields; respects each marketplace's language (Romanian, Hungarian, Bulgarian, Polish, etc.)
- **Multi-provider LLM** — swap between Anthropic, Ollama, Gemini, Groq or Mistral from the UI or `.env`, no restart needed
- **Pre-processing cost estimate** — shows estimated token usage and USD cost before processing starts
- **Persistent AI cache** — processed products are cached; re-runs cost zero tokens
- **Color-coded Excel export** — each type of change has a distinct color
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
│   ├── ai_enricher.py              # AI enrichment with persistent cache; delegates to LLMRouter
│   ├── llm_router.py               # Singleton LLM router — single access point for all AI calls
│   ├── loader.py                   # Loads categories / characteristics / values from Parquet
│   ├── processor.py                # Rule-based + AI processing pipeline
│   ├── offers_parser.py            # Parses marketplace offer export files
│   ├── exporter.py                 # Generates color-coded Excel output
│   ├── state.py                    # Session state + persistent statistics
│   ├── templates.py                # Excel template generators for reference files
│   ├── app_logger.py               # Centralized logger
│   ├── logger.py                   # Processing logs with 7-day auto-cleanup
│   ├── ai_logger.py                # AI request/response logger (24h retention)
│   ├── vision/
│   │   ├── __init__.py             # Exports analyze_product_image, ImageAnalysisResult
│   │   ├── image_analyzer.py       # Main orchestrator for image-based attribute extraction
│   │   ├── color_analyzer.py       # Algorithmic color detection (PIL quantize, HSV classification)
│   │   ├── image_fetcher.py        # Image downloader with local cache (data/image_cache/)
│   │   ├── visual_provider.py      # Vision model providers (Ollama llava-phi3, Mock)
│   │   └── visual_rules.py         # JSON rules engine (data/visual_rules.json)
│   └── providers/
│       ├── base.py                 # Abstract BaseLLMProvider
│       ├── anthropic_provider.py   # Anthropic Claude (SDK)
│       ├── ollama_provider.py      # Ollama local models (REST)
│       ├── gemini_provider.py      # Google Gemini (REST)
│       ├── groq_provider.py        # Groq (REST, OpenAI-compatible)
│       └── mistral_provider.py     # Mistral AI (REST, OpenAI-compatible)
│
├── pages/
│   ├── dashboard.py                # Metrics dashboard + run history
│   ├── setup.py                    # Load marketplace reference files
│   ├── process.py                  # Process offers (rules + AI)
│   ├── results.py                  # View results + export Excel
│   ├── diagnostic.py               # System diagnostic
│   └── llm_providers.py            # AI provider management (switch, configure, test)
│
└── data/
    ├── eMAG_Romania/
    │   ├── categories.parquet
    │   ├── characteristics.parquet
    │   └── values.parquet
    ├── eMAG_HU/                    # eMAG Hungary
    ├── eMAG_BG/                    # eMAG Bulgaria
    ├── Trendyol/
    ├── FashionDays/
    ├── FashionDays_BG/
    ├── ai_cache.json               # Persistent AI cache (gitignored)
    ├── dashboard_stats.json        # Cumulative statistics (gitignored)
    ├── visual_rules.json           # Image analysis rules (color thresholds, per-category overrides)
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
4. Click **Save** — data is stored as Parquet locally, no need to re-upload

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
5. Expand **AI Cost Estimate** to preview token usage and USD cost
6. Click **Start processing for [Marketplace]**

### Step 4 — Results & Export

1. Go to **📊 Results**
2. Review the processed products
3. Click **Generate corrected Excel** and download

---

## Excel export colors

| Color | Meaning |
|---|---|
| Green | New characteristic added automatically |
| Blue | Category assigned (error 1007) |
| Orange | Category corrected |
| Red | Invalid value removed |
| Yellow | Requires manual completion |

---

## AI — Multi-Provider System

The app supports 5 AI providers with a unified interface. All providers accept the same prompt format — no other code changes required when switching.

### Available providers

| Provider | Default model | Notes |
|---|---|---|
| **anthropic** | `claude-haiku-4-5-20251001` | Recommended — best quality/cost ratio |
| **ollama** | `qwen2.5:14b` | Free, runs locally — requires `ollama serve` |
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
| Trendyol / Decathlon / Pepita / FashionDays | neutral |

### AI Request/Response Logging

Every AI call is saved to `data/ai_logs/YYYY-MM-DD.json` (auto-deleted after 24h).

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
| `request.products` / `request.missing_characteristics` | Full input data |
| `response.raw` | Raw text response from the AI |
| `response.parsed` | Parsed JSON from the response |
| `results` | Final accepted values per offer |
| `stats.accepted` / `stats.rejected` | Count of accepted/rejected values |

Log files location: `data/ai_logs/` (gitignored — local only).

### Token optimizations

| Optimization | Saving |
|---|---|
| Batch classification (N products = 1 API call) | ~97% |
| Persistent cache (seen product = 0 tokens) | 100% on re-runs |
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
Backward-compatible with old format: `{"prefix": "...", "category": "..."}`.

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
    -Argument "C:\Users\manue\Desktop\marketplace_tool\start_all.py" `
    -WorkingDirectory "C:\Users\manue\Desktop\marketplace_tool"

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
> Keep the PC awake: `powercfg /change standby-timeout-ac 0` (PowerShell as Admin).

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

# One-time values.xlsx → Parquet conversion
python fix_values_parquet.py

# Check a Parquet file
python -c "import pandas as pd; df=pd.read_parquet('data/eMAG_Romania/values.parquet'); print(df.shape)"
```

---

## Changelog

### v5 — 2026-03-22

- **Image-based color detection** — new `core/vision/` package; analyzes product images algorithmically (Pillow + PIL quantize, no ML required)
  - `image_fetcher.py` — downloads + caches images by URL hash in `data/image_cache/`
  - `color_analyzer.py` — corner-based background removal, PIL FASTOCTREE quantize, neutral avoidance, HSV color family classification (Negru, Alb, Gri, Rosu, Albastru, Verde, Mov, Roz, Portocaliu, Galben, Maro, Bej, Turcoaz, Visiniu, Kaki, Bleumarin, Multicolor), white-product shortcut for Photoroom images
  - `visual_provider.py` — optional Ollama vision model integration (`llava-phi3`) for product type hints
  - `visual_rules.py` — JSON-based rules engine (`data/visual_rules.json`), per-category overrides
  - `image_analyzer.py` — main orchestrator; merges image results into `new_chars` without modifying existing text-based flow
- **Image analysis UI** — two checkboxes in Process Offers: "Detectează culoarea din imagine" and "Folosește imaginea pentru îmbunătățirea categoriei"
- **Image analysis logging** — `log_image_analysis()` added to `ai_logger.py`; every image analysis (success or failure) logged as `image_analysis` type entry in daily AI log
- **Fix: marketplace language context** — `_mp_ctx()` rewritten with `_MP_ALIASES` list supporting full permissive matching: `BG`, `bg`, `Bulgaria`, `BGN`, `HU`, `Hungary`, `Ungaria`, `HUF`, `PL`, `Polonia`, `Allegro`, `FashionDays BG/HU`, etc.
- **Fix: white product detection** — Photoroom images on white background now correctly detected as "Alb" instead of "Rosu" or "Bej"
- **Fix: comma-separated image URLs** — `Imagini` column may contain multiple URLs; only first URL used for analysis
- **Fix: `Imagini` column alias** — added `"imagini"` and `"image src"` to `offers_parser.py` image URL aliases
- **`requirements.txt`** — added `Pillow>=10.0.0`, `requests>=2.28.0`, `numpy>=1.24.0`
- **`.gitignore`** — added `data/image_cache/`

### v4 — 2026-03-22

- **AI Request/Response Logger** — every AI call saved to `data/ai_logs/YYYY-MM-DD.json` with 24h retention; captures full prompt, raw response, parsed result, duration_ms, offer_id, provider, model, accepted/rejected counts
- `core/ai_logger.py` — new module: `log_category_batch()`, `log_char_enrichment()`, `AICallTimer`, `list_ai_log_files()`, `read_ai_log()`; auto-cleanup on every write
- `ai_enricher.py` — integrated timing (`time.perf_counter`) and logging calls after each `get_router().complete()`
- `processor.py` — `process_product()` accepts `offer_id` parameter, passed through to `enrich_with_ai` → logger
- `pages/process.py` — passes `offer_id` to `process_product()`
- `.gitignore` — added `data/ai_logs/`

### v3 — 2026-03-21

- **Fix: eMAG HU / BG used Romanian context in AI** — `_mp_ctx()` substring matching sorted by key length descending; added explicit `emag_hu` (Hungarian) and `emag_bg` (Bulgarian) entries; freeform prompt updated to use marketplace local language
- **UI: Active marketplace banner** in Process Offers — shows name, category count, characteristic count, value count after marketplace selection
- **AI cost estimator** — pre-processing estimate of token batches, characteristic calls, and USD cost
- **Multi-provider LLM architecture** — `core/providers/` with abstract `BaseLLMProvider` + 5 providers; `core/llm_router.py` singleton with `get_router()`, `switch_provider()`, `reset_router()`
- **`ai_enricher.py` refactored** — removed direct Anthropic dependency; all calls go through `get_router().complete()`
- **LLM Providers page** — full UI for configuring, switching and testing all providers; writes directly to `.env`
- **`start_all.py` secured** — Telegram credentials moved from hardcoded to `.env`
- **`.gitignore` and `.env.example` added**

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
