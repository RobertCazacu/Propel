"""
Characteristic processor.
Detects values from product title + description using rule-based logic,
validates against the marketplace valid-values list, and fills gaps.
"""
import re
import functools
import weakref
from bs4 import BeautifulSoup
from typing import Optional
from core.loader import MarketplaceData
from core.app_logger import get_logger

log = get_logger("marketplace.processor")


@functools.lru_cache(maxsize=512)
def _compile_wb(kw: str):
    """Cache compiled word-boundary patterns — keywords are finite (~200 unique)."""
    return re.compile(r"\b" + re.escape(kw) + r"\b")


def _wb(kw: str, text: str) -> bool:
    """Word-boundary match: prevents 'alb' matching 'album', 'rosu' matching 'caramiziu'."""
    return bool(_compile_wb(kw).search(text))


def _warn_missing_mandatory_no_values(data, cat_id, still_missing: list):
    """Emit distinct warning for mandatory chars that have no valid_values defined."""
    for ch in still_missing:
        vs = data.valid_values(cat_id, ch)
        if not vs:
            log.warning(
                "[ATENȚIE] Caracteristica obligatorie '%s' (cat=%s) nu are valori definite "
                "în datele de referință — nu poate fi completată automat.",
                ch, cat_id,
            )


def strip_html(html: str) -> str:
    if not html:
        return ""
    try:
        text = BeautifulSoup(str(html), "html.parser").get_text(" ", strip=True)
        # Colapsează orice secvență de whitespace (newline, tab, spații multiple) într-un singur spațiu
        return re.sub(r"\s+", " ", text).strip()
    except Exception:
        return str(html)


def _get_mandatory_missing_for_ai(
    cat_id: str,
    combined_existing: dict,
    data: "MarketplaceData",
) -> list:
    """Returnează caracteristicile mandatory care lipsesc SAU au valori invalide.

    P02: Include și câmpurile cu valori prezente dar invalide față de valid_values,
    astfel încât AI poate corecta valori greșite detectate de reguli.
    """
    mandatory = data.mandatory_chars(cat_id)
    missing = []
    for ch in mandatory:
        val = combined_existing.get(ch)
        if not val:
            missing.append(ch)
            continue
        vs = data.valid_values(cat_id, ch)
        if vs and val not in vs:
            missing.append(ch)
    return missing


def extract_size_from_title(title: str) -> Optional[str]:
    """Extract size from last ' - ' segment of title.

    Returns None for: missing title, no separator, or empty segment after separator.
    P08: uses `or None` to convert empty string to None.
    """
    if not title:
        return None
    parts = title.rsplit(" - ", 1)
    if len(parts) != 2:
        return None
    return parts[1].strip() or None   # P08: "" → None


# ── Individual detectors ───────────────────────────────────────────────────────

def detect_marime(title: str, data: MarketplaceData, cat_id, char_name: str = "Marime:") -> Optional[str]:
    size_raw = extract_size_from_title(title)
    if not size_raw:
        return None
    # Try to match against valid values list first (full segment)
    found = data.find_valid(size_raw, cat_id, char_name)
    if found:
        return found
    # Try leading numeric token only — handles "42 férfi futócipő" → "42"
    m = re.match(r'^(\d+(?:[,.]\d+)?)', size_raw.strip())
    if m:
        size_num = m.group(1)
        found = data.find_valid(size_num, cat_id, char_name)
        if found:
            return found
        if not data.valid_values(cat_id, char_name):
            try:
                num = float(size_num.replace(",", "."))
                # P20: filter year-like numbers (>200 or <1) — anti year-in-title false positive
                if 1 <= num <= 200:
                    return f"{num:g} EU"
            except ValueError:
                pass
    # Daca nu exista lista de valori permise (camp freeform), returneaza marimea formatata
    if not data.valid_values(cat_id, char_name):
        try:
            num = float(size_raw.replace(",", "."))
            # P20: filter year-like numbers (>200 or <1)
            if 1 <= num <= 200:
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
    for keywords, color in mapping:
        if any(_wb(kw, text) for kw in keywords):
            if vs:
                found = data.find_valid(color, cat_id, char_name)
                if found:
                    log.debug("detect_culoare_baza: %r → %r", keywords, found)
                    return found
            else:
                # P19: freeform field — returnează valoarea detectată direct
                log.debug("detect_culoare_baza freeform: %r → %r", keywords, color)
                return color
    return None


def detect_pentru(title: str, desc: str, data: MarketplaceData, cat_id,
                  char_name: str = "Pentru:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    vs_lower = {v.lower(): v for v in vs}
    if any(_wb(x, text) for x in ["baieti", "boys"]):
        for key in ("baieti", "boys", "boy"):
            if key in vs_lower: return vs_lower[key]
    if any(_wb(x, text) for x in ["fete", "girls"]):
        for key in ("fete", "girls", "girl"):
            if key in vs_lower: return vs_lower[key]
    if any(_wb(x, text) for x in ["copii", "kids", "junior", "jr", "children", "copil"]):
        for key in ("copii", "kids", "children", "junior", "unisex kids"):
            if key in vs_lower: return vs_lower[key]
    if any(_wb(x, text) for x in ["barbati", "men", "mens", "masculin", "barbat", "férfi"]):
        for key in ("barbati", "men", "male", "férfi", "férfiak", "herren", "mężczyźni"):
            if key in vs_lower: return vs_lower[key]
    if any(_wb(x, text) for x in ["dama", "women", "femei", "feminin", "doamne", "lady", "női", "nők"]):
        for key in ("femei", "women", "female", "dama", "lady", "női", "nők", "damen", "kobiety"):
            if key in vs_lower: return vs_lower[key]
    if any(_wb(x, text) for x in ["unisex"]):
        for key in ("unisex",):
            if key in vs_lower: return vs_lower[key]
    return None


def detect_imprimeu(title: str, desc: str, data: MarketplaceData, cat_id,
                    char_name: str = "Imprimeu:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    if any(_wb(x, text) for x in ["logo", "swoosh", "jumpman", "brand", "emblem"]) and "Logo" in vs:
        return "Logo"
    if any(_wb(x, text) for x in ["grafic", "graphic", "imprimeu", "pattern", "all over", "all-over"]):
        found = data.find_valid("Cu model", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["uni color", "unicolor", "solid", "simplu", "plain"]) and "Uni" in vs:
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
    for keywords, mat in checks:
        if any(_wb(kw, text) for kw in keywords):
            found = data.find_valid(mat, cat_id, char_name)
            if found:
                log.debug("detect_material: %r → %r", keywords, found)
                return found
            if not vs:
                log.warning(
                    "detect_material: câmp '%s' restrictiv/freeform fără valori, "
                    "returnez '%s' pentru validator",
                    char_name, mat,
                )
                return mat
    return None


def detect_croiala(title: str, desc: str, data: MarketplaceData, cat_id,
                   char_name: str = "Croiala:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    if any(_wb(x, text) for x in ["slim fit", "slim-fit", "fitted", "slim"]):
        found = data.find_valid("Slim fit", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["regular fit", "regular-fit", "standard"]):
        found = data.find_valid("Regular fit", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["lejer", "loose", "relaxed", "oversized", "wide"]):
        found = data.find_valid("Lejer", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["compresie", "compression", "tight"]):
        found = data.find_valid("De compresie", cat_id, char_name)
        if found: return found
    return None


def detect_lungime_maneca(title: str, desc: str, data: MarketplaceData, cat_id,
                          char_name: str = "Lungime maneca:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    if any(_wb(x, text) for x in ["fara maneca", "sleeveless", "maieu", "maiou", "tank", "vest"]):
        found = data.find_valid("Fara maneca", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["maneca lunga", "long sleeve", "long-sleeve"]):
        found = data.find_valid("Maneca lunga", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["maneca scurta", "short sleeve", "short-sleeve", "tricou", "t-shirt", "tee"]):
        found = data.find_valid("Maneca scurta", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["trei sferturi", "3/4", "three quarter"]):
        found = data.find_valid("Maneca trei sferturi", cat_id, char_name)
        if found: return found
    return None


def detect_sport(title: str, desc: str, data: MarketplaceData, cat_id,
                 char_name: str = "Sport:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    sports = [
        ("Fotbal",    ["fotbal", "football", "soccer"]),
        ("Baschet",   ["baschet", "basketball", "jordan", "nba"]),
        ("Alergare",  ["alergare", "running", "run", "jogging", "marathon", "futócipő", "futás"]),
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
    for sport, keywords in sports:
        if any(_wb(kw, text) for kw in keywords):
            found = data.find_valid(sport, cat_id, char_name)
            if found:
                log.debug("detect_sport: %r → %r", keywords, found)
                return found
            if not vs:
                log.warning(
                    "detect_sport: câmp '%s' restrictiv/freeform fără valori, "
                    "returnez '%s' pentru validator",
                    char_name, sport,
                )
                return sport
    return None


def detect_sezon(title: str, desc: str, data: MarketplaceData, cat_id,
                 char_name: str = "Sezon:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    _winter_hard = ["iarna", "winter", "thermal", "therma", "thermo", "warm", "caldura", "polar"]
    _lightweight_kw = ["light", "usor", "lightweight", "breathable", "respirabil"]
    _is_winter = any(_wb(x, text) for x in _winter_hard)
    _is_fleece_heavy = _wb("fleece", text) and not any(_wb(x, text) for x in _lightweight_kw)
    if _is_winter or _is_fleece_heavy:
        found = data.find_valid("Toamna-Iarna", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["vara", "summer"] + _lightweight_kw):
        found = data.find_valid("Primavara-Vara", cat_id, char_name)
        if found: return found
    return None


def detect_tip_produs(title: str, data: MarketplaceData, cat_id,
                      char_name: str = "Tip produs:") -> Optional[str]:
    t = title.lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    checks = [
        (["hanorac", "hoodie", "sweatshirt"],           "Hanorac"),
        (["bluza", "fleece", "track top"],              "Bluza"),
        (["tricou", "t-shirt", "tee", "tshirt"],        "Tricou"),
        (["geaca", "jacket", "parka", "anorak"],        "Geaca"),
        (["jacheta"],                                    "Jacheta"),
        (["pantalon", "jogger", "pant", "trouser"],     "Pantaloni"),
        (["sort", "pantaloni scurti", "shorts"],        "Pantaloni"),
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
        if any(_wb(kw, t) for kw in keywords):
            found = data.find_valid(tip, cat_id, char_name)
            if found:
                return found
    return None


def detect_instructiuni(data: MarketplaceData, cat_id,
                        char_name: str = "Instructiuni ingrijire:") -> Optional[str]:
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    for v in ["Compatibil masina de spalat rufe", "Compatibil masina spalat rufe"]:
        if v in vs:
            return v
    return None


def detect_sistem_inchidere(title: str, desc: str, data: MarketplaceData, cat_id,
                             char_name: str = "Sistem inchidere:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    checks = [
        (["velcro", "arici", "scratch"],         "Velcro"),
        (["siret", "lace", "sireturi", "laces"], "Siret"),
        (["fermoar", "zip", "zipper"],           "Fermoar"),
        (["slip-on", "slip on", "fara siret"],   "Fara inchidere"),
        (["banda elastica", "elastic band"],     "Banda elastica"),
        (["catarama", "buckle"],                 "Catarama"),
    ]
    for keywords, val in checks:
        if any(_wb(kw, text) for kw in keywords):
            found = data.find_valid(val, cat_id, char_name)
            if found:
                return found
            if not vs:
                log.warning(
                    "detect_sistem_inchidere: câmp '%s' restrictiv/freeform fără valori, "
                    "returnez '%s' pentru validator",
                    char_name, val,
                )
                return val
    return None


def detect_stil(title: str, desc: str, data: MarketplaceData, cat_id, char_name: str = "Stil:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    # Pantofi profile
    if any(_wb(x, text) for x in ["high top", "high-top", "inalti"]):
        found = data.find_valid("Profil inalt", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["low", "low-top", "joasa"]):
        found = data.find_valid("Profil jos", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["mid", "mid-top", "mediu"]):
        found = data.find_valid("Profil mediu", cat_id, char_name)
        if found: return found
    # Sapca
    if _wb("snapback", text):
        found = data.find_valid("Snapback", cat_id, char_name)
        if found: return found
    if _wb("baseball", text):
        found = data.find_valid("Baseball", cat_id, char_name)
        if found: return found
    if _wb("bucket", text):
        found = data.find_valid("Bucket", cat_id, char_name)
        if found: return found
    return None


def detect_crampoane_detasabile(data: MarketplaceData, cat_id,
                                char_name: str = "Crampoane detasabile:") -> Optional[str]:
    vs = data.valid_values(cat_id, char_name)
    if "N/A" in vs:
        return "N/A"
    return None


def detect_varsta(desc: str, data: MarketplaceData, cat_id,
                  char_name: str = "Varsta:") -> Optional[str]:
    vs = data.valid_values(cat_id, char_name)
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


def detect_tip_inchidere(title: str, desc: str, data: MarketplaceData, cat_id,
                         char_name: str = "Tip inchidere:") -> Optional[str]:
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    if any(_wb(x, text) for x in ["fermoar", "zip"]):
        found = data.find_valid("Fermoar", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["capse", "snap"]):
        found = data.find_valid("Capse", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["nasturi", "button"]):
        found = data.find_valid("Nasturi", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["arici", "velcro"]):
        found = data.find_valid("Velcro", cat_id, char_name)
        if found: return found
    return None


def detect_lungime(title: str, desc: str, data: MarketplaceData, cat_id, char_name: str = "Tip:") -> Optional[str]:
    """Detect pant length type."""
    text = (title + " " + desc).lower()
    vs = data.valid_values(cat_id, char_name)
    if not vs:
        return None
    if any(_wb(x, text) for x in ["scurti", "sort"]):
        found = data.find_valid("Scurti", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["trei sferturi", "3/4"]):
        found = data.find_valid("Trei sferturi", cat_id, char_name)
        if found: return found
    if any(_wb(x, text) for x in ["lungi", "long", "full"]):
        found = data.find_valid("Lungi", cat_id, char_name)
        if found: return found
    return None


# ── Main processor ─────────────────────────────────────────────────────────────

# ── Detector concepts — language-agnostic ──────────────────────────────────────
# Each entry: ([known_names_any_language], lambda(t, d, mp, cid, cn) -> Optional[str])
# Resolution: for each concept, find the first name that exists in the category,
# then call the detector with that canonical name. Adding a new language = add synonyms.
DETECTOR_CONCEPTS = [
    (
        ["Marime:", "Méret:", "Размер:", "Size", "Rozmiar", "Größe", "Veľkosť",
         "Beden", "Tamanho"],
        lambda t, d, mp, cid, cn: detect_marime(t, mp, cid, cn),
    ),
    (
        ["Culoare de baza", "Szín:", "Цвят:", "Color", "Case Color", "Strap Color",
         "Web Color", "Dial Color", "Kolor", "Farba", "Barva", "Colore", "Couleur",
         "Band Color", "Lens Color"],
        lambda t, d, mp, cid, cn: detect_culoare_baza(t, d, mp, cid, cn),
    ),
    (
        ["Pentru:", "Gender", "Sex", "Płeć", "Nem", "Пол", "Cinsiyet",
         "Geschlecht", "Pohlaví", "Pohlavie"],
        lambda t, d, mp, cid, cn: detect_pentru(t, d, mp, cid, cn),
    ),
    (
        ["Imprimeu:", "Pattern", "Print", "Wzór", "Minta", "Шаблон"],
        lambda t, d, mp, cid, cn: detect_imprimeu(t, d, mp, cid, cn),
    ),
    (
        ["Material:", "Anyag:", "Материал:", "Material", "Materiał", "Materiál",
         "Strap Material", "Case Material", "Lining Material", "Materiale",
         "Matière", "Malzeme"],
        lambda t, d, mp, cid, cn: detect_material(t, d, mp, cid, cn),
    ),
    (
        ["Croiala:", "Fit", "Cut", "Fassung", "Krój", "Szabás"],
        lambda t, d, mp, cid, cn: detect_croiala(t, d, mp, cid, cn),
    ),
    (
        ["Lungime maneca:", "Sleeve Length", "Sleeve", "Długość rękawa", "Ujjhossz"],
        lambda t, d, mp, cid, cn: detect_lungime_maneca(t, d, mp, cid, cn),
    ),
    (
        ["Sport:", "Sport", "Activity", "Dyscyplina sportu", "Sportág"],
        lambda t, d, mp, cid, cn: detect_sport(t, d, mp, cid, cn),
    ),
    (
        ["Sezon:", "Season", "Sezon", "Évszak", "Сезон"],
        lambda t, d, mp, cid, cn: detect_sezon(t, d, mp, cid, cn),
    ),
    (
        ["Tip produs:", "Product Type", "Ürün Tipi", "Typ produktu", "Terméktípus"],
        lambda t, d, mp, cid, cn: detect_tip_produs(t, mp, cid, cn),
    ),
    (
        ["Instructiuni ingrijire:", "Care Instructions", "Care Instructions (General)",
         "Instrucțiuni de îngrijire", "Pflegehinweise", "Instrukcje pielęgnacji"],
        lambda t, d, mp, cid, cn: detect_instructiuni(mp, cid, cn),
    ),
    (
        ["Sistem inchidere:", "Closure", "Fastening", "Band Style",
         "Zapięcie", "Zárás", "Застёжка"],
        lambda t, d, mp, cid, cn: detect_sistem_inchidere(t, d, mp, cid, cn),
    ),
    (
        ["Stil:", "Style", "Styl", "Stílus", "Стиль"],
        lambda t, d, mp, cid, cn: detect_stil(t, d, mp, cid, cn),
    ),
    (
        ["Crampoane detasabile:"],
        lambda t, d, mp, cid, cn: detect_crampoane_detasabile(mp, cid, cn),
    ),
    (
        ["Varsta:", "Age Group", "Age", "Wiek", "Kor", "Возраст"],
        lambda t, d, mp, cid, cn: detect_varsta(d, mp, cid, cn),
    ),
    (
        ["Tip inchidere:", "Closure Type", "Fastener Type", "Typ zapięcia"],
        lambda t, d, mp, cid, cn: detect_tip_inchidere(t, d, mp, cid, cn),
    ),
    (
        ["Tip:", "Type", "Typ", "Típus"],
        lambda t, d, mp, cid, cn: detect_lungime(t, d, mp, cid, cn),
    ),
]

# Module-level cache: WeakKeyDictionary data -> {cat_id -> tuple of (char_name, detector)}.
# P05: WeakKeyDictionary previne refolosirea cache-ului după GC (id(data) reuse).
# Entriile expiră automat când obiectul data e distrus.
_applicable_detectors_cache: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()


def process_product(
    title: str,
    description: str,
    cat_name: str,
    existing_chars: dict,
    data: MarketplaceData,
    use_ai: bool = False,
    marketplace: str = "",
    offer_id: str = "",
    product_meta: dict = None,
    target_chars: list | None = None,
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
    _char_log: list[dict] = []

    # ── Rule-based detection ──────────────────────────────────────────────────
    # Cache applicable detectors per (data, cat_id) via WeakKeyDictionary.
    # P05: WeakKeyDictionary auto-expires entries when data is GC'd, preventing
    # stale cache reuse after memory address recycling (id(data) reuse).
    _data_cache = _applicable_detectors_cache.setdefault(data, {})
    if cat_id not in _data_cache:
        # For each concept, find the first synonym that exists as a characteristic
        # in this category (canonical_char_name returns None if not found).
        applicable_list = []
        for concept_names, detector in DETECTOR_CONCEPTS:
            for name in concept_names:
                canonical = data.canonical_char_name(cat_id, name)
                if canonical:
                    applicable_list.append((canonical, detector))
                    break  # one match per concept
        _data_cache[cat_id] = tuple(applicable_list)
        if not _data_cache[cat_id]:
            log.warning(
                "Niciun detector aplicabil pentru cat_id=%s cat_name=%r — verifică caracteristicile importate",
                cat_id, cat_name,
            )
    applicable = _data_cache[cat_id]

    for char_name, detector in applicable:
        if char_name in existing_chars and existing_chars[char_name]:
            continue
        try:
            val = detector(title, desc_clean, data, cat_id, char_name)
            if val:
                results[char_name] = val
                log.debug("Rule detect [%s] = %r  (titlu: %s)", char_name, val, title[:60])
                _char_log.append({
                    "char_name":           char_name,
                    "char_canonical":      data.canonical_char_name(cat_id, char_name) or char_name,
                    "source":              "rule",
                    "value_input":         val,
                    "value_mapped":        val,
                    "allowed_values_count": len(data.valid_values(cat_id, char_name)),
                    "validation_pass":     True,
                })
        except Exception as exc:
            log.warning("Exceptie in detector %r pentru %r: %s", char_name, title[:60], exc)

    # ── AI enrichment — AI-first: umple TOATE câmpurile lipsă ───────────────
    if use_ai:
        try:
            from core.ai_enricher import enrich_with_ai, is_configured
            if is_configured():
                combined_existing = {**existing_chars, **results}
                mandatory_missing = _get_mandatory_missing_for_ai(cat_id, combined_existing, data)  # P02 — for logging only

                # Log freeform mandatory fields (no valid values defined)
                for ch in mandatory_missing:
                    vs = data.valid_values(cat_id, ch)
                    if not vs:
                        log.debug(
                            "Freeform mandatory: [%s] cat_id=%s titlu=%s",
                            ch, cat_id, title[:60],
                        )

                # AI-first: build char_options for ALL missing fields (not just mandatory).
                # Fields pre-filled by rule-based are excluded via combined_existing.
                # If target_chars is set, restrict to only those characteristics.
                all_cat_chars = data._valid_values.get(cat_id, {})
                # Color fields are allowed through even when >80 values (capped at 50)
                # so AI can still pick a color even in categories with large value lists.
                # DETECTOR_CONCEPTS[1][0] = the color concept synonyms list.
                _color_synonyms = {n.casefold() for n in DETECTOR_CONCEPTS[1][0]}
                char_options = {
                    ch: sorted(vals)[:50] if len(vals) > 50 else vals
                    for ch, vals in all_cat_chars.items()
                    if not combined_existing.get(ch)
                    and (len(vals) <= 80 or ch.casefold() in _color_synonyms)
                    and (target_chars is None or ch in target_chars)
                }
                _skipped_over80 = [ch for ch, vals in all_cat_chars.items() if len(vals) > 80 and not combined_existing.get(ch) and ch.casefold() not in _color_synonyms]
                _prefilled = [ch for ch in all_cat_chars if combined_existing.get(ch)]
                log.debug(
                    "char_options build [%s] cat=%r: total=%d trimitere=%d prefilled=%d excluse(>80vals)=%s",
                    title[:50], cat_name, len(all_cat_chars),
                    len(char_options), len(_prefilled),
                    _skipped_over80 if _skipped_over80 else "none",
                )

                # Early exit: nothing missing → skip AI call
                if not char_options:
                    log.debug(
                        "AI skip — toate câmpurile completate sau excluse pentru %r "
                        "(prefilled=%d, excluse>80=%d)",
                        title[:60], len(_prefilled), len(_skipped_over80),
                    )
                else:
                    log.info(
                        "AI-first enrichment pentru %r — %d câmpuri lipsă (din care %d obligatorii)",
                        title[:60], len(char_options), len(mandatory_missing),
                    )
                    _meta = product_meta or {}
                    ai_fills, ai_suggested = enrich_with_ai(
                        title=title,
                        description=desc_clean,
                        category=cat_name,
                        existing={**combined_existing, "_offer_id": offer_id},
                        char_options=char_options,
                        valid_values_for_cat=data._valid_values.get(cat_id, {}),
                        mandatory_chars=None,  # AI-first: fill all, not just mandatory
                        marketplace=marketplace,
                        product_meta=_meta,
                        data=data,
                        ean=_meta.get("ean") or None,
                        brand=_meta.get("brand") or None,
                    )
                    _cat_vals = data._valid_values.get(cat_id, {})
                    ai_filled_count = 0
                    ai_rejected_count = 0
                    for k, v in ai_fills.items():
                        if k not in results:
                            results[k] = v
                            ai_filled_count += 1
                            log.debug("AI detect [%s] = %r  (titlu: %s)", k, v, title[:60])
                        _char_log.append({
                            "char_name":           k,
                            "char_canonical":      data.canonical_char_name(cat_id, k) or k,
                            "source":              "ai",
                            "value_input":         str(v),
                            "value_mapped":        str(v),
                            "allowed_values_count": len(_cat_vals.get(k, set())),
                            "validation_pass":     True,
                        })
                    for k, v in ai_suggested.items():
                        if k not in ai_fills:
                            ai_rejected_count += 1
                            _char_log.append({
                                "char_name":           k,
                                "char_canonical":      data.canonical_char_name(cat_id, k),
                                "source":              "ai",
                                "value_input":         str(v),
                                "value_mapped":        None,
                                "allowed_values_count": len(_cat_vals.get(k, set())),
                                "validation_pass":     False,
                            })
                    log.info(
                        "AI-first rezultate pentru %r: %d completate, %d respinse de validare",
                        title[:60], ai_filled_count, ai_rejected_count,
                    )
                    still_missing = [c for c in mandatory_missing if not results.get(c) and not combined_existing.get(c)]
                    if still_missing:
                        log.warning(
                            "Obligatorii inca lipsa dupa AI pentru %r: %s",
                            title[:60], still_missing,
                        )
                        _warn_missing_mandatory_no_values(data, cat_id, still_missing)
        except Exception as exc:
            log.error("Exceptie in AI enrichment pentru %r: %s", title[:60], exc, exc_info=True)

    # ── Post-validation gate — safety net before returning ───────────────────
    # Ensures every (char, value) pair in results is valid per characteristics/values tables.
    # Rule detectors already return find_valid-validated values; AI uses strict mode above.
    # This gate catches any edge-cases and canonicalises key names to characteristics display form.
    if results:
        try:
            from core.char_validator import validate_new_chars_strict
            validated_results, gate_audit = validate_new_chars_strict(
                results, cat_id, data, source="gate"
            )
            gate_rejected = [e for e in gate_audit if not e["accept"]]
            for entry in gate_rejected:
                log.warning(
                    "Post-gate rejection: char=%r value=%r reason=%s",
                    entry["char_input"], entry["value_input"], entry["reason"],
                )
            results = validated_results
        except Exception as exc:
            log.warning("Post-gate exceptie (non-fatal): %s", exc)

    if _char_log:
        try:
            from core.ai_logger import log_char_source_detail
            log_char_source_detail(
                offer_id=offer_id,
                marketplace=marketplace,
                title=title,
                category=cat_name,
                char_entries=_char_log,
            )
        except Exception:
            pass

    # ── Per-product summary (INFO) ─────────────────────────────────────────────
    if offer_id and cat_id:
        try:
            mandatory = data.mandatory_chars(cat_id)
            all_filled = {**existing_chars, **results}
            filled_parts = []
            for e in _char_log:
                if e.get("validation_pass") and (e.get("value_mapped") or e.get("value_input")):
                    val = e.get("value_mapped") or e.get("value_input", "")
                    filled_parts.append(f"{e['char_name']}={val!r}[{e.get('source', '?')}]")
            missing_parts = []
            for ch in mandatory:
                if all_filled.get(ch):
                    continue
                vs = data.valid_values(cat_id, ch)
                if not vs:
                    reason = "no_values"
                elif use_ai:
                    reason = "AI+rule_no_match"
                else:
                    reason = "rule_no_match"
                missing_parts.append(f"{ch}:{reason}")
            log.info(
                "[SUMAR] offer=%s cat=%r | filled(%d): %s | mandatory_lipsă(%d): %s",
                offer_id, cat_name,
                len(filled_parts), ", ".join(filled_parts) or "none",
                len(missing_parts), ", ".join(missing_parts) or "none",
            )
        except Exception:
            pass

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
        if vs:
            val_str = str(value).strip()
            if val_str not in vs:
                # Fallback: case-insensitive check înainte de a marca ca invalid
                vs_lower = {v.lower() for v in vs}
                if val_str.lower() not in vs_lower:
                    invalid[char_name] = value
    return invalid
