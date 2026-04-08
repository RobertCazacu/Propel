"""
Canonical color clusters — multilingual synonym dictionary.

Structure: canonical_key → frozenset of normalized synonyms.

Languages covered: RO, EN, HU, BG, PL, EL (Greek), DE, FR, IT, ES partial.
Commercial/nuance terms are included (e.g. champagne, nude, teal, mauve).

Usage:
    from core.color_mapper.synonyms import find_cluster_for

    find_cluster_for("purple")  # returns "mov" (canonical key)
    CLUSTERS["mov"]             # returns all synonyms in that cluster
"""
from __future__ import annotations
from core.color_mapper.normalize import normalize_color_text

# ── Raw cluster definitions ────────────────────────────────────────────────────
# Key = canonical family name (arbitrary, used internally)
# Value = list of synonyms in any language, any case (normalized at build time)

_RAW_CLUSTERS: dict[str, list[str]] = {
    "negru": [
        "negru", "black", "noir", "schwarz", "fekete", "czarny",
        "μαύρο", "mavro", "черен", "чёрный", "negra", "nero",
        "antracit", "anthracite", "charcoal", "carbune", "grafit",
        "jet black", "onyx",
    ],
    "alb": [
        "alb", "white", "blanc", "weiss", "feher", "bialy",
        "λευκό", "lefko", "бял", "bianco", "blanco",
        "alb optic", "optical white", "off white", "off-white", "snow",
    ],
    "gri": [
        "gri", "gray", "grey", "grau", "szurke", "szary",
        "γκρι", "gkri", "сив", "grigio", "gris",
        "melange", "melanj", "heather", "marl", "gri deschis", "gri inchis",
        "light gray", "dark gray", "light grey", "dark grey",
        "slate", "ash", "smoke",
    ],
    "argintiu": [
        "argintiu", "silver", "ezust", "srebrny",
        "ασημί", "asimi", "сребрист", "argento", "plata",
        "metalic argintiu", "metallic silver", "chrome",
    ],
    "rosu": [
        "rosu", "roșu", "red", "rouge", "rot", "piros", "czerwony",
        "κόκκινο", "kokkino", "червен", "rosso", "rojo",
        "scarlet", "carmin", "carmine", "tomato", "rosu aprins",
        "bright red", "crimson",
    ],
    "roz": [
        "roz", "pink", "rose", "rosa", "rozsaszin", "rozowy",
        "ροζ", "roz", "розов", "rosato",
        "roz pudrat", "dusty pink", "blush", "salmon", "somon",
        "hot pink", "fuchsia", "fuxia", "magenta",
    ],
    "portocaliu": [
        "portocaliu", "orange", "narancssarga", "pomaranczowy",
        "πορτοκαλί", "portokali", "оранжев", "arancione",
        "coral", "corai", "peach", "piersica", "terracota", "terra",
        "burnt orange",
    ],
    "galben": [
        "galben", "yellow", "jaune", "gelb", "sarga", "zolty",
        "κίτρινο", "kitrino", "жълт", "giallo", "amarillo",
        "mustard", "mustar", "lime", "lemon", "lamaie",
        "neon yellow", "gold yellow",
    ],
    "verde": [
        "verde", "green", "vert", "grun", "zold", "zielony",
        "πράσινο", "prasino", "зелен", "verde", "verde",
        "khaki", "kaki", "olive", "oliv", "masliniu",
        "forest green", "army green", "military", "militar",
        "lime green", "neon green", "sage", "mint green",
        "hunter green", "emerald", "smarald",
    ],
    "albastru": [
        "albastru", "blue", "bleu", "blau", "kek", "niebieski",
        "μπλε", "mple", "син", "azzurro", "azul",
        "cobalt", "cobalt blue", "electric blue", "royal blue",
        "albastru electric", "albastru royal", "denim",
        "powder blue", "sky blue", "cerulean", "cornflower",
    ],
    "bleumarin": [
        "bleumarin", "navy", "navy blue", "marine", "sotétkék",
        "granatowy", "ναυτικό", "темносин", "тъмносин",
        "blu navy", "azul marino",
        "indigo", "midnight blue", "dark blue", "albastru inchis",
        "dark navy", "ink blue",
    ],
    "mov": [
        "mov", "lila", "violet", "purple", "lilas", "liliowy",
        "λιλά", "lila", "лилав", "μωβ", "mwv", "bíbor", "viola",
        "liliac", "lavanda", "lavender", "mauve", "mouve",
        "orchid", "plum", "pruna", "amethyst", "ametist",
        "grape", "eggplant", "wisteria",
    ],
    "maro": [
        "maro", "brown", "marron", "braun", "barna", "brazowy",
        "καφέ", "kafe", "кафяв", "marrone", "marrón",
        "chocolate", "ciocolata", "camel", "tan",
        "cognac", "coniac", "chestnut", "castan",
        "walnut", "nuc", "rust", "rugina",
        "cinnamon", "scortisoara", "hazel", "aluna",
    ],
    "bej": [
        "bej", "beige", "bezs", "bezowy",
        "μπεζ", "mpez", "бежов", "beige", "beige",
        "cream", "crema", "ivory", "fildes", "lapte",
        "nude", "nude color", "sand", "nisip", "dune",
        "natural", "ecru", "linen", "in", "taupe",
        "stone", "piatra", "champagne", "sampanie",
    ],
    "turcoaz": [
        "turcoaz", "turquoise", "turkiz", "turkusowy",
        "τιρκουάζ", "tirkoaz", "тюркоаз", "turchese",
        "teal", "aqua", "cyan", "mint", "menta",
        "aquamarine", "acvamarin", "sea green", "petrol",
        "duck blue", "albastru verzui",
    ],
    "visiniu": [
        "visiniu", "burgundy", "bordo", "bordeaux", "burgund",
        "бордо", "bordó", "cherry", "cirese",
        "wine", "vin", "marsala", "oxblood",
        "claret", "deep red", "dark red", "rosu inchis",
        "merlot",
    ],
    "auriu": [
        "auriu", "gold", "aur", "arany", "zloty",
        "χρυσό", "chryso", "злато", "oro", "dorado",
        "golden", "dourado", "champagne gold", "rose gold",
        "aur roz",
    ],
    "multicolor": [
        "multicolor", "multi", "multi color", "több színű",
        "wielokolorowy", "πολύχρωμο", "многоцветен",
        "colorat", "printed", "print", "pattern", "tipar",
        "tie dye", "tie-dye",
    ],
}

# ── Build normalized lookup ────────────────────────────────────────────────────

# CLUSTERS: canonical_key → frozenset of normalized synonyms
CLUSTERS: dict[str, frozenset] = {
    key: frozenset(normalize_color_text(s) for s in synonyms)
    for key, synonyms in _RAW_CLUSTERS.items()
}

# Reverse index: normalized_term → canonical_key
_TERM_TO_CLUSTER: dict[str, str] = {}
for _key, _terms in CLUSTERS.items():
    for _term in _terms:
        _TERM_TO_CLUSTER[_term] = _key

# Clusters that frequently produce near-ties — trigger semantic check in Phase 2
AMBIGUOUS_CLUSTERS: frozenset[str] = frozenset([
    "mov", "turcoaz", "visiniu", "bej",
])


def find_cluster_for(text: str) -> str | None:
    """
    Return the canonical cluster key for a normalized color string, or None.

    Example:
        find_cluster_for("purple") → "mov"
        find_cluster_for("lila")   → "mov"
        find_cluster_for("teal")   → "turcoaz"
    """
    norm = normalize_color_text(text)
    return _TERM_TO_CLUSTER.get(norm)


def cluster_synonyms(cluster_key: str) -> frozenset:
    """Return all normalized synonyms for a given cluster key."""
    return CLUSTERS.get(cluster_key, frozenset())


def is_ambiguous_cluster(cluster_key: str | None) -> bool:
    return cluster_key in AMBIGUOUS_CLUSTERS
