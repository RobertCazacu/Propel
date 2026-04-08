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
    page_title="Marketplace Processor",
    page_icon="⚡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Custom CSS ─────────────────────────────────────────────────────────────────
st.markdown("""
<style>
    /* Sidebar */
    [data-testid="stSidebar"] { background: #0f1117; }
    [data-testid="stSidebar"] .stSelectbox label { color: #94a3b8 !important; font-size: 12px; }

    /* Cards */
    .mp-card {
        background: #1e2130; border: 1px solid #2d3250;
        border-radius: 8px; padding: 16px; margin-bottom: 12px;
    }
    .mp-card-title { font-weight: 700; font-size: 15px; margin-bottom: 4px; }
    .mp-card-sub { color: #64748b; font-size: 12px; }

    /* Status badges */
    .badge { display:inline-block; padding:2px 10px; border-radius:12px; font-size:12px; font-weight:600; }
    .badge-green  { background:#16a34a22; color:#22c55e; }
    .badge-yellow { background:#ca8a0422; color:#eab308; }
    .badge-red    { background:#dc262622; color:#ef4444; }
    .badge-blue   { background:#2563eb22; color:#3b82f6; }
    .badge-gray   { background:#33333344; color:#94a3b8; }

    /* Metric row */
    .metric-row { display:flex; gap:16px; margin-bottom:20px; }
    .metric-box {
        flex:1; background:#1e2130; border:1px solid #2d3250;
        border-radius:8px; padding:16px; text-align:center;
    }
    .metric-val { font-size:28px; font-weight:800; }
    .metric-lbl { font-size:11px; color:#64748b; text-transform:uppercase; margin-top:4px; }
</style>
""", unsafe_allow_html=True)

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
    st.markdown("## ⚡ Marketplace\nProcessor")
    st.markdown("---")

    mp_names = all_marketplace_names()
    loaded   = [n for n in mp_names if is_marketplace_available(n)]

    # Show marketplace status
    for name in mp_names:
        mp = get_marketplace(name)
        available = (mp and mp.is_loaded()) or is_marketplace_available(name)
        badge = '<span class="badge badge-green">✓ Loaded</span>' if available else '<span class="badge badge-gray">○ Empty</span>'
        st.markdown(f"**{name}** {badge}", unsafe_allow_html=True)

    st.markdown("---")
    st.markdown("**Navigation**")
    page = st.radio(
        "Go to",
        ["🏠 Dashboard", "⚙️ Setup Marketplace", "📁 Process Offers", "📊 Results", "🔍 Diagnostic", "🤖 LLM Providers"],
        label_visibility="collapsed",
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
