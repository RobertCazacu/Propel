"""
Global state helpers for the Streamlit app.
Manages per-marketplace data objects and processing results.
"""
import streamlit as st
import json
from pathlib import Path
from core.loader import MarketplaceData
from core.app_logger import get_logger

log = get_logger("marketplace.state")

DATA_DIR = Path(__file__).parent.parent / "data"
STATS_FILE = DATA_DIR / "dashboard_stats.json"


def load_dashboard_stats() -> dict:
    """Incarca statisticile cumulative salvate pe disk."""
    if STATS_FILE.exists():
        try:
            return json.loads(STATS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"total_processed": 0, "total_chars_added": 0, "total_completed": 0,
            "total_cats_fixed": 0, "runs": 0, "last_run": None}


def save_dashboard_stats(results: list):
    """Actualizeaza si salveaza statisticile cumulative dupa o procesare."""
    stats = load_dashboard_stats()
    n_processed  = len(results)
    n_chars      = sum(len(r.get("new_chars", {})) for r in results)
    n_completed  = sum(1 for r in results if r.get("new_chars") and not r.get("needs_manual"))
    n_cats_fixed = sum(1 for r in results if r.get("action") in ("cat_assigned", "cat_corrected"))

    stats["total_processed"]  += n_processed
    stats["total_chars_added"] += n_chars
    stats["total_completed"]  += n_completed
    stats["total_cats_fixed"] += n_cats_fixed
    stats["runs"]             += 1

    from datetime import datetime
    stats["last_run"] = datetime.now().strftime("%d.%m.%Y %H:%M")

    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        STATS_FILE.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

PREDEFINED_MARKETPLACES = [
    "eMAG Romania",
    "Trendyol",
    "Allegro",
    "FashionDays",
]

# Marketplace-uri care folosesc DuckDB ca backend de stocare (pilot controlat)
DUCKDB_MARKETPLACES = {"eMAG HU"}

# ── Error code configuration per marketplace ───────────────────────────────────
# Each marketplace defines which error codes should be processed.
# The system attempts all fixes (category + characteristics) for any product with these codes.
DEFAULT_ERROR_CODES = {
    "eMAG Romania": {"processable_codes": ["1007", "1009", "1010"]},
    "Trendyol":     {"processable_codes": ["3111"]},
    "Allegro":      {"processable_codes": []},
    "FashionDays":  {"processable_codes": []},
}

ERROR_CODES_FILE    = DATA_DIR / "error_codes_config.json"
CUSTOM_MP_FILE      = DATA_DIR / "custom_marketplaces.json"


def load_custom_marketplaces() -> list:
    if CUSTOM_MP_FILE.exists():
        try:
            return json.loads(CUSTOM_MP_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def save_custom_marketplaces(names: list):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CUSTOM_MP_FILE.write_text(json.dumps(names, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_error_codes_config() -> dict:
    if ERROR_CODES_FILE.exists():
        try:
            return json.loads(ERROR_CODES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def save_error_codes_config(config: dict):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        ERROR_CODES_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def get_error_codes(marketplace: str) -> dict:
    """Returns error code config for a marketplace. Merges saved config on top of defaults."""
    saved = st.session_state.get("error_codes_config", {})
    defaults = DEFAULT_ERROR_CODES.get(marketplace, {"processable_codes": []})
    mp_saved = saved.get(marketplace, {})
    return {
        "processable_codes": mp_saved.get("processable_codes", defaults["processable_codes"]),
    }


def set_error_codes(marketplace: str, config: dict):
    """Save error code config for a marketplace (session + disk)."""
    all_config = st.session_state.get("error_codes_config", {})
    all_config[marketplace] = config
    st.session_state["error_codes_config"] = all_config
    save_error_codes_config(all_config)


def get_all_processable_codes(marketplace: str) -> set:
    """Returns the set of error codes that should be processed for this marketplace."""
    return set(get_error_codes(marketplace)["processable_codes"])

def init_state():
    """Initialise all session state keys."""
    defaults = {
        "marketplaces":       {},   # name -> MarketplaceData
        "active_mp":          None,
        "offers_products":    [],
        "offers_pairs":       [],
        "process_results":    [],
        "offers_file_buf":    None,
        "custom_mp_names":    [],
        "error_codes_config": {},   # name -> {category_errors, chars_missing, chars_invalid}
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

    # Load persisted custom marketplaces
    if not st.session_state["custom_mp_names"]:
        st.session_state["custom_mp_names"] = load_custom_marketplaces()

    # Load persisted error codes config
    if not st.session_state["error_codes_config"]:
        st.session_state["error_codes_config"] = load_error_codes_config()

    # Auto-load any previously saved marketplace data
    for mp_name in PREDEFINED_MARKETPLACES + st.session_state.get("custom_mp_names", []):
        if mp_name not in st.session_state["marketplaces"]:
            if mp_name in DUCKDB_MARKETPLACES:
                # eMAG HU pilot: load din DuckDB în loc de Parquet
                try:
                    from core import reference_store_duckdb as _duckdb_store
                    if _duckdb_store.is_available(_duckdb_store.EMAG_HU_ID):
                        cats, chars, vals = _duckdb_store.load_marketplace_data(
                            _duckdb_store.EMAG_HU_ID
                        )
                        mp = MarketplaceData(mp_name)
                        mp.load_from_dataframes(cats, chars, vals)
                        st.session_state["marketplaces"][mp_name] = mp
                        log.info("Loaded %s from DuckDB", mp_name)
                except Exception as exc:
                    log.warning("DuckDB load failed for %s: %s", mp_name, exc)
                continue  # nu face load_from_disk parquet pentru eMAG HU
            mp = MarketplaceData(mp_name)
            folder = DATA_DIR / mp_name.replace(" ", "_")
            if mp.load_from_disk(folder):
                st.session_state["marketplaces"][mp_name] = mp


def get_marketplace(name: str) -> MarketplaceData | None:
    return st.session_state["marketplaces"].get(name)


def set_marketplace(name: str, mp: MarketplaceData):
    st.session_state["marketplaces"][name] = mp
    # Persist to disk
    folder = DATA_DIR / name.replace(" ", "_")
    mp.save_to_disk(folder)


def all_marketplace_names() -> list[str]:
    return PREDEFINED_MARKETPLACES + st.session_state.get("custom_mp_names", [])


def add_custom_marketplace(name: str):
    names = st.session_state.get("custom_mp_names", [])
    if name not in names and name not in PREDEFINED_MARKETPLACES:
        names.append(name)
        st.session_state["custom_mp_names"] = names
        save_custom_marketplaces(names)
