"""
Marketplace Offer Processor
Multi-marketplace tool for automatic characteristic completion.
"""
import logging
import streamlit as st
import sys
from pathlib import Path

log = logging.getLogger("marketplace.app")

sys.path.insert(0, str(Path(__file__).parent))

from core.state import init_state, all_marketplace_names, get_marketplace, is_marketplace_available

st.set_page_config(
    page_title="Propel — Marketplace Processor",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

from pages.ui_helpers import inject_css
inject_css()

init_state()

# Asigura schema DuckDB la zi (migrari idempotente)
try:
    from core.reference_store_duckdb import ensure_schema
    ensure_schema()
except Exception as _schema_exc:
    log.error("ensure_schema() failed: %s", _schema_exc, exc_info=True)  # P24: nu mai înghite eroarea în tăcere

# Sterge log-urile mai vechi de 7 zile
from core.logger import cleanup_old_logs
cleanup_old_logs()

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    # Logo / name
    st.markdown(
        '<div class="sidebar-logo">'
        '<div class="app-name">⚡ Propel</div>'
        '<div class="app-sub">Marketplace Offer Processor · v1.0</div>'
        '</div>',
        unsafe_allow_html=True,
    )

    mp_names = all_marketplace_names()
    loaded   = [n for n in mp_names if is_marketplace_available(n)]

    # Marketplace status indicators
    st.markdown(
        '<div style="font-size:9px;color:#374151;text-transform:uppercase;'
        'letter-spacing:0.5px;font-weight:700;padding:10px 0 4px 0">'
        'Marketplace-uri</div>',
        unsafe_allow_html=True,
    )
    for name in mp_names:
        mp = get_marketplace(name)
        available = (mp and mp.is_loaded()) or is_marketplace_available(name)
        dot   = '<span style="color:#22c55e;font-size:9px">●</span>' if available \
                else '<span style="color:#374151;font-size:9px">●</span>'
        label = f'<span style="font-size:12px;color:{"#f1f5f9" if available else "#4b5563"}">{name}</span>'
        st.markdown(f'{dot} {label}', unsafe_allow_html=True)

    st.markdown('<div style="border-top:1px solid #1a1a2e;margin:10px 0 6px 0"></div>', unsafe_allow_html=True)

    page = st.radio(
        "Navigare",
        ["🏠 Dashboard", "⚙️ Setup Marketplace", "📁 Process Offers", "📊 Results", "🔍 Diagnostic", "🤖 LLM Providers"],
        label_visibility="collapsed",
    )

    # Session stats widget
    _sess_results = st.session_state.get("process_results", [])
    _sess_chars   = sum(len(r.get("new_chars", {})) for r in _sess_results)
    st.markdown(
        f'<div class="session-stats">'
        f'<div class="stat-title">Sesiunea curentă</div>'
        f'<div class="stat-row"><span class="stat-label">Produse procesate</span>'
        f'<span class="stat-val">{len(_sess_results)}</span></div>'
        f'<div class="stat-row"><span class="stat-label">Caracteristici adăugate</span>'
        f'<span class="stat-val">{_sess_chars}</span></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

# ── Pages ──────────────────────────────────────────────────────────────────────
if page == "🏠 Dashboard":
    from pages.dashboard import render
    render()
elif page == "⚙️ Setup Marketplace":
    from pages.setup import render
    render()
elif page == "📁 Process Offers":
    from pages.process import render
    render()
elif page == "📊 Results":
    from pages.results import render
    render()
elif page == "🔍 Diagnostic":
    from pages.diagnostic import render
    render()
elif page == "🤖 LLM Providers":
    from pages.llm_providers import render
    render()
