import streamlit as st
from core.state import all_marketplace_names, get_marketplace, load_dashboard_stats, is_marketplace_available
from core.reference_store_duckdb import DB_PATH, marketplace_id_slug
from pages.ui_helpers import hero_header, section_header, kpi_row, badge_html
import duckdb


def render():
    hero_header(
        "Marketplace Offer Processor",
        "Completare automată a caracteristicilor produselor pentru multiple marketplace-uri.",
    )

    mp_names = all_marketplace_names()
    loaded   = [n for n in mp_names if is_marketplace_available(n)]

    # ── Metrici cumulative (persistente pe disk) ───────────────────────────────
    stats        = load_dashboard_stats()
    session_res  = st.session_state.get("process_results", [])

    # Sesiunea curenta (daca exista) o afisam separat ca delta
    session_chars     = sum(len(r.get("new_chars", {})) for r in session_res)
    session_completed = sum(1 for r in session_res if r.get("new_chars") and not r.get("needs_manual"))

    # Product knowledge count din DuckDB
    _pk_count = 0
    try:
        if DB_PATH.exists():
            with duckdb.connect(str(DB_PATH), read_only=True) as _con:
                _pk_count = _con.execute("SELECT COUNT(*) FROM product_knowledge").fetchone()[0]
    except Exception:
        pass

    kpi_row([
        {"value": f"{len(loaded)}/{len(mp_names)}", "label": "Marketplace configurate", "color": "#6366f1"},
        {"value": stats["runs"],                    "label": "Rulări totale"},
        {"value": f"{stats['total_processed']:,}",  "label": "Produse procesate",
         "delta": len(session_res) if session_res else None},
        {"value": f"{stats['total_chars_added']:,}", "label": "Caracteristici adăugate",
         "delta": session_chars if session_res else None, "color": "#22c55e"},
        {"value": f"{stats['total_cats_fixed']:,}",  "label": "Categorii fixate"},
        {"value": f"{_pk_count:,}",                  "label": "Product Knowledge (DB)", "color": "#a5b4fc"},
    ])

    if stats["last_run"]:
        st.caption(f"Ultima rulare: **{stats['last_run']}**")

    # ── Marketplace cards ──────────────────────────────────────────────────────
    section_header("Status marketplace-uri", "Starea curentă a configurației per marketplace")

    # Pre-fetch stats din DuckDB pentru toate marketplace-urile configurate
    def _mp_db_stats(mp_id: str) -> dict | None:
        if not DB_PATH.exists():
            return None
        try:
            with duckdb.connect(str(DB_PATH), read_only=True) as con:
                cats  = con.execute("SELECT COUNT(*) FROM categories WHERE marketplace_id=?", [mp_id]).fetchone()[0]
                chars = con.execute("SELECT COUNT(DISTINCT name) FROM characteristics WHERE marketplace_id=?", [mp_id]).fetchone()[0]
                vals  = con.execute("SELECT COUNT(*) FROM characteristic_values WHERE marketplace_id=?", [mp_id]).fetchone()[0]
                return {"categories": cats, "characteristics": chars, "values": vals}
        except Exception:
            return None

    cols = st.columns(3)
    for i, name in enumerate(mp_names):
        mp_id = marketplace_id_slug(name)
        available = is_marketplace_available(name)
        with cols[i % 3]:
            if available:
                db_stats = _mp_db_stats(mp_id)
                meta_html = ""
                if db_stats:
                    meta_html = (
                        f'<div class="mp-meta">'
                        f'📂 {db_stats["categories"]} categorii &nbsp;·&nbsp; '
                        f'🏷 {db_stats["characteristics"]} caracteristici &nbsp;·&nbsp; '
                        f'📋 {db_stats["values"]:,} valori</div>'
                    )
                st.markdown(
                    f'<div class="mp-status-card active">'
                    f'<div style="display:flex;align-items:center;justify-content:space-between">'
                    f'<span class="mp-name">{name}</span>'
                    f'{badge_html("Active", "success")}</div>'
                    f'{meta_html}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f'<div class="mp-status-card inactive">'
                    f'<div style="display:flex;align-items:center;justify-content:space-between">'
                    f'<span class="mp-name">{name}</span>'
                    f'{badge_html("Neconfigurat", "gray")}</div>'
                    f'<div class="mp-meta">Mergi la ⚙️ Setup pentru a configura.</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )

    # ── Istoric rulari (log-uri) ───────────────────────────────────────────────
    section_header("📋 Istoric procesări", "Ultimele rulări din ultimele 7 zile")

    from core.logger import list_logs, read_log

    logs = list_logs()
    if not logs:
        st.info("Nicio procesare inregistrata inca. Log-urile apar dupa prima rulare.")
    else:
        for log_meta in logs:
            s = log_meta["sumar"]
            ai_badge = "🤖 AI activ" if log_meta["ai_activat"] else "📐 Reguli"
            header = (
                f"**{log_meta['timestamp']}** · {log_meta['marketplace']} · "
                f"`{log_meta['fisier']}` · {log_meta['durata_s']}s · {ai_badge}"
            )
            with st.expander(header, expanded=False):
                # Sumar
                c1, c2, c3, c4, c5 = st.columns(5)
                c1.metric("Total produse",          s.get("total_produse", 0))
                c2.metric("Categorii fixate",       s.get("categorii_fixate", 0))
                c3.metric("Caracteristici adaugate",s.get("caracteristici_adaugate", 0))
                c4.metric("Completate automat",     s.get("completate_automat", 0))
                c5.metric("Necesita manual",        s.get("necesita_manual", 0))

                log_data = read_log(log_meta["path"])
                mapped   = log_data.get("mapate", [])
                unmapped = log_data.get("nemapate", [])

                tab1, tab2 = st.tabs([
                    f"✅ Mapate ({len(mapped)})",
                    f"⚠️ Nemapate / Manual ({len(unmapped)})",
                ])

                with tab1:
                    if not mapped:
                        st.caption("Niciun produs mapat automat.")
                    else:
                        import pandas as pd
                        rows = []
                        for p in mapped:
                            chars = ", ".join(
                                f"{k}: {v}" for k, v in list(p.get("caracteristici_adaugate", {}).items())[:5]
                            ) or "—"
                            rows.append({
                                "ID":          p.get("id", ""),
                                "Titlu":       p.get("titlu", "")[:70],
                                "Eroare":      p.get("eroare", ""),
                                "Actiune":     p.get("actiune", ""),
                                "Categorie":   p.get("categorie_noua", ""),
                                "Char. adaugate": chars,
                                "Motiv":       p.get("motiv", ""),
                            })
                        st.dataframe(pd.DataFrame(rows), width="stretch", height=300)

                with tab2:
                    if not unmapped:
                        st.caption("Toate produsele cu erori au fost rezolvate.")
                    else:
                        import pandas as pd
                        rows = []
                        for p in unmapped:
                            rows.append({
                                "ID":       p.get("id", ""),
                                "Titlu":    p.get("titlu", "")[:70],
                                "Eroare":   p.get("eroare", ""),
                                "Motiv":    p.get("motiv", ""),
                                "Obligatorii lipsa": ", ".join(
                                    p.get("caracteristici_obligatorii_lipsa", [])
                                ) or "—",
                            })
                        st.dataframe(pd.DataFrame(rows), width="stretch", height=300)

    # ── Quick start guide ──────────────────────────────────────────────────────
    section_header("Cum funcționează", "Trei pași simpli pentru a procesa ofertele")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown(
            '<div class="how-card">'
            '<div class="step-num">1️⃣</div>'
            '<h4>Setup</h4>'
            '<p>Mergi la <strong>⚙️ Setup Marketplace</strong> și încarcă cele 3 fișiere de referință '
            'pentru fiecare marketplace (Categorii, Caracteristici, Valori).</p>'
            '</div>',
            unsafe_allow_html=True,
        )
    with c2:
        st.markdown(
            '<div class="how-card">'
            '<div class="step-num">2️⃣</div>'
            '<h4>Procesare</h4>'
            '<p>Mergi la <strong>📁 Process Offers</strong>, selectează marketplace-ul, '
            'încarcă fișierul de oferte și pornește procesarea automată.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
    with c3:
        st.markdown(
            '<div class="how-card">'
            '<div class="step-num">3️⃣</div>'
            '<h4>Export</h4>'
            '<p>Revizuiește rezultatele în <strong>📊 Results</strong> și descarcă '
            'Excel-ul corectat, gata de import.</p>'
            '</div>',
            unsafe_allow_html=True,
        )
