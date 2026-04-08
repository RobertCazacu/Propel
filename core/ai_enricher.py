"""
Claude AI enrichment — optimizat pentru consum minim de tokeni.

Strategii de optimizare:
1. Cache persistent (data/ai_cache.json) — produse vazute = 0 tokeni
2. Batch category suggestion — N produse fara categorie = 1 singur apel API
3. Auto-invatare reguli — AI mapeaza o data, urmatoarele runde sunt gratuite
4. AI doar pentru caracteristici obligatorii lipsa — skip daca regulile au acoperit tot
"""
import json
import os
import re
import random
import hashlib
import time
import threading
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from core.app_logger import get_logger
from core.llm_router import get_router
from core.ai_logger import log_category_batch, log_char_enrichment, write_run_to_duckdb
from core.reference_store_duckdb import get_product_knowledge, upsert_product_knowledge, marketplace_id_slug
from core.schema_builder import SchemaBuilder, build_json_schema


# ── Structured output config ───────────────────────────────────────────────────

def get_structured_config() -> dict:
    """Citește config structured output din session_state (UI) sau env.

    Returns dict cu cheile:
      mode: 'off' | 'shadow' | 'on'
      sample: float [0..1]
      provider_only: bool
    """
    try:
        import streamlit as st
        cfg = st.session_state.get("structured_output_config", {})
    except Exception:
        cfg = {}

    return {
        "mode":          cfg.get("mode",          os.getenv("AI_STRUCTURED_MODE", "off")),
        "sample":        float(cfg.get("sample",  os.getenv("AI_STRUCTURED_SAMPLE", "0.10"))),
        "provider_only": cfg.get("provider_only", os.getenv("AI_STRUCTURED_PROVIDER_ONLY", "true").lower() == "true"),
    }


def _should_run_structured(cfg: dict, provider_name: str) -> bool:
    """Decide dacă structured output trebuie rulat pentru această cerere."""
    mode = cfg.get("mode", "off")
    if mode == "off":
        return False
    # Verifică restricție provider
    if cfg.get("provider_only", True) and provider_name != "anthropic":
        return False
    # Sampling
    sample = float(cfg.get("sample", 0.10))
    return random.random() < sample

log = get_logger("marketplace.ai")

CACHE_PATH = Path(__file__).parent.parent / "data" / "ai_cache.json"


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
_cache_lock = threading.Lock()
_merge_lock = threading.Lock()   # P01: protejează citirea+scrierea learned_title_rules

# Load .env if present
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass


def is_configured() -> bool:
    try:
        get_router()
        return True
    except Exception:
        return False


# ── Cache helpers ──────────────────────────────────────────────────────────────

def _load_cache() -> dict:
    with _cache_lock:
        if CACHE_PATH.exists():
            try:
                data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                # Backward-compat: ensure done_map key exists
                data.setdefault("done_map", {})
                return data
            except Exception:
                pass
    return {"category_map": {}, "char_map": {}, "learned_title_rules": [], "done_map": {}}


def _save_cache(cache: dict):
    with _cache_lock:
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass


def _title_key(title: str, marketplace: str = "") -> str:
    return hashlib.md5(f"{marketplace}|{title.strip().lower()}".encode()).hexdigest()


def _char_key(title: str, category: str, marketplace: str = "",
              missing_keys: tuple = ()) -> str:
    missing_part = ",".join(sorted(missing_keys))
    return hashlib.md5(
        f"{marketplace}|{title.strip().lower()}|{category}|{missing_part}".encode()
    ).hexdigest()


def _done_key(title: str, category: str, marketplace: str = "") -> str:
    """Stable key for tracking fully-processed products (no missing_keys component)."""
    return hashlib.md5(f"done|{marketplace}|{title.strip().lower()}|{category}".encode()).hexdigest()


_MAX_AI_RETRIES = 4
_RATE_LIMIT_BASE_SLEEP = 10  # secunde — backoff initial pentru 429


def _sanitize_for_prompt(text: str, max_len: int = 300) -> str:
    """P10: Sanitizează input utilizator pentru inserare sigură în prompt LLM.

    Elimină newline-uri (ar putea injecta instrucțiuni noi),
    escape-ează ghilimele duble (sparg structura JSON așteptată),
    truncată la max_len pentru a limita token-ii și vectorii de injecție.
    """
    if not text:
        return ""
    text = str(text).replace("\n", " ").replace("\r", " ")
    text = text.replace('"', '\\"')
    return text[:max_len]


def _complete_with_retry(prompt: str, max_tok: int, system: str, temperature: float) -> str:
    """Calls router.complete with up to _MAX_AI_RETRIES retries.

    Handling:
    - 429 RateLimitError → exponential backoff (10s, 20s, 40s, 80s)
    - TimeoutError / ConnectionError → retry imediat (max 2×)
    """
    router = get_router()
    last_exc: Exception = Exception("unknown")
    rate_sleep = _RATE_LIMIT_BASE_SLEEP

    for attempt in range(_MAX_AI_RETRIES + 1):
        try:
            return router.complete(prompt, max_tok, system=system, temperature=temperature)
        except Exception as exc:
            last_exc = exc
            exc_str = str(type(exc).__name__)
            is_rate_limit = (
                "RateLimitError" in exc_str
                or "rate_limit" in str(exc).lower()
                or "429" in str(exc)
                or "overloaded" in str(exc).lower()
            )
            if is_rate_limit and attempt < _MAX_AI_RETRIES:
                # Jitter: ±50% din sleep pentru a evita thundering herd cu workeri paraleli
                jitter = random.uniform(0, rate_sleep * 0.5)
                actual_sleep = rate_sleep + jitter
                log.warning(
                    "Rate limit API (attempt %d/%d) — backoff %.0fs...",
                    attempt + 1, _MAX_AI_RETRIES + 1, actual_sleep,
                )
                time.sleep(actual_sleep)
                rate_sleep = min(rate_sleep * 2, 120)  # max 2 minute sleep
            elif isinstance(exc, (TimeoutError, ConnectionError)) and attempt < _MAX_AI_RETRIES:
                log.warning("AI call failed (attempt %d/%d): %s — retrying...",
                            attempt + 1, _MAX_AI_RETRIES + 1, exc)
                time.sleep(2)
            else:
                raise
    raise last_exc


def get_learned_rules() -> list[dict]:
    """Returneaza regulile invatate de AI, gata de folosit in process.py."""
    return _load_cache().get("learned_title_rules", [])


# ── Category suggestion — BATCH (un singur apel pentru N produse) ─────────────

MARKETPLACE_CONTEXT = {
    "trendyol":     "Trendyol.",
    "decathlon":    "Decathlon.",
    "pepita":       "Pepita.",
    "emag_hu":      "eMAG Magyarország (Ungaria). Valorile caracteristicilor trebuie sa fie in limba maghiara.",
    "emag_bg":      "eMAG България (Bulgaria). Valorile caracteristicilor trebuie sa fie in limba bulgara.",
    "emag":         "eMAG Romania. Valorile caracteristicilor trebuie sa fie in limba romana.",
    "fashiondays":  "FashionDays.",
    "allegro":      "Allegro (Polonia). Categoriile si valorile sunt in poloneza.",
}

# Token aliases — orice token din lista → contextul corespunzator
# Ordinea listelor conteaza: mai specific primul (bg inainte de emag generic)
_MP_ALIASES: list[tuple[list[str], str]] = [
    # ── Bulgarian ──────────────────────────────────────────────────────────────
    (["bg", "bulg", "bulgaria", "bulgarian", "bgn", "българия", "emag bg", "fashiondays bg"],
     "eMAG България (Bulgaria). Valorile caracteristicilor trebuie sa fie in limba bulgara."),

    # ── Hungarian ──────────────────────────────────────────────────────────────
    (["hu", "hun", "hungary", "ungaria", "hungarian", "huf", "magyarország", "emag hu", "fashiondays hu"],
     "eMAG Magyarország (Ungaria). Valorile caracteristicilor trebuie sa fie in limba maghiara."),

    # ── Polish (Allegro) ───────────────────────────────────────────────────────
    (["pl", "pol", "polonia", "poland", "polish", "allegro", "pln"],
     "Allegro (Polonia). Categoriile si valorile sunt in poloneza."),

    # ── FashionDays generic (fara tara → romana) ───────────────────────────────
    (["fashiondays", "fashion days", "fashion-days"],
     "FashionDays."),

    # ── Trendyol ──────────────────────────────────────────────────────────────
    (["trendyol"],
     "Trendyol."),

    # ── Romanian / eMAG generic ────────────────────────────────────────────────
    (["ro", "romania", "romanian", "ron", "emag"],
     "eMAG Romania. Valorile caracteristicilor trebuie sa fie in limba romana."),
]


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


def _mp_ctx(marketplace: str) -> str:
    """Returneaza contextul marketplace-ului pentru prompt.

    Matching permisiv: imparte numele in tokeni si verifica daca oricare
    alias din lista apare in numele marketplace-ului (sau invers).
    Regulile mai specifice (BG, HU) sunt verificate primele.
    """
    key = marketplace.lower().strip()
    # Tokenize: split by space, dash, underscore
    import re as _re
    tokens = set(_re.split(r"[\s_\-]+", key))

    for aliases, ctx in _MP_ALIASES:
        for alias in aliases:
            alias_tokens = set(_re.split(r"[\s_\-]+", alias.lower()))
            # Match daca TOATE tokenele aliasului se regasesc in key sau
            # daca aliasul apare ca substring in key
            if alias_tokens <= tokens or alias.lower() in key:
                return ctx

    return marketplace  # fallback la numele exact


BATCH_SIZE = 60  # max produse per apel AI (limita tokeni output ~8192)


def _process_batch(batch: list[dict], category_list: list[str],
                   marketplace: str, cache: dict) -> dict:
    """Trimite un singur batch de produse la AI. Returneaza {prod_id: category}."""
    lines = []
    for i, prod in enumerate(batch, 1):
        raw_desc = re.sub(r"<[^>]+>", " ", prod.get("description") or "").strip()[:80]
        safe_title = _sanitize_for_prompt(prod.get("title", ""), max_len=300)  # P10
        safe_desc  = _sanitize_for_prompt(raw_desc, max_len=80)                # P10
        line = f'{i}. "{safe_title}"'
        if safe_desc:
            line += f" | {safe_desc}"
        lines.append(line)

    system_prompt = _build_batch_system_prompt(marketplace, category_list)
    prompt = "PRODUSE DE CLASIFICAT:\n" + "\n".join(lines) + "\n"

    batch_results = {}
    max_tok = min(4096, max(512, len(batch) * 60))
    log.info("AI batch categorii: %d produse, max_tokens=%d", len(batch), max_tok)
    raw = ""
    suggested = {}
    accepted = 0
    rejected = 0
    t_start = time.perf_counter()
    router = get_router()

    # Index normalizat pentru match rapid (lowercase strip) — util pt. categorii non-ASCII (BG, HU)
    import difflib as _difflib
    _cat_norm_index = {c.strip().lower(): c for c in category_list}

    def _match_category(ai_cat: str):
        """Incearca match exact → normalizat → fuzzy. Returneaza categoria din lista sau None."""
        if ai_cat in category_list:
            return ai_cat
        norm_key = ai_cat.strip().lower()
        if norm_key in _cat_norm_index:
            return _cat_norm_index[norm_key]
        # Fuzzy fallback (cutoff 0.88 — previne false positives)
        close = _difflib.get_close_matches(ai_cat, category_list, n=1, cutoff=0.88)
        return close[0] if close else None

    try:
        raw = _complete_with_retry(prompt, max_tok, system_prompt, 0.2)
        duration_ms = round((time.perf_counter() - t_start) * 1000)
        log.debug("AI batch raw response (first 300 chars): %s", raw[:300])
        suggested = _parse_json(raw)
        if not suggested:
            log.warning("AI batch categorii — raspuns invalid/gol (raw: %s)", raw[:300])

        for i, prod in enumerate(batch, 1):
            pid = prod["id"]
            ai_cat = suggested.get(str(i))
            matched_cat = _match_category(ai_cat) if ai_cat else None
            if matched_cat:
                if matched_cat != ai_cat:
                    log.debug("AI batch categorie fuzzy-matched %r → %r (prod %s)", ai_cat, matched_cat, pid)
                batch_results[pid] = matched_cat
                accepted += 1
                cache["category_map"][_title_key(prod["title"], marketplace)] = matched_cat
                # Auto-invata regula
                title_base = prod["title"].rsplit(" - ", 1)[0].strip()
                words = [w.lower() for w in re.split(r"[\s\-]+", title_base)
                         if len(w) > 3
                         and not w.isdigit()
                         and not w.isupper()
                         and not any(c.isdigit() for c in w)][:5]
                kw_string = ", ".join(words)
                with _merge_lock:   # P01: atomic read+append pentru thread safety
                    existing_kws = {r.get("keywords", r.get("prefix", "")) for r in cache["learned_title_rules"]}
                    if kw_string and kw_string not in existing_kws:
                        cache["learned_title_rules"].append({
                            "keywords": kw_string,
                            "exclude": "",
                            "category": matched_cat,
                        })
            else:
                if ai_cat:
                    log.warning(
                        "AI a sugerat categorie invalida %r pentru prod %s (%r)",
                        ai_cat, pid, prod["title"][:60],
                    )
                batch_results[pid] = None
                rejected += 1
        log.info("AI batch categorii: %d acceptate, %d respinse/null", accepted, rejected)
    except TimeoutError as exc:
        duration_ms = round((time.perf_counter() - t_start) * 1000)
        log.error("Timeout AI batch categorii: %s", exc)
        for prod in batch:
            batch_results[prod["id"]] = "__timeout__"
    except Exception as exc:
        duration_ms = round((time.perf_counter() - t_start) * 1000)
        log.error("Exceptie AI batch categorii: %s", exc, exc_info=True)
        for prod in batch:
            batch_results[prod["id"]] = None

    # ── AI request/response log ────────────────────────────────────────────────
    try:
        log_category_batch(
            marketplace=marketplace,
            provider=router.provider_name,
            model=str(getattr(router._provider, "_model", "unknown")),
            products=batch,
            category_list=category_list,
            prompt=system_prompt + "\n---\n" + prompt,
            raw_response=raw,
            parsed=suggested,
            results=batch_results,
            duration_ms=duration_ms,
            accepted=accepted,
            rejected=rejected,
            max_tokens=max_tok,
        )
    except Exception:
        pass

    return batch_results


def suggest_categories_batch(
    products: list[dict],
    category_list: list[str],
    marketplace: str = "",
    status_callback=None,   # optional callable(message: str)
) -> dict:
    """
    Clasifica produsele in batch-uri de max BATCH_SIZE pentru a respecta
    limita de tokeni output a modelului.
    Returneaza {product_id: category_name_or_None}.
    """
    if not products:
        return {}

    cache = _load_cache()
    results = {}
    uncached = []

    for prod in products:
        h = _title_key(prod["title"], marketplace)
        if h in cache["category_map"]:
            results[prod["id"]] = cache["category_map"][h]
        else:
            uncached.append(prod)

    if not uncached:
        return results

    # Imparte in batch-uri de BATCH_SIZE
    chunks = [uncached[i:i + BATCH_SIZE] for i in range(0, len(uncached), BATCH_SIZE)]
    total = len(uncached)
    completed_count = 0
    merge_lock = threading.Lock()

    if status_callback:
        status_callback(
            f"AI categorii: se procesează {len(chunks)} batch-uri în paralel (2 simultan)..."
        )

    def _run_chunk(args):
        chunk_idx, chunk = args
        # Fiecare thread primește un cache local — evită race conditions
        local_cache: dict = {"category_map": {}, "char_map": {}, "learned_title_rules": [], "done_map": {}}
        batch_res = _process_batch(chunk, category_list, marketplace, local_cache)
        return chunk_idx, batch_res, local_cache

    # 2 workeri paraleli (redus de la 5) — evita thundering herd pe rate limit
    max_workers = min(2, len(chunks))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_run_chunk, (i, chunk)): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            chunk_idx, batch_res, local_cache = future.result()
            with merge_lock:
                results.update(batch_res)
                cache["category_map"].update(local_cache["category_map"])
                for rule in local_cache.get("learned_title_rules", []):
                    existing_kws = {r.get("keywords", r.get("prefix", "")) for r in cache["learned_title_rules"]}
                    if rule.get("keywords") and rule["keywords"] not in existing_kws:
                        cache["learned_title_rules"].append(rule)
                completed_count += len(chunks[chunk_idx])
                if status_callback:
                    status_callback(
                        f"AI categorii: {completed_count}/{total} produse "
                        f"({chunk_idx + 1}/{len(chunks)} batch-uri)..."
                    )

    _save_cache(cache)  # o singura scriere la final in loc de N scrieri

    if status_callback:
        status_callback(f"AI categorii: gata ({total} produse procesate).")

    return results


# ── Characteristic enrichment — cu cache ──────────────────────────────────────

_BRAND_KEYS = {"brand", "brand:", "marca", "marcă:", "marka:", "márka:", "marque:", "marka", "марка"}


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


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    result = None
    try:
        result = json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                result = json.loads(m.group())
            except Exception:
                pass
    if isinstance(result, dict):
        result.pop("_reasoning", None)
        return result
    return {}


def enrich_with_ai(
    title: str,
    description: str,
    category: str,
    existing: dict,
    char_options: dict,
    valid_values_for_cat: dict,
    mandatory_chars: list = None,
    marketplace: str = "",
    product_meta: dict = None,
    data=None,           # Optional[MarketplaceData] — enables strict value validation
    ean: str | None = None,
    brand: str | None = None,
    vision_chars: dict | None = None,
) -> tuple[dict, dict]:
    """
    Completeaza caracteristicile cu AI.
    Optimizare: verifica cache-ul mai intai, apeleaza API doar daca e necesar.
    Daca mandatory_chars e specificat, trimite AI doar caracteristicile obligatorii lipsa.
    vision_chars: atribute deja detectate din imagine — AI le va considera ca existente.

    Returns:
        (validated, suggested_remapped) — validated = chars that passed validation;
        suggested_remapped = all AI suggestions (original keys, _reasoning excluded).
    """
    # Injectează atributele detectate vizual ca deja-existente (AI nu le mai solicită)
    if vision_chars:
        existing = {**existing}
        for k, v in vision_chars.items():
            if k not in existing and v:
                existing[k] = v

    # Filtreaza doar cele lipsa
    missing_options = {k: v for k, v in char_options.items() if not existing.get(k)}

    # Adauga caracteristicile obligatorii fara lista de valori (campuri freeform)
    if mandatory_chars:
        for ch in mandatory_chars:
            if not existing.get(ch) and ch not in missing_options:
                # Camp freeform — AI poate pune orice valoare
                missing_options[ch] = set()

    if not missing_options:
        return {}, {}

    # Daca avem lista de mandatory, prioritizeaza-le
    if mandatory_chars:
        mandatory_missing = {k: v for k, v in missing_options.items() if k in mandatory_chars}
        # Daca nu lipseste niciun mandatory, skip AI
        if not mandatory_missing:
            return {}, {}
        # Trimite doar mandatory lipsa (plus cateva optionale ca context)
        optional_extra = {k: v for k, v in missing_options.items()
                         if k not in mandatory_chars and len(mandatory_missing) < 8}
        missing_options = {**mandatory_missing, **dict(list(optional_extra.items())[:5])}

    # ── Knowledge store lookup ──────────────────────────────────────────────────
    _norm_title = _normalize_title(title)
    _mp_id = marketplace_id_slug(marketplace) if marketplace else ""
    _known = get_product_knowledge(ean=ean, brand=brand, normalized_title=_norm_title, marketplace_id=_mp_id) if (ean or brand) and _mp_id else None
    _known_attrs = _known["final_attributes"] if _known else {}

    if _known_attrs:
        log.debug("Knowledge store hit: %d atribute cunoscute pentru '%s'", len(_known_attrs), title[:50])

    # Verifica cache
    cache = _load_cache()

    # Done-map: daca toate campurile obligatorii au fost deja rezolvate, skip AI
    dk = _done_key(title, category, marketplace)
    done_set = set(cache.get("done_map", {}).get(dk, []))
    if mandatory_chars and done_set and set(mandatory_chars) <= done_set:
        log.debug("Skip AI (done_map hit) pentru %r", title[:60])
        return {}, {}

    h = _char_key(title, category, marketplace, tuple(missing_options.keys()))
    if h in cache["char_map"]:
        cached = cache["char_map"][h]
        validated_cached = {}
        for k, v in cached.items():
            if k in missing_options:
                vs = valid_values_for_cat.get(k, set())
                if not vs or str(v).strip() in vs:
                    validated_cached[k] = v
        if validated_cached:
            return validated_cached, {}

    # Strip trailing ":" din char names inainte de trimitere la AI (EasySales pattern).
    # AI-ul va raspunde cu chei fara ":" — le remapam inapoi dupa parsare.
    stripped_to_orig = {}
    missing_options_display = {}
    for k, v in missing_options.items():
        display = k.rstrip(":")
        # Evita conflicte daca doua chars se normalizeaza la acelasi display name
        if display in stripped_to_orig:
            display = k
        stripped_to_orig[display] = k
        missing_options_display[display] = v

    freeform_chars = [ch for ch in missing_options if not valid_values_for_cat.get(ch)]
    if freeform_chars:
        log.debug("AI char enrichment — freeform (no valid values): %s | titlu: %s",
                  freeform_chars, title[:60])

    # Log product_meta fields that will be included as AI context
    meta = product_meta or {}
    meta_present = {k: v for k, v in meta.items() if v and str(v).strip() not in ("", "nan")}
    if meta_present:
        log.debug("AI char enrichment — meta context: %s | titlu: %s", list(meta_present.keys()), title[:60])

    log.info("AI char enrichment pentru %r — missing: %s", title[:60], list(missing_options.keys()))
    max_tok = 300
    raw = ""
    suggested = {}
    validated = {}
    t_start = time.perf_counter()
    try:
        # Setul de display-names obligatorii (cu ":" deja stripuit) pentru marcare în prompt
        mandatory_display_set = {k.rstrip(":") for k in (mandatory_chars or [])}
        prompt = _build_prompt(title, description, category, existing, missing_options_display, marketplace, mandatory_display_set, product_meta)
        if _known_attrs:
            known_context = "\n".join(f"  {k}: {v}" for k, v in _known_attrs.items())
            prompt += f"\n\nDate cunoscute din alte marketplace-uri (verificate):\n{known_context}"
        system_prompt = _build_char_system_prompt(marketplace)
        raw = _complete_with_retry(prompt, max_tok, system_prompt, 0.2)
        duration_ms = round((time.perf_counter() - t_start) * 1000)
        log.debug("AI char raw response: %s", raw[:300])
        suggested = _parse_json(raw)

        # Resolve category id once for strict path
        _ai_cat_id = data.category_id(category) if data is not None else None

        for ch_display, ch_val in suggested.items():
            # Remapeaza cheia inapoi la numele original (cu ":" daca era)
            ch_name = stripped_to_orig.get(ch_display, ch_display)
            val_str = str(ch_val).strip()
            if not val_str:
                continue

            if data is not None and _ai_cat_id is not None:
                # ── Strict path: use data.find_valid (handles canonicalization,
                #    numeric EU formats, diacritics normalization, fuzzy) ─────
                mapped = data.find_valid(val_str, _ai_cat_id, ch_name)
                if mapped is not None:
                    validated[ch_name] = mapped
                    log.debug("AI char acceptat [%s] = %r → %r", ch_name, ch_val, mapped)
                else:
                    vs = data.valid_values(_ai_cat_id, ch_name)
                    restrictive = data.is_restrictive(_ai_cat_id, ch_name)
                    if not vs:
                        # Try marketplace-level fallback (same char in other categories)
                        fb = data.marketplace_fallback_values(ch_name)
                        if fb:
                            fb_mapped = data._find_in_set(val_str, fb)
                            if fb_mapped is not None:
                                validated[ch_name] = fb_mapped
                                log.debug("AI char fallback-acceptat [%s] = %r → %r",
                                          ch_name, ch_val, fb_mapped)
                            elif not restrictive:
                                # Non-restrictive: accept AI value as freeform
                                validated[ch_name] = val_str
                                log.debug("AI char freeform-acceptat [%s] = %r", ch_name, ch_val)
                            else:
                                log.warning(
                                    "AI char respins [%s] = %r — fara match in fallback (%d vals)",
                                    ch_name, ch_val, len(fb),
                                )
                        elif not restrictive:
                            # Non-restrictive and no values defined: accept as freeform
                            validated[ch_name] = val_str
                            log.debug("AI char freeform-acceptat [%s] = %r (no values defined)", ch_name, ch_val)
                        else:
                            log.warning(
                                "AI char respins [%s] = %r — nicio valoare definita, niciun fallback",
                                ch_name, ch_val,
                            )
                    elif not restrictive:
                        # Non-restrictive: accept AI value even if not in the table
                        validated[ch_name] = val_str
                        log.debug("AI char freeform-acceptat [%s] = %r (non-restrictive)", ch_name, ch_val)
                    else:
                        log.warning(
                            "AI char respins [%s] = %r — nu e in lista de valori permise (%d valori)",
                            ch_name, ch_val, len(vs),
                        )
            else:
                # ── Legacy path (no data object): direct set lookup ──────────
                valid_set = valid_values_for_cat.get(ch_name, set())
                if not valid_set:
                    log.warning(
                        "AI char respins freeform [%s] = %r — fara valori permise (no data object)",
                        ch_name, ch_val,
                    )
                elif val_str in valid_set:
                    validated[ch_name] = ch_val
                    log.debug("AI char acceptat [%s] = %r", ch_name, ch_val)
                else:
                    log.warning(
                        "AI char respins [%s] = %r — nu e in lista de valori permise (%d valori)",
                        ch_name, ch_val, len(valid_set),
                    )

        if validated:
            log.info("AI char enrichment OK pentru %r — validate: %s", title[:60], list(validated.keys()))
            cache["char_map"][h] = validated
            # Update done_map: mark mandatory chars that are now resolved
            if mandatory_chars:
                done_set_new = set(cache.get("done_map", {}).get(dk, []))
                newly_done = {k for k in validated if k in mandatory_chars}
                done_set_new.update(newly_done)
                cache.setdefault("done_map", {})[dk] = sorted(done_set_new)
                if newly_done:
                    log.debug("Done-map updated pentru %r — obligatorii rezolvate: %s", title[:60], sorted(newly_done))
            _save_cache(cache)

            # ── Save to knowledge store (doar atribute validate) ─────────────
            if ean or brand:
                import uuid as _uuid
                try:
                    upsert_product_knowledge(
                        ean=ean,
                        brand=brand or "",
                        normalized_title=_norm_title,
                        marketplace_id=_mp_id,
                        offer_id=str(existing.get("_offer_id", "")),
                        category=str(category),
                        final_attributes=validated,
                        confidence=round(len(validated) / max(len(missing_options), 1), 2),
                        run_id=str(_uuid.uuid4()),
                    )
                except Exception as e:
                    log.warning("Nu s-a putut salva în knowledge store: %s", e)
        else:
            log.warning("AI char enrichment — niciun camp validat pentru %r (raw: %s)", title[:60], raw[:200])

    except Exception as exc:
        duration_ms = round((time.perf_counter() - t_start) * 1000)
        log.error("Exceptie AI char enrichment pentru %r: %s", title[:60], exc, exc_info=True)

    # ── AI request/response log ────────────────────────────────────────────────
    try:
        router = get_router()
        log_char_enrichment(
            marketplace=marketplace,
            provider=router.provider_name,
            model=str(getattr(router._provider, "_model", "unknown")),
            offer_id=str(existing.get("_offer_id", "")),
            title=title,
            category=category,
            existing_chars={k: v for k, v in existing.items() if not k.startswith("_")},
            missing_chars=missing_options,
            prompt=prompt,
            raw_response=raw,
            parsed=suggested,
            validated=validated,
            duration_ms=duration_ms,
            max_tokens=max_tok,
        )
    except Exception:
        pass

    # ── Structured output (off / shadow / on) ──────────────────────────────────
    _scfg = get_structured_config()
    _smode = _scfg.get("mode", "off")
    _s_attempted = False
    _s_success = False
    _s_fallback = False
    _s_latency_ms = 0
    _s_model = ""
    _s_schema_fields = 0
    _shadow_diff = None

    try:
        router = get_router()
        if _should_run_structured(_scfg, router.provider_name):
            _s_attempted = True
            # Construiește schema pentru caracteristicile lipsă
            char_list = [
                {"name": k, "is_mandatory": k in (mandatory_chars or []), "values": sorted(v)}
                for k, v in missing_options.items()
            ]
            sb = SchemaBuilder(max_total=20)
            selected_chars = sb.select(char_list, "", _known_attrs)
            schema = build_json_schema(selected_chars)
            _s_schema_fields = len(selected_chars)

            _s_t0 = time.perf_counter()
            structured_result = router.complete_structured(
                prompt if "prompt" in dir() else "",
                schema,
                system=system_prompt if "system_prompt" in dir() else None,
            )
            _s_latency_ms = round((time.perf_counter() - _s_t0) * 1000)
            _s_model = str(getattr(router._provider, "_STRUCTURED_MODEL",
                                   getattr(router._provider, "_model", "unknown")))

            if structured_result and isinstance(structured_result, dict):
                _s_success = True
                if _smode == "on":
                    # Structured devine primar — validăm și folosim rezultatul
                    validated_structured = {}
                    if data is not None:
                        _ai_cat_id_s = data.category_id(category) if data is not None else None
                        for ch_name, ch_val in structured_result.items():
                            if not str(ch_val).strip():
                                continue
                            if _ai_cat_id_s is not None:
                                mapped = data.find_valid(str(ch_val), _ai_cat_id_s, ch_name)
                                if mapped is not None:
                                    validated_structured[ch_name] = mapped
                            elif ch_name in missing_options:
                                vs = missing_options.get(ch_name, set())
                                if not vs or str(ch_val) in vs:
                                    validated_structured[ch_name] = str(ch_val)
                    else:
                        validated_structured = {
                            k: str(v) for k, v in structured_result.items()
                            if str(v).strip() and k in missing_options
                        }

                    if validated_structured:
                        # Structured output valid — înlocuiește text validated
                        validated = validated_structured
                        log.info("Structured output ON — %d câmpuri validate pentru %r",
                                 len(validated), title[:60])
                    else:
                        # Structured a returnat dar validarea a eșuat — fallback la text
                        _s_fallback = True
                        log.warning("Structured output ON — validare eșuată, fallback text pentru %r",
                                    title[:60])
                else:
                    # shadow: log comparativ, NU afectează output final
                    _shadow_diff = {
                        "agree": sorted(set(structured_result.keys()) & set(validated.keys())),
                        "only_structured": sorted(set(structured_result.keys()) - set(validated.keys())),
                        "only_plain": sorted(set(validated.keys()) - set(structured_result.keys())),
                        "value_conflicts": {
                            k: {"structured": str(structured_result[k]), "plain": str(validated[k])}
                            for k in set(structured_result.keys()) & set(validated.keys())
                            if str(structured_result[k]).strip().lower() != str(validated[k]).strip().lower()
                        },
                    }
                    log.info(
                        "Structured SHADOW pentru %r — agree=%d only_s=%d only_p=%d conflicts=%d",
                        title[:60],
                        len(_shadow_diff["agree"]),
                        len(_shadow_diff["only_structured"]),
                        len(_shadow_diff["only_plain"]),
                        len(_shadow_diff["value_conflicts"]),
                    )
            else:
                # Structured a returnat None/empty — fallback la text deja calculat
                _s_fallback = True
                log.debug("Structured output None/gol pentru %r — text fallback activ", title[:60])
    except Exception as _s_exc:
        _s_fallback = True
        log.warning("Structured output exc pentru %r: %s", title[:60], _s_exc)

    # ── DuckDB telemetry ───────────────────────────────────────────────────────
    try:
        import uuid as _uuid
        router = get_router()
        write_run_to_duckdb(
            run_id=str(_uuid.uuid4()),
            ean=ean,
            offer_id=str(existing.get("_offer_id", "")),
            marketplace=marketplace,
            model_used=str(getattr(router._provider, "_model", "unknown")),
            tokens_input=0,   # nu e expus de provider
            tokens_output=0,
            cost_usd=0.0,
            fields_requested=len(missing_options),
            fields_accepted=len(validated),
            fields_rejected=len(suggested) - len(validated),
            retry_count=0,
            fallback_used=False,
            duration_ms=duration_ms,
            structured_mode=_smode,
            structured_attempted=_s_attempted,
            structured_success=_s_success,
            structured_fallback_used=_s_fallback,
            structured_latency_ms=_s_latency_ms,
            structured_model_used=_s_model,
            schema_fields_count=_s_schema_fields,
            shadow_diff=_shadow_diff,
        )
    except Exception:
        pass

    # Build remapped suggested dict (original keys, _reasoning excluded) for caller
    suggested_remapped = {
        stripped_to_orig.get(k, k): v
        for k, v in suggested.items()
        if k != "_reasoning"
    }
    return validated, suggested_remapped


def test_connection() -> tuple[bool, str]:
    try:
        router = get_router()
        router.complete("ping", 10)
        return True, f"Conexiune OK. Provider: {router.provider_name.upper()}"
    except Exception as e:
        return False, f"Eroare: {str(e)}"
