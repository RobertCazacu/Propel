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
from pathlib import Path
from core.app_logger import get_logger
from core.llm_router import get_router

log = get_logger("marketplace.ai")

CACHE_PATH = Path(__file__).parent.parent / "data" / "ai_cache.json"

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
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"category_map": {}, "char_map": {}, "learned_title_rules": []}


def _save_cache(cache: dict):
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _title_key(title: str, marketplace: str = "") -> str:
    return hashlib.md5(f"{marketplace}|{title.strip().lower()}".encode()).hexdigest()


def _char_key(title: str, category: str, marketplace: str = "") -> str:
    return hashlib.md5(f"{marketplace}|{title.strip().lower()}|{category}".encode()).hexdigest()


def get_learned_rules() -> list[dict]:
    """Returneaza regulile invatate de AI, gata de folosit in process.py."""
    return _load_cache().get("learned_title_rules", [])


# ── Category suggestion — BATCH (un singur apel pentru N produse) ─────────────

MARKETPLACE_CONTEXT = {
    "allegro":      "Allegro (Polonia). Categoriile si valorile sunt in poloneza.",
    "trendyol":     "Trendyol.",
    "decathlon":    "Decathlon.",
    "pepita":       "Pepita.",
    "emag_hu":      "eMAG Magyarország (Ungaria). Valorile caracteristicilor trebuie sa fie in limba maghiara.",
    "emag_bg":      "eMAG България (Bulgaria). Valorile caracteristicilor trebuie sa fie in limba bulgara.",
    "emag":         "eMAG Romania. Valorile caracteristicilor trebuie sa fie in limba romana.",
    "fashiondays":  "FashionDays.",
}

def _mp_ctx(marketplace: str) -> str:
    """Returneaza contextul marketplace-ului pentru prompt."""
    key = marketplace.lower().strip()
    # Sort by key length descending — longer (more specific) keys match first
    for k, v in sorted(MARKETPLACE_CONTEXT.items(), key=lambda x: -len(x[0])):
        if k in key:
            return v
    return marketplace  # fallback la numele exact


BATCH_SIZE = 60  # max produse per apel AI (limita tokeni output ~8192)


def _process_batch(batch: list[dict], category_list: list[str],
                   marketplace: str, cache: dict) -> dict:
    """Trimite un singur batch de produse la AI. Returneaza {prod_id: category}."""
    cats_list = "\n".join(category_list)
    lines = []
    for i, prod in enumerate(batch, 1):
        desc = re.sub(r"<[^>]+>", " ", prod.get("description") or "").strip()[:80]
        line = f'{i}. "{prod["title"]}"'
        if desc:
            line += f" | {desc}"
        lines.append(line)

    prompt = (
        f"Clasifica fiecare produs intr-una din categoriile de mai jos.\n"
        f"Marketplace: {_mp_ctx(marketplace)}\n\n"
        f"CATEGORII DISPONIBILE (copiaza EXACT, fara modificari):\n{cats_list}\n\n"
        f"PRODUSE DE CLASIFICAT:\n" + "\n".join(lines) +
        f'\n\nRaspunde DOAR cu JSON: {{"1":"Categorie exacta","2":"Categorie exacta",...}}\n'
        f'Reguli: (1) Copiaza EXACT numele categoriei din lista de mai sus. '
        f'(2) Daca nicio categorie nu se potriveste, pune null. '
        f'(3) Zero text in afara JSON-ului.'
    )

    batch_results = {}
    max_tok = min(4096, max(512, len(batch) * 60))
    log.info("AI batch categorii: %d produse, max_tokens=%d", len(batch), max_tok)
    try:
        raw = get_router().complete(prompt, max_tok)
        log.debug("AI batch raw response (first 300 chars): %s", raw[:300])
        suggested = _parse_json(raw)

        accepted = 0
        rejected = 0
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
    except Exception as exc:
        log.error("Exceptie AI batch categorii: %s", exc, exc_info=True)
        for prod in batch:
            batch_results[prod["id"]] = None

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

def _build_prompt(title: str, description: str, category: str,
                  existing: dict, char_options: dict, marketplace: str = "") -> str:
    # Separa campurile cu lista de valori de cele freeform
    options_lines = []
    freeform_lines = []
    for ch_name, values in list(char_options.items())[:15]:
        if values:
            options_lines.append(
                f'  "{ch_name}": {json.dumps(sorted(values)[:20], ensure_ascii=False)}'
            )
        else:
            freeform_lines.append(f'  "{ch_name}"')

    desc_clean = re.sub(r"<[^>]+>", " ", description or "").strip()[:200]

    prompt = (
        f"Marketplace: {_mp_ctx(marketplace)}\n"
        f"Produs: {title}\nCategorie: {category}\nDescriere: {desc_clean}\n"
        f"Completate deja: {json.dumps(existing, ensure_ascii=False)}\n\n"
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

    prompt += (
        'Raspunde DOAR cu JSON: {"Nume caracteristica": "valoare", ...}. '
        'Pentru campuri cu lista: foloseste EXACT valorile din lista. '
        'Pentru campuri libere: foloseste valorile in limba locala a marketplace-ului specificat mai sus. '
        'Daca nu poti determina valoarea, omite caracteristica. Fara text extra.'
    )
    return prompt


def _parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
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
) -> dict:
    """
    Completeaza caracteristicile cu AI.
    Optimizare: verifica cache-ul mai intai, apeleaza API doar daca e necesar.
    Daca mandatory_chars e specificat, trimite AI doar caracteristicile obligatorii lipsa.
    """
    # Filtreaza doar cele lipsa
    missing_options = {k: v for k, v in char_options.items() if k not in existing}

    # Adauga caracteristicile obligatorii fara lista de valori (campuri freeform)
    if mandatory_chars:
        for ch in mandatory_chars:
            if ch not in existing and ch not in missing_options:
                # Camp freeform — AI poate pune orice valoare
                missing_options[ch] = set()

    if not missing_options:
        return {}

    # Daca avem lista de mandatory, prioritizeaza-le
    if mandatory_chars:
        mandatory_missing = {k: v for k, v in missing_options.items() if k in mandatory_chars}
        # Daca nu lipseste niciun mandatory, skip AI
        if not mandatory_missing:
            return {}
        # Trimite doar mandatory lipsa (plus cateva optionale ca context)
        optional_extra = {k: v for k, v in missing_options.items()
                         if k not in mandatory_chars and len(mandatory_missing) < 8}
        missing_options = {**mandatory_missing, **dict(list(optional_extra.items())[:5])}

    # Verifica cache
    cache = _load_cache()
    h = _char_key(title, category, marketplace)
    if h in cache["char_map"]:
        cached = cache["char_map"][h]
        validated_cached = {}
        for k, v in cached.items():
            if k in missing_options:
                vs = valid_values_for_cat.get(k, set())
                if not vs or str(v).strip() in vs:
                    validated_cached[k] = v
        if validated_cached:
            return validated_cached

    freeform_chars = [ch for ch in missing_options if not valid_values_for_cat.get(ch)]
    if freeform_chars:
        log.debug("AI char enrichment — freeform (no valid values): %s | titlu: %s",
                  freeform_chars, title[:60])

    log.info("AI char enrichment pentru %r — missing: %s", title[:60], list(missing_options.keys()))
    try:
        prompt = _build_prompt(title, description, category, existing, missing_options, marketplace)
        raw = get_router().complete(prompt, 300)
        log.debug("AI char raw response: %s", raw[:300])
        suggested = _parse_json(raw)

        validated = {}
        for ch_name, ch_val in suggested.items():
            valid_set = valid_values_for_cat.get(ch_name, set())
            val_str = str(ch_val).strip()
            if not valid_set:
                # Freeform — accept orice valoare non-goala
                if val_str:
                    validated[ch_name] = ch_val
                    log.debug("AI char freeform acceptat [%s] = %r", ch_name, ch_val)
            elif val_str in valid_set:
                validated[ch_name] = ch_val
                log.debug("AI char acceptat [%s] = %r", ch_name, ch_val)
            else:
                log.warning(
                    "AI char respins [%s] = %r — nu e in lista de valori permise (%d valori)",
                    ch_name, ch_val, len(valid_set),
                )

        if validated:
            cache["char_map"][h] = validated
            _save_cache(cache)

        return validated

    except Exception as exc:
        log.error("Exceptie AI char enrichment pentru %r: %s", title[:60], exc, exc_info=True)
        return {}


def test_connection() -> tuple[bool, str]:
    try:
        router = get_router()
        router.complete("ping", 10)
        return True, f"Conexiune OK. Provider: {router.provider_name.upper()}"
    except Exception as e:
        return False, f"Eroare: {str(e)}"
