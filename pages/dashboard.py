import streamlit as st
from core.state import all_marketplace_names, get_marketplace, load_dashboard_stats


def render():
    st.title("⚡ Marketplace Offer Processor")
    st.markdown("Completare automată caracteristici produse pentru multiple marketplace-uri.")
    st.markdown("---")

    mp_names = all_marketplace_names()
    loaded   = [n for n in mp_names if get_marketplace(n) and get_marketplace(n).is_loaded()]

    # ── Metrici cumulative (persistente pe disk) ───────────────────────────────
    stats        = load_dashboard_stats()
    session_res  = st.session_state.get("process_results", [])

    # Sesiunea curenta (daca exista) o afisam separat ca delta
    session_chars     = sum(len(r.get("new_chars", {})) for r in session_res)
    session_completed = sum(1 for r in session_res if r.get("new_chars") and not r.get("needs_manual"))

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Marketplace configurate", f"{len(loaded)} / {len(mp_names)}")
    col2.metric("Rulări totale", stats["runs"])
    col3.metric("Produse procesate", f"{stats['total_processed']:,}",
                delta=len(session_res) if session_res else None)
    col4.metric("Caracteristici adăugate", f"{stats['total_chars_added']:,}",
                delta=session_chars if session_res else None)
    col5.metric("Categorii fixate", f"{stats['total_cats_fixed']:,}")

    if stats["last_run"]:
        st.caption(f"Ultima rulare: **{stats['last_run']}**")

    st.markdown("---")

    # ── Marketplace cards ──────────────────────────────────────────────────────
    st.subheader("Status marketplace-uri")
    cols = st.columns(3)
    for i, name in enumerate(mp_names):
        mp = get_marketplace(name)
        with cols[i % 3]:
            if mp and mp.is_loaded():
                stats = mp.stats()
                st.success(f"**{name}** ✓")
                st.caption(
                    f"📂 {stats['categories']} categorii · "
                    f"🏷 {stats['characteristics']} caracteristici · "
                    f"📋 {stats['values']:,} valori"
                )
            else:
                st.warning(f"**{name}**")
                st.caption("Nu este configurat. Mergi la ⚙️ Setup.")

    # ── Istoric rulari (log-uri) ───────────────────────────────────────────────
    st.markdown("---")
    st.subheader("📋 Istoric procesari (ultimele 7 zile)")

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
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=300)

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
                        st.dataframe(pd.DataFrame(rows), use_container_width=True, height=300)

    # ── Quick start guide ──────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("Cum funcționează")
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("### 1️⃣ Setup")
        st.markdown("Mergi la **⚙️ Setup Marketplace** și încarcă cele 3 fișiere de referință pentru fiecare marketplace (Categorii, Caracteristici, Valori).")
    with c2:
        st.markdown("### 2️⃣ Procesare")
        st.markdown("Mergi la **📁 Process Offers**, selectează marketplace-ul, încarcă fișierul de oferte și pornește procesarea automată.")
    with c3:
        st.markdown("### 3️⃣ Export")
        st.markdown("Revizuiește rezultatele în **📊 Results** și descarcă Excel-ul corectat, gata de import.")
