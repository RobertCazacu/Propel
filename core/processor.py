"""
Characteristic processor.
Detects values from product title + description using rule-based logic,
validates against the marketplace valid-values list, and fills gaps.
"""
import re
from bs4 import BeautifulSoup
from typing import Optional
from core.loader import MarketplaceData
from core.app_logger import get_logger

log = get_logger("marketplace.processor")


def strip_html(html: str) -> str:
    if not html:
        return ""
    try:
        return BeautifulSoup(str(html), "html.parser").get_text(" ", strip=True)
    except Exception:
        return str(html)


def extract_size_from_title(title: str) -> Optional[str]:
    """Extract size from last ' - ' segment of title."""
    parts = title.rsplit(" - ", 1)
    return parts[1].strip() if len(parts) == 2 else None


# ── Individual detectors ───────────────────────────────────────────────────────

def detect_marime(title: str, data: MarketplaceData, cat_id, char_name: str = "Marime:") -> Optional[str]:
    size_raw = extract_size_from_title(title)
    if not size_raw:
        return None
    # Try to match against valid values list first
    found = data.find_valid(size_raw, cat_id, char_name)
    if found:
        return found
    # Daca nu exista lista de valori permise (camp freeform), returneaza marimea formatata
    if not data.valid_values(cat_id, char_name):
        try:
            num = float(size_raw.replace(",", "."))
            return f"{num:g} EU"
        except ValueError:
            return size_raw.strip()
    return None


def detect_culoare_baza(title: str, desc: str, data: MarketplaceData, cat_id,
                        char_name: str = "Culoare de baza") -> Optional[str]:
    text = (title + " " + desc).lower()
    mapping = [
        (["negru", "black", "noir"],             "Negru"),
        (["alb", "white", "blanc", "alb optic"], "Alb"),
        (["gri", "grey", "gray", "gris"],        "Gri"),
        (["albastru", "blue", "navy", "bleumarin", "bleu"], "Albastru"),
        (["rosu", "red", "rouge"],               "Rosu"),
        (["verde", "green", "vert"],              "Verde"),
        (["portocaliu", "orange"],                "Portocaliu"),
        (["galben", "yellow", "jaune"],           "Galben"),
        (["roz", "pink", "rose", "roz arctic"],  "Roz"),
        (["mov", "purple", "violet", "lila"],     "Mov"),
        (["maro", "brown", "marron", "camel"],    "Maro"),
        (["bej", "beige", "crem", "ivory"],       "Bej"),
        (["argintiu", "silver", "argint"],        "Argintiu"),
        (["auriu", "gold", "aur", "aurie"],       "Auriu"),
    ]
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None  # fara lista valida nu putem sti ce valoare e corecta
    for keywords, color in mapping:
        if any(kw in text for kw in keywords):
            if color in vs:
                return color
    return None


def detect_pentru(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Pentru:")
    if not vs:
        return None
    if any(x in text for x in ["baieti", "boys"]) and "Baieti" in vs:
        return "Baieti"
    if any(x in text for x in ["fete", "girls"]) and "Fete" in vs:
        return "Fete"
    if any(x in text for x in ["copii", "kids", "junior", "jr", "children", "copil"]):
        if "Copii" in vs:
            return "Copii"
    if any(x in text for x in ["barbati", "men", "mens", "masculin", "barbat"]):
        if "Barbati" in vs:
            return "Barbati"
    if any(x in text for x in ["dama", "women", "femei", "feminin", "doamne", "lady"]):
        if "Femei" in vs:
            return "Femei"
    return None


def detect_imprimeu(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Imprimeu:")
    if not vs:
        return None
    if any(x in text for x in ["logo", "swoosh", "jumpman", "brand", "emblem"]) and "Logo" in vs:
        return "Logo"
    if any(x in text for x in ["grafic", "graphic", "print", "imprimeu", "pattern", "model", "all over"]) and "Cu model" in vs:
        return "Cu model"
    if any(x in text for x in ["uni color", "unicolor", "solid", "simplu", "plain"]) and "Uni" in vs:
        return "Uni"
    return None


def detect_material(title: str, desc: str, data: MarketplaceData, cat_id,
                    char_name: str = "Material:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    checks = [
        (["bumbac", "cotton", "100% bumbac", "100% cotton"], "Bumbac"),
        (["fleece", "french terry", "polar"],                 "Fleece"),
        (["poliester", "polyester", "dri-fit", "drifit", "dri fit", "climacool", "climalite"], "Poliester"),
        (["nailon", "nylon"],                                  "Nailon"),
        (["piele", "leather", "cuir", "piele naturala"],      "Piele"),
        (["lana", "wool", "merinos"],                          "Lana"),
        (["acril", "acrylic"],                                 "Acril"),
        (["elastan", "elastane", "spandex", "lycra"],          "Elastan"),
        (["mesh", "plasa"],                                    "Mesh"),
        (["textil", "textile", "fabric"],                      "Textil"),
    ]
    if not vs:
        return None  # fara lista valida nu putem sti ce valoare e corecta
    for keywords, mat in checks:
        if any(kw in text for kw in keywords):
            if mat in vs:
                return mat
    return None


def detect_croiala(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Croiala:")
    if not vs:
        return None
    if any(x in text for x in ["slim fit", "slim-fit", "fitted", "slim"]) and "Slim fit" in vs:
        return "Slim fit"
    if any(x in text for x in ["regular fit", "regular-fit", "standard"]) and "Regular fit" in vs:
        return "Regular fit"
    if any(x in text for x in ["lejer", "loose", "relaxed", "oversized", "wide"]) and "Lejer" in vs:
        return "Lejer"
    if any(x in text for x in ["compresie", "compression", "tight"]) and "De compresie" in vs:
        return "De compresie"
    return None


def detect_lungime_maneca(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Lungime maneca:")
    if not vs:
        return None
    if any(x in text for x in ["fara maneca", "sleeveless", "maieu", "maiou", "tank", "vest"]):
        if "Fara maneca" in vs:
            return "Fara maneca"
    if any(x in text for x in ["maneca lunga", "long sleeve", "long-sleeve"]):
        if "Maneca lunga" in vs:
            return "Maneca lunga"
    if any(x in text for x in ["maneca scurta", "short sleeve", "short-sleeve", "tricou", "t-shirt", "tee"]):
        if "Maneca scurta" in vs:
            return "Maneca scurta"
    if any(x in text for x in ["trei sferturi", "3/4", "three quarter"]):
        if "Maneca trei sferturi" in vs:
            return "Maneca trei sferturi"
    return None


def detect_sport(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Sport:")
    sports = [
        ("Fotbal",    ["fotbal", "football", "soccer"]),
        ("Baschet",   ["baschet", "basketball", "jordan", "nba"]),
        ("Alergare",  ["alergare", "running", "run", "jogging", "marathon"]),
        ("Fitness",   ["fitness", "gym", "antrenament", "training", "workout", "crossfit"]),
        ("Tenis",     ["tenis", "tennis"]),
        ("Golf",      ["golf"]),
        ("Natatie",   ["natatie", "swimming", "swim"]),
        ("Ciclism",   ["ciclism", "cycling", "bike", "bicycle"]),
        ("Ski",       ["ski", "schi", "snow", "snowboard"]),
        ("Volei",     ["volei", "volleyball"]),
        ("Rugby",     ["rugby"]),
        ("Handbal",   ["handbal", "handball"]),
    ]
    if not vs:
        return None  # fara lista valida nu putem sti ce valoare e corecta
    for sport, keywords in sports:
        if any(kw in text for kw in keywords):
            if sport in vs:
                return sport
    return None


def detect_sezon(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Sezon:")
    if not vs:
        return None
    if any(x in text for x in ["iarna", "winter", "fleece", "thermal", "therma", "thermo", "warm", "caldura", "polar"]):
        if "Toamna-Iarna" in vs:
            return "Toamna-Iarna"
    if any(x in text for x in ["vara", "summer", "light", "usor", "lightweight", "breathable", "respirabil"]):
        if "Primavara-Vara" in vs:
            return "Primavara-Vara"
    return None


def detect_tip_produs(title: str, data: MarketplaceData, cat_id) -> Optional[str]:
    t = title.lower()
    vs = data.valid_values(cat_id, "Tip produs:")
    if not vs:
        return None
    checks = [
        (["hanorac", "hoodie", "sweatshirt"],           "Hanorac"),
        (["bluza", "fleece", "track top"],              "Bluza"),
        (["tricou", "t-shirt", "tee", "tshirt"],        "Tricou"),
        (["geaca", "jacket", "parka", "anorak"],        "Geaca"),
        (["jacheta"],                                    "Jacheta"),
        (["pantalon", "jogger", "pant", "trouser"],     "Pantaloni"),
        (["sort", "short"],                             "Pantaloni"),
        (["colanti", "legging", "tight"],               "Colanti"),
        (["sapca", "cap", "hat", "bucket hat"],         "Sapca"),
        (["caciula", "beanie", "boneta"],               "Caciula"),
        (["sosete", "sock"],                            "Sosete"),
        (["minge", "ball"],                             "Minge"),
        (["rucsac", "backpack", "ghiozdan"],            "Rucsacuri"),
        (["geanta", "bag", "borseta", "tote"],          "Geanta"),
        (["manusi", "gloves", "glove"],                 "Manusi"),
    ]
    for keywords, tip in checks:
        if any(kw in t for kw in keywords) and tip in vs:
            return tip
    return None


def detect_instructiuni(data: MarketplaceData, cat_id) -> Optional[str]:
    vs = data.valid_values(cat_id, "Instructiuni ingrijire:")
    if not vs:
        return None
    for v in ["Compatibil masina de spalat rufe", "Compatibil masina spalat rufe"]:
        if v in vs:
            return v
    return None


def detect_sistem_inchidere(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Sistem inchidere:")
    if not vs:
        return None
    checks = [
        (["velcro", "arici", "scratch"],         "Velcro"),
        (["siret", "lace", "sireturi", "laces"], "Siret"),
        (["fermoar", "zip", "zipper"],           "Fermoar"),
        (["slip-on", "slip on", "fara siret"],   "Fara inchidere"),
        (["banda elastica", "elastic band"],     "Banda elastica"),
        (["catarama", "buckle"],                 "Catarama"),
    ]
    for keywords, val in checks:
        if any(kw in text for kw in keywords) and val in vs:
            return val
    return None


def detect_stil(title: str, desc: str, data: MarketplaceData, cat_id, char_name: str = "Stil:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    # Pantofi profile
    if "Profil inalt" in vs:
        if any(x in text for x in ["high top", "high-top", "inalti"]): return "Profil inalt"
    if "Profil jos" in vs:
        if any(x in text for x in ["low", "low-top", "joasa"]): return "Profil jos"
    if "Profil mediu" in vs:
        if any(x in text for x in ["mid", "mid-top", "mediu"]): return "Profil mediu"
    # Sapca
    if "Snapback" in vs and "snapback" in text: return "Snapback"
    if "Baseball" in vs and "baseball" in text: return "Baseball"
    if "Bucket" in vs and "bucket" in text: return "Bucket"
    return None


def detect_crampoane_detasabile(data: MarketplaceData, cat_id) -> Optional[str]:
    vs = data.valid_values(cat_id, "Crampoane detasabile:")
    if "N/A" in vs:
        return "N/A"
    return None


def detect_varsta(desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    vs = data.valid_values(cat_id, "Varsta:")
    if not vs:
        return None
    text = desc.lower()
    for pattern in [r"(\d+)-(\d+)\s*ani", r"(\d+)\s*ani"]:
        m = re.search(pattern, text)
        if m:
            candidate = m.group(0).strip()
            if candidate in vs:
                return candidate
    return None


def detect_tip_inchidere(title: str, desc: str, data: MarketplaceData, cat_id) -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, "Tip inchidere:")
    if not vs:
        return None
    if any(x in text for x in ["fermoar", "zip"]) and "Fermoar" in vs: return "Fermoar"
    if any(x in text for x in ["capse", "snap"]) and "Capse" in vs: return "Capse"
    if any(x in text for x in ["nasturi", "button"]) and "Nasturi" in vs: return "Nasturi"
    if any(x in text for x in ["arici", "velcro"]) and "Velcro" in vs: return "Velcro"
    return None


def detect_lungime(title: str, desc: str, data: MarketplaceData, cat_id, char_name: str = "Tip:") -> Optional[str]:
    """Detect pant length type."""
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    if any(x in text for x in ["scurti", "short", "sort"]) and "Scurti" in vs: return "Scurti"
    if any(x in text for x in ["trei sferturi", "3/4"]) and "Trei sferturi" in vs: return "Trei sferturi"
    if any(x in text for x in ["lungi", "long", "full"]) and "Lungi" in vs: return "Lungi"
    return None


# ── Main processor ─────────────────────────────────────────────────────────────

ALL_DETECTORS = [
    # Romanian field names
    ("Marime:",              lambda t, d, mp, cid: detect_marime(t, mp, cid)),
    ("Culoare de baza",      lambda t, d, mp, cid: detect_culoare_baza(t, d, mp, cid)),
    ("Pentru:",              lambda t, d, mp, cid: detect_pentru(t, d, mp, cid)),
    ("Imprimeu:",            lambda t, d, mp, cid: detect_imprimeu(t, d, mp, cid)),
    ("Material:",            lambda t, d, mp, cid: detect_material(t, d, mp, cid)),
    ("Croiala:",             lambda t, d, mp, cid: detect_croiala(t, d, mp, cid)),
    ("Lungime maneca:",      lambda t, d, mp, cid: detect_lungime_maneca(t, d, mp, cid)),
    ("Sport:",               lambda t, d, mp, cid: detect_sport(t, d, mp, cid)),
    ("Sezon:",               lambda t, d, mp, cid: detect_sezon(t, d, mp, cid)),
    ("Tip produs:",          lambda t, d, mp, cid: detect_tip_produs(t, mp, cid)),
    ("Instructiuni ingrijire:", lambda t, d, mp, cid: detect_instructiuni(mp, cid)),
    ("Sistem inchidere:",    lambda t, d, mp, cid: detect_sistem_inchidere(t, d, mp, cid)),
    ("Stil:",                lambda t, d, mp, cid: detect_stil(t, d, mp, cid)),
    ("Crampoane detasabile:",lambda t, d, mp, cid: detect_crampoane_detasabile(mp, cid)),
    ("Varsta:",              lambda t, d, mp, cid: detect_varsta(d, mp, cid)),
    ("Tip inchidere:",       lambda t, d, mp, cid: detect_tip_inchidere(t, d, mp, cid)),
    ("Tip:",                 lambda t, d, mp, cid: detect_lungime(t, d, mp, cid, "Tip:")),
    # Hungarian field name aliases (eMAG HU and similar)
    ("Méret:",               lambda t, d, mp, cid: detect_marime(t, mp, cid, "Méret:")),
    ("Szín:",                lambda t, d, mp, cid: detect_culoare_baza(t, d, mp, cid, "Szín:")),
    ("Anyag:",               lambda t, d, mp, cid: detect_material(t, d, mp, cid, "Anyag:")),
    # Bulgarian field name aliases (eMAG BG and similar)
    ("Размер:",              lambda t, d, mp, cid: detect_marime(t, mp, cid, "Размер:")),
    ("Цвят:",                lambda t, d, mp, cid: detect_culoare_baza(t, d, mp, cid, "Цвят:")),
    ("Материал:",            lambda t, d, mp, cid: detect_material(t, d, mp, cid, "Материал:")),
]


def process_product(
    title: str,
    description: str,
    cat_name: str,
    existing_chars: dict,
    data: MarketplaceData,
    use_ai: bool = False,
    marketplace: str = "",
    offer_id: str = "",
) -> dict:
    """
    Return dict of {char_name: value} for characteristics that can be
    auto-detected and are not already filled.
    All returned values are guaranteed to be in the valid values list.

    If use_ai=True and API key is configured, remaining gaps are sent
    to Claude API for enrichment after rule-based detection.
    """
    cat_id = data.category_id(cat_name)
    if cat_id is None:
        log.warning("Categorie negasita in index: %r (titlu: %s)", cat_name, title[:60])
        return {}

    desc_clean = strip_html(description)
    results = {}

    # ── Rule-based detection ──────────────────────────────────────────────────
    for char_name, detector in ALL_DETECTORS:
        if char_name in existing_chars and existing_chars[char_name]:
            continue
        # Skip detector daca acest camp nu apartine marketplace-ului curent
        if not data.has_char(cat_id, char_name):
            continue
        try:
            val = detector(title, desc_clean, data, cat_id)
            if val:
                results[char_name] = val
                log.debug("Rule detect [%s] = %r  (titlu: %s)", char_name, val, title[:60])
        except Exception as exc:
            log.warning("Exceptie in detector %r pentru %r: %s", char_name, title[:60], exc)

    # ── AI enrichment — doar pentru caracteristici obligatorii lipsa ─────────
    if use_ai:
        try:
            from core.ai_enricher import enrich_with_ai, is_configured
            if is_configured():
                combined_existing = {**existing_chars, **results}
                mandatory = data.mandatory_chars(cat_id)
                mandatory_missing = [c for c in mandatory if not combined_existing.get(c)]

                # Log freeform mandatory fields (no valid values defined)
                for ch in mandatory_missing:
                    vs = data.valid_values(cat_id, ch)
                    if not vs:
                        log.debug(
                            "Freeform mandatory: [%s] cat_id=%s titlu=%s",
                            ch, cat_id, title[:60],
                        )

                # Skip AI daca nu lipseste nimic obligatoriu
                if mandatory_missing:
                    log.info(
                        "AI enrichment pentru %r — lipsesc obligatorii: %s",
                        title[:60], mandatory_missing,
                    )
                    char_options = {
                        ch: vals
                        for ch, vals in data._valid_values.get(cat_id, {}).items()
                        if ch not in combined_existing and len(vals) <= 40
                    }
                    ai_fills = enrich_with_ai(
                        title=title,
                        description=desc_clean,
                        category=cat_name,
                        existing={**combined_existing, "_offer_id": offer_id},
                        char_options=char_options,
                        valid_values_for_cat=data._valid_values.get(cat_id, {}),
                        mandatory_chars=mandatory_missing,
                        marketplace=marketplace,
                    )
                    for k, v in ai_fills.items():
                        if k not in results:
                            results[k] = v
                            log.debug("AI detect [%s] = %r  (titlu: %s)", k, v, title[:60])
                    still_missing = [c for c in mandatory_missing if not results.get(c) and not combined_existing.get(c)]
                    if still_missing:
                        log.warning(
                            "Obligatorii inca lipsa dupa AI pentru %r: %s",
                            title[:60], still_missing,
                        )
        except Exception as exc:
            log.error("Exceptie in AI enrichment pentru %r: %s", title[:60], exc, exc_info=True)

    return results


def explain_missing_chars(
    data: MarketplaceData,
    cat_id,
    filled_chars: dict,
    use_ai: bool = False,
) -> dict:
    """
    Pentru fiecare caracteristică obligatorie nemapată returnează motivul.
    filled_chars = {**existing, **new_chars} (tot ce s-a completat)
    """
    reasons = {}
    for char_name in data.mandatory_chars(cat_id):
        if filled_chars.get(char_name):
            continue
        valid_vals = data.valid_values(cat_id, char_name)
        if not valid_vals:
            reasons[char_name] = "Fără valori permise definite pentru această categorie"
        elif use_ai:
            reasons[char_name] = "Detectie automată și AI nu au găsit o valoare validă în titlu/descriere"
        else:
            reasons[char_name] = "Keywords nedetectate în titlu/descriere (AI dezactivat)"
    return reasons


def validate_existing(
    existing_chars: dict,
    cat_name: str,
    data: MarketplaceData,
) -> dict:
    """
    Check existing characteristics against valid values.
    Returns {char_name: current_value} for invalid ones.
    """
    cat_id = data.category_id(cat_name)
    if cat_id is None:
        return {}

    invalid = {}
    for char_name, value in existing_chars.items():
        if not value:
            continue
        vs = data.valid_values(cat_id, char_name)
        if vs and str(value).strip() not in vs:
            invalid[char_name] = value
    return invalid
