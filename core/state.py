"""
Global state helpers for the Streamlit app.
Manages per-marketplace data objects and processing results.
"""
import os
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

def get_backend() -> str:
    """Return the configured storage backend.

    Reads REFERENCE_BACKEND environment variable.
    Valid values: 'duckdb' (default), 'parquet', 'dual'.
    'dual' writes to both DuckDB and Parquet, reads from DuckDB.
    """
    return os.environ.get("REFERENCE_BACKEND", "duckdb").lower()


PREDEFINED_MARKETPLACES = [
    "eMAG Romania",
    "Trendyol",
    "Allegro",
    "FashionDays",
]

# DEPRECATED: all marketplaces now use DuckDB when REFERENCE_BACKEND=duckdb (default).
# Kept for backward compatibility with any import that references this symbol.
DUCKDB_MARKETPLACES: set = set()

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

@st.cache_data(ttl=60)
def _cached_is_available(mp_id: str) -> bool:
    """Verificare rapidă (cached 60s) dacă un marketplace are date în DuckDB."""
    try:
        from core import reference_store_duckdb as _ddb
        return _ddb.is_available(mp_id)
    except Exception:
        return False


@st.cache_data(ttl=600)
def _cached_load_marketplace_data(mp_id: str):
    """Încarcă datele marketplace din DuckDB (cached 10 min).
    Returnează (cats, chars, vals) ca DataFrames.
    """
    from core import reference_store_duckdb as _ddb
    return _ddb.load_marketplace_data(mp_id)


def is_marketplace_available(name: str) -> bool:
    """Verificare rapidă (fără load complet) dacă un marketplace are date.

    Folosit în sidebar și selectbox-uri pentru a afișa status fără să încarce
    categorii/caracteristici/valori în memorie.
    """
    backend = get_backend()
    if backend in ("duckdb", "dual"):
        try:
            from core import reference_store_duckdb as _ddb
            mp_id = _ddb.marketplace_id_slug(name)
            return _cached_is_available(mp_id)
        except Exception:
            return False
    # Parquet: verificare rapidă pe disk
    folder = DATA_DIR / name.replace(" ", "_")
    return (folder / "categories.parquet").exists()


def load_marketplace_on_select(name: str) -> bool:
    """Încarcă datele complete ale unui marketplace la selecție.

    Dacă e deja în session_state, returnează imediat (fără re-load).
    Altfel, încarcă din DuckDB (cu cache) sau Parquet și stochează în session_state.
    """
    if st.session_state.get("marketplaces", {}).get(name) and \
       st.session_state["marketplaces"][name].is_loaded():
        return True

    backend = get_backend()
    loaded = False

    if backend in ("duckdb", "dual"):
        try:
            from core import reference_store_duckdb as _ddb
            mp_id = _ddb.marketplace_id_slug(name)
            if _cached_is_available(mp_id):
                cats, chars, vals = _cached_load_marketplace_data(mp_id)
                mp = MarketplaceData(name)
                mp.load_from_dataframes(cats, chars, vals)
                st.session_state["marketplaces"][name] = mp
                log.info("Lazy-loaded %s from DuckDB", name)
                loaded = True
        except Exception as exc:
            log.warning("DuckDB lazy load failed for %s: %s", name, exc)

    if not loaded and backend in ("parquet", "dual"):
        mp = MarketplaceData(name)
        folder = DATA_DIR / name.replace(" ", "_")
        if mp.load_from_disk(folder):
            st.session_state["marketplaces"][name] = mp
            log.info("Lazy-loaded %s from Parquet", name)
            loaded = True

    return loaded


def init_state():
    """Initialise session state keys. Nu încarcă date marketplace — lazy load via load_marketplace_on_select()."""
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

    # Marketplace data este încărcat lazy via load_marketplace_on_select()
    # când utilizatorul selectează un marketplace — nu la startup.


def get_marketplace(name: str) -> MarketplaceData | None:
    return st.session_state["marketplaces"].get(name)


def set_marketplace(name: str, mp: MarketplaceData):
    """Store a MarketplaceData in session state and persist to Parquet.

    .. deprecated::
        In REFERENCE_BACKEND=duckdb mode, persistence is handled by
        ``_do_save_unified`` in ``pages/setup.py`` via DuckDB import pipeline.
        This function still writes Parquet for REFERENCE_BACKEND=parquet|dual.
        Do not call this function when using the DuckDB backend.
    """
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


def clear_marketplace_data(name: str):
    """Șterge datele (categorii, caracteristici, valori) pentru un marketplace, fără a-l elimina din listă."""
    import shutil
    # Curăță session state
    st.session_state.get("marketplaces", {}).pop(name, None)
    # Șterge fișierele parquet de pe disk
    folder = DATA_DIR / name.replace(" ", "_")
    if folder.exists():
        shutil.rmtree(folder)
    # Always attempt DuckDB clear (no-op if marketplace doesn't exist in DB)
    try:
        from core import reference_store_duckdb as _ddb
        mp_id = _ddb.marketplace_id_slug(name)
        _ddb.clear_marketplace_data(mp_id)
    except Exception as exc:
        log.warning("Eroare la ștergerea DuckDB pentru %s: %s", name, exc)
    log.info("Date șterse pentru marketplace '%s'", name)


def remove_custom_marketplace(name: str) -> bool:
    """Elimină complet un marketplace custom (date + intrare din listă). Nu funcționează pe cele predefinite."""
    if name in PREDEFINED_MARKETPLACES:
        return False
    clear_marketplace_data(name)
    names = st.session_state.get("custom_mp_names", [])
    if name in names:
        names.remove(name)
        st.session_state["custom_mp_names"] = names
        save_custom_marketplaces(names)
    log.info("Marketplace '%s' eliminat complet", name)
    return True
