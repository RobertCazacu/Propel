"""
Claude AI enrichment — optimizat pentru consum minim de tokeni.

Strategii de optimizare:
1. Cache persistent (data/ai_cache.json) — produse vazute = 0 tokeni
2. Batch category suggestion — N produse fara categorie = 1 singur apel API
3. Auto-invatare reguli — AI mapeaza o data, urmatoarele runde sunt gratuite
4. AI doar pentru caracteristici obligatorii lipsa — skip daca regulile au acoperit tot
"""
import json
import re
import hashlib
import time
import threading
import unicodedata
from pathlib import Path
from core.app_logger import get_logger
from core.llm_router import get_router
from core.ai_logger import log_category_batch, log_char_enrichment
from core.reference_store_duckdb import get_product_knowledge, upsert_product_knowledge
from core.schema_builder import SchemaBuilder, build_json_schema

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


_MAX_AI_RETRIES = 2


def _complete_with_retry(prompt: str, max_tok: int, system: str, temperature: float) -> str:
    """Calls router.complete with up to _MAX_AI_RETRIES retries on transient errors."""
    router = get_router()
    last_exc: Exception = Exception("unknown")
    for attempt in range(_MAX_AI_RETRIES + 1):
        try:
            return router.complete(prompt, max_tok, system=system, temperature=temperature)
        except (TimeoutError, ConnectionError) as exc:
            last_exc = exc
            if attempt < _MAX_AI_RETRIES:
                log.warning("AI call failed (attempt %d/%d): %s — retrying...",
                            attempt + 1, _MAX_AI_RETRIES + 1, exc)
                time.sleep(1)
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
    """Prompt de sistem static pentru enrichment caracteristici — nu conține date de produs."""
    return (
        f"Ești un expert în clasificarea produselor pe marketplace-uri.\n"
        f"Marketplace activ: {_mp_ctx(marketplace)}\n\n"
        "REGULI STRICTE:\n"
        "1. Răspunde EXCLUSIV cu JSON valid: {\"Nume caracteristica\": \"valoare\", ...}\n"
        "2. Primul câmp TREBUIE să fie \"_reasoning\": o propoziție scurtă care explică alegerile.\n"
        "3. Pentru câmpuri cu listă de valori: folosești EXACT o valoare din lista permisă.\n"
        "4. Pentru câmpuri libere: folosești valorile în limba locală a marketplace-ului.\n"
        "5. Câmpurile marcate [OBLIGATORIU] se completează cu prioritate maximă.\n"
        "6. Dacă nu poți determina o valoare, omite acea caracteristică.\n"
        "7. Zero text în afara JSON-ului. Fără markdown, fără explicații extra."
    )


def _build_batch_system_prompt(marketplace: str, category_list: list[str]) -> str:
    """Prompt de sistem static pentru clasificare batch categorii."""
    cats_list = "\n".join(category_list)
    return (
        f"Ești un expert în clasificarea produselor pe marketplace-uri.\n"
        f"Marketplace activ: {_mp_ctx(marketplace)}\n\n"
        "CATEGORII DISPONIBILE (copiază EXACT, fără modificări):\n"
        f"{cats_list}\n\n"
        "REGULI STRICTE:\n"
        "1. Răspunde EXCLUSIV cu JSON: {\"1\":\"Categorie\",\"2\":\"Categorie\",...}\n"
        "2. Copiezi EXACT numele categoriei din lista de mai sus.\n"
        "3. Titlurile produselor pot fi în orice limbă — clasifici după tipul produsului, nu după limbă.\n"
        "4. Dacă nicio categorie nu se potrivește, pui null pentru acel produs.\n"
        "5. Zero text în afara JSON-ului."
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
        desc = re.sub(r"<[^>]+>", " ", prod.get("description") or "").strip()[:80]
        line = f'{i}. "{prod["title"]}"'
        if desc:
            line += f" | {desc}"
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
    try:
        raw = _complete_with_retry(prompt, max_tok, system_prompt, 0.2)
        duration_ms = round((time.perf_counter() - t_start) * 1000)
        log.debug("AI batch raw response (first 300 chars): %s", raw[:300])
        suggested = _parse_json(raw)
        if not suggested:
            log.warning("AI batch categorii — raspuns invalid/gol (raw: %s)", raw[:300])

        for i, prod in enumerate(batch, 1):
            pid = prod["id"]
            cat = suggested.get(str(i))
            if cat and cat in category_list:
                batch_results[pid] = cat
                accepted += 1
                cache["category_map"][_title_key(prod["title"], marketplace)] = cat
                # Auto-invata regula
                title_base = prod["title"].rsplit(" - ", 1)[0].strip()
                words = [w.lower() for w in re.split(r"[\s\-]+", title_base)
                         if len(w) > 3
                         and not w.isdigit()
                         and not w.isupper()
                         and not any(c.isdigit() for c in w)][:5]
                kw_string = ", ".join(words)
                existing_kws = {r.get("keywords", r.get("prefix", "")) for r in cache["learned_title_rules"]}
                if kw_string and kw_string not in existing_kws:
                    cache["learned_title_rules"].append({
                        "keywords": kw_string,
                        "exclude": "",
                        "category": cat,
                    })
            else:
                if cat:
                    log.warning(
                        "AI a sugerat categorie invalida %r pentru prod %s (%r)",
                        cat, pid, prod["title"][:60],
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
    processed = 0

    for chunk_idx, chunk in enumerate(chunks):
        if status_callback:
            status_callback(
                f"AI categorii: batch {chunk_idx + 1}/{len(chunks)} "
                f"({processed}/{total} produse)..."
            )
        batch_res = _process_batch(chunk, category_list, marketplace, cache)
        results.update(batch_res)
        processed += len(chunk)
        _save_cache(cache)  # salveaza dupa fiecare batch

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

    mandatory_set: set de display-names (cu ":" deja stripuit) ale câmpurilor obligatorii.
    product_meta: câmpuri extra din fișierul de oferte (ean, sku, weight, warranty, brand).
    """
    mandatory_set = mandatory_set or set()
    meta = product_meta or {}

    # Separa campurile cu lista de valori de cele freeform; obligatorii primele
    options_lines = []
    freeform_lines = []
    for ch_name, values in list(char_options.items())[:15]:
        req = " [OBLIGATORIU]" if ch_name in mandatory_set else ""
        if values:
            options_lines.append(
                f'  "{ch_name}"{req}: {json.dumps(sorted(values)[:20], ensure_ascii=False)}'
            )
        else:
            freeform_lines.append(f'  "{ch_name}"{req}')

    # Curăță descrierea: elimină HTML rezidual, colapsează whitespace, trunchiază
    desc_clean = re.sub(r"<[^>]+>", " ", description or "")
    desc_clean = re.sub(r"\s+", " ", desc_clean).strip()[:400]

    # Brand: prioritate meta > existing chars
    brand = (
        str(meta["brand"]).strip() if meta.get("brand") and str(meta["brand"]).strip() not in ("", "nan")
        else next(
            (str(v).strip() for k, v in existing.items()
             if k.lower().rstrip(":").strip() in _BRAND_KEYS and v and str(v).strip()),
            None,
        )
    )

    # Filtrează cheile interne (prefixate cu "_") din afișarea existing
    existing_display = {k: v for k, v in existing.items() if not k.startswith("_")}

    prompt = f"Marketplace: {_mp_ctx(marketplace)}\n"
    prompt += f"Produs: {title}\n"
    if brand:
        prompt += f"Brand: {brand}\n"
    # Extra metadata signals for the model
    for meta_key, label in (("ean", "EAN"), ("sku", "SKU"), ("weight", "Greutate"), ("warranty", "Garanție")):
        val = meta.get(meta_key)
        if val and str(val).strip() not in ("", "nan"):
            prompt += f"{label}: {str(val).strip()}\n"
    prompt += (
        f"Categorie: {category}\n"
        f"Descriere: {desc_clean}\n"
        f"Completate deja: {json.dumps(existing_display, ensure_ascii=False)}\n\n"
    )

    if options_lines:
        prompt += (
            "Completeaza caracteristicile urmatoare alegand EXACT o valoare din lista permisa:\n"
            + "\n".join(options_lines) + "\n\n"
        )

    if freeform_lines:
        prompt += (
            "Completeaza si aceste campuri libere (orice valoare potrivita, ex: '41 EU', culoare, material):\n"
            + "\n".join(freeform_lines) + "\n\n"
        )

    prompt += 'Completează caracteristicile lipsă. Câmpurile marcate [OBLIGATORIU] au prioritate maximă.'
    return prompt


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
) -> tuple[dict, dict]:
    """
    Completeaza caracteristicile cu AI.
    Optimizare: verifica cache-ul mai intai, apeleaza API doar daca e necesar.
    Daca mandatory_chars e specificat, trimite AI doar caracteristicile obligatorii lipsa.

    Returns:
        (validated, suggested_remapped) — validated = chars that passed validation;
        suggested_remapped = all AI suggestions (original keys, _reasoning excluded).
    """
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
    _known = get_product_knowledge(ean=ean, brand=brand, normalized_title=_norm_title) if (ean or brand) else None
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
                        marketplace=marketplace,
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
