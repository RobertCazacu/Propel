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
            "Color",
            "Case Color",
            "Strap Color",
            "Web Color",
            "Dial Color",
            "Band Color",
            "Lens Color",
            "Kolor",
            "Farba",
            "Barva",
            "Colore",
            "Couleur",
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

        # ── YOLO settings ────────────────────────────────────────────────────
        # Minimum YOLO detection confidence to accept a detection
        "min_yolo_confidence": 0.50,

        # Only accept YOLO detections whose label is in this list.
        # Empty list = no filtering (accept all COCO labels).
        # Default excludes sport-equipment labels (surfboard, skateboard…)
        # that frequently fire as false positives on clothing/footwear images.
        "yolo_label_allowlist": [
            "person", "backpack", "handbag", "suitcase", "tie", "umbrella",
            "chair", "couch", "bed", "dining table", "potted plant",
            "clock", "vase", "teddy bear",
            "cell phone", "laptop", "keyboard", "tv", "remote",
            "bottle", "cup", "bowl", "book", "scissors",
            "toothbrush", "hair drier",
        ],

        # ── CLIP settings ────────────────────────────────────────────────────
        # Minimum CLIP score to trust a label as valid
        "min_clip_confidence": 0.25,

        # ── Fusion settings ──────────────────────────────────────────────────
        # Prefer text over image when both are available
        "prefer_text_over_image": True,

        # How to resolve text vs image conflict:
        # "review"       → mark needs_review, pick higher confidence (default)
        # "prefer_text"  → always use text
        # "prefer_image" → always use image
        "conflict_policy": "review",

        # Text confidence below this threshold: run image analysis for category
        "min_text_conf_for_image": 0.70,

        # ── Image strategy ───────────────────────────────────────────────────
        # "first_only"       → analyze only the first image URL
        # "best_confidence"  → analyze up to max_images_per_product, keep highest YOLO conf
        # "aggregate_vote"   → analyze multiple, majority-vote on attributes
        "image_strategy": "first_only",

        # Maximum number of images to analyze per product
        "max_images_per_product": 1,

        # If True: image suggestions are only advisory — never auto-fill
        "suggestion_only": False,

        # If True: automatically enable color detection when mandatory color char is missing
        "auto_enable_color_if_mandatory": True,

        # ── Attribute fusion policy ──────────────────────────────────────────
        # Per-attribute rules for text vs vision fusion.
        # vision_eligible: False = skip vision entirely for this attribute
        # override_text_if_filled: ALWAYS False (conservative default)
        # min_vision_confidence: external threshold (NOT model-reported confidence)
        #   Primary gate = data.find_valid(). This is a soft pre-filter only.
        # conflict_action: "prefer_text" | "review"
        # allowed_sources: which vision extraction methods can fill this attr
        "attribute_fusion_policy": {
            # ── Color fields (all marketplaces) ──────────────────────────────
            "Culoare de baza": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Culoare:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Szín:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Цвят:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Color": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Case Color": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Strap Color": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Web Color": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Dial Color": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Band Color": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Lens Color": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Kolor": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Farba": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Barva": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Colore": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            "Couleur": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.60,
                "conflict_action": "prefer_text",
                "allowed_sources": ["color_algorithm"],
            },
            # ── Visual attributes (cloud vision only) ─────────────────────
            "Imprimeu:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.70,
                "conflict_action": "review",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Lungime maneca:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.65,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Tip produs:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.75,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Stil:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.70,
                "conflict_action": "review",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Sistem inchidere:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.75,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Tip inchidere:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.75,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            "Pentru:": {
                "vision_eligible": True,
                "override_text_if_filled": False,
                "min_vision_confidence": 0.80,
                "conflict_action": "prefer_text",
                "allowed_sources": ["vision_llm_cloud"],
            },
            # ── NON-visual (never use vision for these) ───────────────────
            "Material:": {
                "vision_eligible": False,
                "reason": "Material cannot be reliably determined visually",
            },
            "Anyag:": {"vision_eligible": False, "reason": "same — HU"},
            "Материал:": {"vision_eligible": False, "reason": "same — BG"},
            "Sezon:": {
                "vision_eligible": False,
                "reason": "Season cannot be reliably determined visually",
            },
            "Marime:": {"vision_eligible": False, "reason": "Size not visual"},
            "Méret:": {"vision_eligible": False, "reason": "Size not visual — HU"},
            "Размер:": {"vision_eligible": False, "reason": "Size not visual — BG"},
            "Sport:": {"vision_eligible": False, "reason": "Sport context is in text, not image"},
            "Varsta:": {"vision_eligible": False, "reason": "Age not visual"},
            "Instructiuni ingrijire:": {"vision_eligible": False, "reason": "Not visual"},
        },
    },
    "categories": {
        # Per-category overrides — example (uncomment to activate):
        # "Tricouri sport barbati": {
        #     "min_color_confidence": 0.70,
        #     "prefer_text_color_over_image": False,
        # },
        "Smartwatches": {
            "min_color_confidence": 0.40,
        },
        "Watches": {
            "min_color_confidence": 0.40,
        },
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


def get_attr_fusion_policy(attr_name: str, rules: dict = None) -> dict:
    """
    Return the fusion policy for a specific attribute name.
    Falls back to {"vision_eligible": False} if not found.

    Uses exact match first, then case-insensitive. This avoids fragile
    substring matching that would break for multilingual attr names (HU/BG).
    """
    if rules is None:
        rules = load_rules()
    policy_table = rules.get("default", {}).get("attribute_fusion_policy", {})

    # Exact match
    if attr_name in policy_table:
        return policy_table[attr_name]

    # Case-insensitive fallback
    normalized = attr_name.strip().casefold()
    for key, val in policy_table.items():
        if key.strip().casefold() == normalized:
            return val

    return {"vision_eligible": False, "reason": "not in policy table"}


def is_vision_eligible(attr_name: str, rules: dict = None) -> bool:
    """Quick check: can vision fill this attribute?"""
    return get_attr_fusion_policy(attr_name, rules).get("vision_eligible", False)
