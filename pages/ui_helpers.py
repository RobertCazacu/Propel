"""Shared UI helpers, global CSS, and styled components for Propel."""
import streamlit as st

GLOBAL_CSS = """
<style>
:root {
    --primary: #6366f1;
    --success: #22c55e;
    --warning: #f59e0b;
    --danger:  #ef4444;
    --surface: #1e1e2e;
    --muted:   #6b7280;
}

/* ── Sidebar ──────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: #080810 !important;
    border-right: 1px solid #1a1a2e;
}
[data-testid="stSidebar"] .stRadio > div { gap: 2px !important; }
[data-testid="stSidebar"] .stRadio label {
    padding: 7px 12px !important;
    border-radius: 7px;
    font-size: 13px;
    color: #94a3b8 !important;
    transition: background 0.15s, color 0.15s;
    cursor: pointer;
}
[data-testid="stSidebar"] .stRadio label:hover {
    background: #1a1a2e !important;
    color: #e2e8f0 !important;
}

/* ── Global base ─────────────────────────────────────────────────────── */
.main .block-container { padding-top: 1.5rem; }

/* ── Propel hero header ──────────────────────────────────────────────── */
.propel-hero {
    padding: 0.5rem 0 1.2rem 0;
    border-bottom: 1px solid #1e1e3a;
    margin-bottom: 1.5rem;
}
.propel-hero h1 {
    font-size: 1.9rem;
    font-weight: 800;
    margin: 0 0 4px 0;
    background: linear-gradient(135deg, #a5b4fc 0%, #6366f1 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.propel-hero p {
    color: #6b7280;
    font-size: 0.88rem;
    margin: 0;
}

/* ── Section header (left-border accent) ────────────────────────────── */
.section-header {
    border-left: 4px solid #6366f1;
    padding-left: 12px;
    margin: 1.5rem 0 1rem 0;
}
.section-header h3 { margin: 0; color: #f1f5f9; font-size: 1.05rem; font-weight: 700; }
.section-header p  { margin: 2px 0 0 0; color: #6b7280; font-size: 0.8rem; }

/* ── KPI row ─────────────────────────────────────────────────────────── */
.kpi-row { display: flex; gap: 12px; margin-bottom: 1.5rem; flex-wrap: wrap; }
.kpi-card {
    flex: 1;
    min-width: 130px;
    background: #1e1e2e;
    border: 1px solid #2d2d3d;
    border-radius: 12px;
    padding: 1.1rem 1rem;
    text-align: center;
    transition: border-color 0.2s;
}
.kpi-card:hover { border-color: #6366f1; }
.kpi-value { font-size: 1.9rem; font-weight: 800; color: #f1f5f9; line-height: 1; }
.kpi-label {
    font-size: 10px;
    color: #6b7280;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-top: 5px;
}
.kpi-delta { font-size: 11px; color: #22c55e; margin-top: 4px; font-weight: 600; }

/* ── Badges ──────────────────────────────────────────────────────────── */
.badge {
    display: inline-block;
    padding: 2px 9px;
    border-radius: 999px;
    font-size: 11px;
    font-weight: 600;
    letter-spacing: 0.2px;
}
.badge-success { background: #16a34a22; color: #22c55e; border: 1px solid #22c55e44; }
.badge-warning { background: #ca8a0422; color: #f59e0b; border: 1px solid #f59e0b44; }
.badge-danger  { background: #dc262622; color: #ef4444; border: 1px solid #ef444444; }
.badge-info    { background: #2563eb22; color: #60a5fa; border: 1px solid #3b82f644; }
.badge-gray    { background: #1e1e2e;   color: #6b7280; border: 1px solid #2d2d3d; }
.badge-primary { background: #312e8122; color: #a5b4fc; border: 1px solid #6366f144; }

/* ── Marketplace status cards ────────────────────────────────────────── */
.mp-status-card {
    background: #1e1e2e;
    border: 1px solid #2d2d3d;
    border-radius: 10px;
    padding: 14px 16px;
    margin-bottom: 10px;
    transition: border-color 0.2s;
}
.mp-status-card.active  { border-color: #22c55e44; }
.mp-status-card.inactive{ border-color: #374151; }
.mp-status-card .mp-name { font-weight: 700; font-size: 14px; color: #f1f5f9; }
.mp-status-card .mp-meta { color: #6b7280; font-size: 11px; margin-top: 5px; }

/* ── How-it-works cards ──────────────────────────────────────────────── */
.how-card {
    background: #1e1e2e;
    border: 1px solid #2d2d3d;
    border-radius: 10px;
    padding: 1.2rem;
    height: 100%;
}
.how-card .step-num {
    font-size: 1.5rem;
    margin-bottom: 6px;
}
.how-card h4 { margin: 0 0 6px 0; color: #f1f5f9; font-size: 0.95rem; }
.how-card p  { margin: 0; color: #6b7280; font-size: 0.82rem; line-height: 1.5; }

/* ── Sidebar logo & session stats ────────────────────────────────────── */
.sidebar-logo { padding: 0.8rem 0 0.6rem 0; border-bottom: 1px solid #1a1a2e; }
.sidebar-logo .app-name {
    font-size: 1.25rem;
    font-weight: 800;
    background: linear-gradient(135deg, #a5b4fc, #6366f1);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
}
.sidebar-logo .app-sub { font-size: 10px; color: #374151; margin-top: 1px; }

.session-stats {
    background: #0a0a14;
    border: 1px solid #1a1a2e;
    border-radius: 8px;
    padding: 10px 12px;
    margin-top: 0.8rem;
}
.session-stats .stat-title {
    font-size: 9px;
    color: #374151;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: 6px;
    font-weight: 700;
}
.session-stats .stat-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: 11px;
    margin-bottom: 3px;
}
.session-stats .stat-label { color: #4b5563; }
.session-stats .stat-val   { color: #a5b4fc; font-weight: 600; }

/* ── Cards / containers ──────────────────────────────────────────────── */
.propel-card {
    background: #1e1e2e;
    border: 1px solid #2d2d3d;
    border-radius: 12px;
    padding: 1.5rem;
    margin-bottom: 1rem;
}

/* ── Status bar (info strip) ─────────────────────────────────────────── */
.status-bar {
    border-radius: 8px;
    padding: 10px 16px;
    margin-bottom: 1rem;
    display: flex;
    align-items: center;
    gap: 10px;
}
.status-bar.active   { background: #0a1f12; border: 1px solid #22c55e44; }
.status-bar.inactive { background: #1a0a0a; border: 1px solid #ef444444; }
.status-bar.info     { background: #0f1830; border: 1px solid #3b82f644; }

/* ── Setup page — marketplace status card ───────────────────────────── */
.setup-status-card {
    background: #12201a;
    border: 1px solid #22c55e33;
    border-radius: 12px;
    padding: 1.2rem 1.4rem;
    margin-bottom: 1rem;
}
.setup-empty-card {
    background: #1a1a2e;
    border: 2px dashed #2d2d4e;
    border-radius: 12px;
    padding: 2rem;
    text-align: center;
    margin-bottom: 1rem;
}
.setup-stats { display: flex; gap: 2rem; margin-top: 10px; }
.setup-stat  { display: flex; flex-direction: column; }
.setup-stat-val { font-size: 1.4rem; font-weight: 800; color: #f1f5f9; }
.setup-stat-lbl { font-size: 10px; color: #6b7280; text-transform: uppercase; letter-spacing: 0.4px; margin-top: 2px; }

/* ── Setup page — file slot headers ─────────────────────────────────── */
.file-slot-header {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 0 4px 0;
    border-bottom: 1px solid #2d2d3d;
    margin-bottom: 8px;
}
.file-slot-icon  { font-size: 1.1rem; }
.file-slot-title { font-size: 0.9rem; font-weight: 700; color: #f1f5f9; }

/* ── Upload progress bar ─────────────────────────────────────────────── */
.upload-progress { margin: 8px 0 12px 0; }
.upload-bar {
    height: 4px;
    background: #2d2d3d;
    border-radius: 999px;
    margin-top: 5px;
    overflow: hidden;
}
.upload-bar-fill {
    height: 100%;
    background: linear-gradient(90deg, #6366f1, #a5b4fc);
    border-radius: 999px;
    transition: width 0.3s ease;
}
</style>
"""


def inject_css() -> None:
    """Inject global Propel CSS. Call once in app.py."""
    st.markdown(GLOBAL_CSS, unsafe_allow_html=True)


def section_header(title: str, subtitle: str = "", color: str = "#6366f1") -> None:
    """Render a section header with a colored left border."""
    sub_html = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f'<div class="section-header" style="border-left-color:{color}">'
        f"<h3>{title}</h3>{sub_html}</div>",
        unsafe_allow_html=True,
    )


def hero_header(title: str, subtitle: str = "") -> None:
    """Render the page hero header (gradient title + subtitle)."""
    sub_html = f"<p>{subtitle}</p>" if subtitle else ""
    st.markdown(
        f'<div class="propel-hero"><h1>{title}</h1>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def kpi_row(items: list) -> None:
    """
    Render a horizontal row of KPI cards.

    items: list of dicts — required keys: ``value``, ``label``
           optional: ``delta`` (int/float), ``color`` (hex)
    """
    cards_html = ""
    for item in items:
        color = item.get("color", "#f1f5f9")
        delta_html = ""
        if item.get("delta") is not None:
            sign = "+" if float(item["delta"]) > 0 else ""
            delta_html = f'<div class="kpi-delta">{sign}{item["delta"]}</div>'
        cards_html += (
            f'<div class="kpi-card">'
            f'<div class="kpi-value" style="color:{color}">{item["value"]}</div>'
            f'<div class="kpi-label">{item["label"]}</div>'
            f"{delta_html}</div>"
        )
    st.markdown(f'<div class="kpi-row">{cards_html}</div>', unsafe_allow_html=True)


def badge_html(text: str, kind: str = "info") -> str:
    """Return an inline badge HTML string. kind: success|warning|danger|info|gray|primary"""
    return f'<span class="badge badge-{kind}">{text}</span>'
