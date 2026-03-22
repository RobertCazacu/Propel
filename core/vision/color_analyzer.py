"""
Algorithmic color detection from images.

Pipeline:
  1. Resize + crop borders for speed.
  2. Detect background from corner pixels (not a fixed threshold).
  3. Remove background + very dark shadows.
  4. Quantize with PIL FASTOCTREE.
  5. Neutral avoidance: if dominant is white/gray/black but a chromatic color
     has >= 12% share, prefer the chromatic one.
  6. Map to named color family via HSV ranges.
  7. Return ColorResult with confidence and multicolor flag.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from io import BytesIO
from typing import Optional

from PIL import Image


# ── Result dataclass ──────────────────────────────────────────────────────────

@dataclass
class ColorResult:
    dominant_color_raw: str         = ""    # "rgb(R,G,B)"
    dominant_color_normalized: str  = ""    # "Negru", "Albastru", ...
    secondary_color_raw: str        = ""
    secondary_color_normalized: str = ""
    confidence: float               = 0.0   # 0..1
    is_multicolor: bool             = False
    palette_rgb: list               = field(default_factory=list)  # [(R,G,B), ...]


# ── Multicolor value aliases per marketplace language ─────────────────────────

_MULTICOLOR_ALIASES = ["Multicolor", "Több színű", "Многоцветен", "Multi"]

# ── Color family → Romanian/HU/BG terms (for _map_to_valid fallback) ─────────
# Used by image_analyzer._map_to_valid which already does case-insensitive match,
# so we just need to return the right Romanian normalized name.

_FAMILY_TO_NORMALIZED = {
    "black":      "Negru",
    "white":      "Alb",
    "gray":       "Gri",
    "silver":     "Gri",
    "red":        "Rosu",
    "pink":       "Roz",
    "orange":     "Portocaliu",
    "yellow":     "Galben",
    "green":      "Verde",
    "blue":       "Albastru",
    "navy":       "Bleumarin",
    "purple":     "Mov",
    "brown":      "Maro",
    "beige":      "Bej",
    "turquoise":  "Turcoaz",
    "burgundy":   "Visiniu",
    "khaki":      "Kaki",
    "multicolor": "Multicolor",
    "other":      "",
}

# Per-family search terms for pick_best_accepted_color
# Ordered by specificity (most specific first)
_FAMILY_TERMS: dict[str, list[str]] = {
    "black":      ["negru", "black", "noir", "черн", "fekete"],
    "white":      ["alb", "white", "blanc", "бял", "fehér"],
    "gray":       ["gri", "gray", "grey", "сив", "szürke"],
    "silver":     ["argintiu", "silver", "gri", "ezüst", "сребрист"],
    "red":        ["rosu", "roșu", "red", "rouge", "червен", "piros"],
    "pink":       ["roz", "pink", "rose", "rosa", "розов", "rózsaszín"],
    "orange":     ["portocaliu", "orange", "naranja", "оранжев", "narancssárga"],
    "yellow":     ["galben", "yellow", "jaune", "жълт", "sárga"],
    "green":      ["verde", "green", "vert", "зелен", "zöld"],
    "blue":       ["albastru", "blue", "bleu", "azul", "син", "kék"],
    "navy":       ["bleumarin", "navy", "marine", "темносин", "sötétkék"],
    "purple":     ["mov", "lila", "purple", "violet", "лилав"],
    "brown":      ["maro", "brown", "marron", "кафяв", "barna"],
    "beige":      ["bej", "beige", "бежов", "bézs"],
    "turquoise":  ["turcoaz", "turquoise", "türkiz", "тюркоаз"],
    "burgundy":   ["visiniu", "bordo", "burgund", "burgundy", "бордо", "bordó"],
    "khaki":      ["kaki", "khaki", "хаки"],
    "multicolor": ["multicolor", "multi", "több színű", "многоцветен"],
    "other":      [],
}


# ── HSV helpers ───────────────────────────────────────────────────────────────

def _rgb_to_hsv(rgb: tuple) -> tuple:
    """Return (H 0-360, S 0-1, V 0-1)."""
    r, g, b = rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0
    mx = max(r, g, b)
    mn = min(r, g, b)
    diff = mx - mn
    if diff == 0:
        h = 0.0
    elif mx == r:
        h = (60.0 * ((g - b) / diff) + 360.0) % 360.0
    elif mx == g:
        h = (60.0 * ((b - r) / diff) + 120.0) % 360.0
    else:
        h = (60.0 * ((r - g) / diff) + 240.0) % 360.0
    s = 0.0 if mx == 0 else diff / mx
    v = mx
    return h, s, v


def _dist_manhattan(a: tuple, b: tuple) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1]) + abs(a[2] - b[2])


# ── Color family classification ───────────────────────────────────────────────

def _is_neutral(rgb: tuple) -> bool:
    """True if the color is white / black / gray (achromatic)."""
    h, s, v = _rgb_to_hsv(rgb)
    return s < 0.10 or v < 0.20 or v > 0.95


def _rgb_to_family(rgb: tuple) -> str:
    """Map an RGB tuple to a color family name (English, lowercase)."""
    h, s, v = _rgb_to_hsv(rgb)

    if v < 0.18:
        return "black"

    if s < 0.12:
        if v > 0.92:
            return "white"
        if v > 0.70:
            return "silver"
        return "gray"

    # Beige — low-saturation warm
    if 15 <= h <= 70 and v > 0.75 and s < 0.45:
        return "beige"

    if 15 <= h <= 70:
        if v < 0.55:
            return "brown"
        if 35 <= h <= 65:
            return "yellow"
        return "orange"   # 15 <= h < 35

    if h < 15 or h >= 345:
        if v < 0.55:
            return "burgundy"
        if v > 0.75 and s < 0.75:
            return "pink"
        return "red"

    if 330 <= h < 345:
        return "pink" if v > 0.65 else "purple"

    if 290 <= h < 330:
        return "purple"

    if 70 < h <= 170:
        if 70 <= h <= 105 and s < 0.35 and v < 0.75:
            return "khaki"
        return "green"

    if 170 < h <= 200:
        return "turquoise"

    if 200 < h <= 250:
        return "blue"

    if 250 < h <= 290:
        return "navy"

    return "other"


# ── Multicolor detection ──────────────────────────────────────────────────────

def _is_multicolor(rgb_counts: list, min_share: float = 0.30, min_dist: int = 120) -> bool:
    """
    True only if:
      - 2nd color has >= min_share of total pixels
      - Manhattan distance between top-2 colors >= min_dist
    """
    if not rgb_counts or len(rgb_counts) < 2:
        return False
    total = sum(c for _, c in rgb_counts)
    if total <= 0:
        return False
    (c1, n1), (c2, n2) = rgb_counts[0], rgb_counts[1]
    if (n2 / total) < min_share:
        return False
    if _dist_manhattan(c1, c2) < min_dist:
        return False
    return True


# ── Core image analysis ───────────────────────────────────────────────────────

def _dominant_rgb_ignore_bg(
    img: Image.Image,
    resize_to: tuple = (220, 220),
    palette_size: int = 10,
    bg_dist: int = 40,
    crop_border: float = 0.06,
) -> tuple[tuple, list]:
    """
    Returns (dominant_rgb, rgb_counts) where rgb_counts = [((r,g,b), count), ...].

    Background is estimated from the 4 corner pixels, then removed.
    Very dark shadows (luminance < 35) are also excluded.
    If dominant is neutral (white/gray/black) and a chromatic color has >= 12%
    share, the chromatic color is used instead.
    """
    # Crop border noise
    w, h = img.size
    dx, dy = int(w * crop_border), int(h * crop_border)
    if dx > 0 and dy > 0 and w - 2 * dx > 10 and h - 2 * dy > 10:
        img = img.crop((dx, dy, w - dx, h - dy))

    img = img.resize(resize_to)
    px = img.load()
    W, H = img.size

    # Estimate background from corners
    corners = [px[0, 0], px[W - 1, 0], px[0, H - 1], px[W - 1, H - 1]]
    bg = tuple(sum(c[i] for c in corners) // 4 for i in range(3))

    # Quantize palette
    q = img.quantize(colors=palette_size, method=Image.Quantize.FASTOCTREE)
    palette = q.getpalette()
    qpix = list(q.getdata())

    # Mark pixels to keep (not background, not shadow)
    keep = []
    for y in range(H):
        for x in range(W):
            p = px[x, y]
            if _dist_manhattan(p, bg) <= bg_dist:
                keep.append(False)
                continue
            lum = 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]
            if lum < 35:
                keep.append(False)
                continue
            keep.append(True)

    from collections import Counter
    cnt: Counter = Counter()
    for i, idx in enumerate(qpix):
        if keep[i]:
            cnt[idx] += 1

    # Fallback: if nothing remains, exclude only near-pure-white
    if not cnt:
        for idx in qpix:
            r = palette[idx * 3]
            g = palette[idx * 3 + 1]
            b = palette[idx * 3 + 2]
            if not (r > 245 and g > 245 and b > 245):
                cnt[idx] += 1

    if not cnt:
        return (0, 0, 0), []

    # Build rgb_counts list
    rgb_counts = []
    for idx, c in cnt.most_common():
        r = palette[idx * 3]
        g = palette[idx * 3 + 1]
        b = palette[idx * 3 + 2]
        rgb_counts.append(((r, g, b), c))

    dominant = rgb_counts[0][0]

    # Neutral avoidance: if dominant is neutral but a chromatic color exists
    if _is_neutral(dominant):
        total = sum(c for _, c in rgb_counts)
        for rgb, c in rgb_counts[1:]:
            if total > 0 and (c / total) >= 0.12 and not _is_neutral(rgb):
                dominant = rgb
                break

    return dominant, rgb_counts


# ── Accepted value picker ─────────────────────────────────────────────────────

def _norm(s: str) -> str:
    import unicodedata, re
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


def pick_best_accepted_color(family: str, accepted_values: set | list) -> str:
    """
    Given a color family name and a set of marketplace-valid values,
    return the best matching accepted value.
    """
    if not accepted_values:
        return ""
    terms = _FAMILY_TERMS.get(family, [])
    if not terms:
        return ""
    scored = []
    for v in accepted_values:
        nv = _norm(v)
        score = sum(10 for t in terms if _norm(t) in nv)
        scored.append((score, v))
    scored.sort(key=lambda x: -x[0])
    if scored and scored[0][0] > 0:
        return scored[0][1]
    return ""


# ── Main entry ────────────────────────────────────────────────────────────────

def _is_white_product(img: Image.Image, white_threshold: int = 220, white_ratio: float = 0.72) -> bool:
    """
    True when the vast majority of the image is near-white,
    meaning the product itself is white (Photoroom / white background).
    We sample the image at low resolution for speed.
    """
    small = img.copy()
    small.thumbnail((80, 80))
    px = list(small.getdata())
    near_white = sum(1 for r, g, b in px if r > white_threshold and g > white_threshold and b > white_threshold)
    return (near_white / max(len(px), 1)) >= white_ratio


def analyze_colors(img: Image.Image) -> ColorResult:
    """
    Analyze a PIL image and return a ColorResult.
    """
    img = img.convert("RGB")

    # White product shortcut: if image is dominated by near-white, return Alb
    if _is_white_product(img):
        return ColorResult(
            dominant_color_raw="rgb(255, 255, 255)",
            dominant_color_normalized="Alb",
            confidence=0.85,
        )

    dominant, rgb_counts = _dominant_rgb_ignore_bg(img)

    if not rgb_counts:
        return ColorResult()

    # Check multicolor — but only if top-1 is NOT neutral
    top1 = rgb_counts[0][0]
    h1, s1, _ = _rgb_to_hsv(top1)
    multicolor = s1 >= 0.18 and _is_multicolor(rgb_counts)

    family = _rgb_to_family(dominant)
    if multicolor:
        family = "multicolor"

    normalized = _FAMILY_TO_NORMALIZED.get(family, "")

    # Confidence: share of dominant color in total
    total = sum(c for _, c in rgb_counts)
    dominant_count = next((c for rgb, c in rgb_counts if rgb == dominant), 0)
    confidence = round(min((dominant_count / total) * 1.15, 1.0), 3) if total else 0.0

    # Secondary color
    secondary_rgb = None
    secondary_family = ""
    for rgb, _ in rgb_counts[1:]:
        if rgb != dominant:
            secondary_rgb = rgb
            secondary_family = _FAMILY_TO_NORMALIZED.get(_rgb_to_family(rgb), "")
            break

    palette = [rgb for rgb, _ in rgb_counts[:5]]

    return ColorResult(
        dominant_color_raw        = f"rgb{dominant}",
        dominant_color_normalized = normalized,
        secondary_color_raw       = f"rgb{secondary_rgb}" if secondary_rgb else "",
        secondary_color_normalized= secondary_family,
        confidence                = confidence,
        is_multicolor             = multicolor,
        palette_rgb               = palette,
    )


def find_multicolor_value(valid_values: set) -> Optional[str]:
    """Return the marketplace-specific 'Multicolor' value if available."""
    for alias in _MULTICOLOR_ALIASES:
        if alias in valid_values:
            return alias
    return None
