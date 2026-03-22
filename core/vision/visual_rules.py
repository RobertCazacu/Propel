"""
Rules engine for visual attribute processing.

Rules are stored in data/visual_rules.json and auto-created on first run.
Category-level rules override the global default.

Structure:
{
  "default": { ... global settings ... },
  "categories": {
    "Tricouri sport barbati": { "min_color_confidence": 0.70 },
    ...
  }
}
"""
import json
from pathlib import Path
from core.app_logger import get_logger

log = get_logger("marketplace.vision.rules")

RULES_FILE = Path(__file__).parent.parent.parent / "data" / "visual_rules.json"

# ── Default configuration ─────────────────────────────────────────────────────

DEFAULT_RULES: dict = {
    "default": {
        # Which characteristic names are treated as "color" fields
        "color_mandatory_chars": [
            "Culoare de baza",
            "Culoare:",
            "Szín:",
            "Цвят:",
        ],

        # Minimum color_confidence to auto-fill a color attribute
        "min_color_confidence": 0.60,

        # If text already resolved the color, skip image color (no override)
        "prefer_text_color_over_image": True,

        # If image color contradicts text color, mark needs_review instead of auto-fill
        "fallback_to_review_if_conflict": True,

        # Minimum confidence to call is_multicolor True and return "Multicolor"
        "multicolor_threshold": 0.80,

        # Minimum confidence for product_type_hint to be reported
        "min_product_confidence": 0.65,
    },
    "categories": {
        # Per-category overrides — example (uncomment to activate):
        # "Tricouri sport barbati": {
        #     "min_color_confidence": 0.70,
        #     "prefer_text_color_over_image": False,
        # },
    },
}


# ── Persistence ───────────────────────────────────────────────────────────────

def load_rules() -> dict:
    if RULES_FILE.exists():
        try:
            data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
            # Ensure defaults are present even for old files
            merged_default = {**DEFAULT_RULES["default"], **data.get("default", {})}
            return {"default": merged_default, "categories": data.get("categories", {})}
        except Exception as e:
            log.error("Failed to load visual_rules.json: %s — using defaults", e)
    return DEFAULT_RULES.copy()


def save_rules(rules: dict) -> bool:
    try:
        RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
        RULES_FILE.write_text(json.dumps(rules, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        log.error("Failed to save visual_rules.json: %s", e)
        return False


def ensure_rules_file():
    """Create rules file with defaults if it doesn't exist."""
    if not RULES_FILE.exists():
        save_rules(DEFAULT_RULES)
        log.info("Created default visual_rules.json")


# ── Rule resolution ───────────────────────────────────────────────────────────

def get_category_rules(category_name: str, rules: dict = None) -> dict:
    """
    Return the effective rules for a given category.
    Category-level keys override the global default.
    """
    if rules is None:
        rules = load_rules()
    defaults   = {**DEFAULT_RULES["default"], **rules.get("default", {})}
    overrides  = rules.get("categories", {}).get(category_name, {})
    return {**defaults, **overrides}
