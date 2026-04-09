import json
import re
import streamlit as st
import pandas as pd
from pathlib import Path
from core.logger import list_logs, read_log
from pages.ui_helpers import hero_header, section_header, kpi_row


def render():
    hero_header("🔍 Diagnostic Mapare", "Analiză detaliată a motivelor pentru care maparea automată nu a reușit.")

    logs = list_logs()
    if not logs:
        st.info("Nicio procesare înregistrată încă. Rulează o procesare pentru a vedea diagnosticele.")
        return

    # ── Selector log ──────────────────────────────────────────────────────────
    log_labels = [
        f"{m['timestamp']} · {m['marketplace']} · {m['fisier']}"
        for m in logs
    ]
    selected_idx = st.selectbox("Selectează rularea", range(len(logs)), format_func=lambda i: log_labels[i])
    log_data = read_log(logs[selected_idx]["path"])

    diagnostic = log_data.get("diagnostic", {})
    unmapped   = log_data.get("nemapate", [])
    mapped     = log_data.get("mapate", [])

    st.caption(
        f"Marketplace: **{log_data.get('marketplace')}** · "
        f"Fișier: `{log_data.get('fisier')}` · "
        f"AI: {'✅ activ' if log_data.get('ai_activat') else '❌ dezactivat'} · "
        f"Durată: {log_data.get('durata_s')}s"
    )
    st.markdown("---")

    # ── Sumar rapid ───────────────────────────────────────────────────────────
    s = log_data.get("sumar", {})
    kpi_row([
        {"value": s.get("cu_erori_procesate", 0), "label": "Procesate",          "color": "#6366f1"},
        {"value": s.get("completate_automat",  0), "label": "Completate automat", "color": "#22c55e"},
        {"value": s.get("necesita_manual",     0), "label": "Necesită manual",    "color": "#f59e0b"},
        {"value": s.get("categorii_fixate",    0), "label": "Categorii fixate"},
    ])

    tab1, tab2, tab3, tab_ai_metrics, tab_logs = st.tabs([
        "📂 Categorii nemapate",
        "🏷 Caracteristici nemapate",
        "🔎 Detalii per produs",
        "📊 AI Metrics",
        "📝 Logs Unificate",
    ])

    # ── Tab 1: motive categorie ────────────────────────────────────────────────
    with tab1:
        cat_reasons = diagnostic.get("motive_categorie_nemapata", {})

        if not cat_reasons:
            st.success("✅ Toate categoriile au fost mapate automat.")
        else:
            st.markdown(f"**{sum(cat_reasons.values())} produse** cu categorie nemapată, grupate pe motiv:")

            rows = sorted(cat_reasons.items(), key=lambda x: -x[1])
            df_cat = pd.DataFrame(rows, columns=["Motiv", "Nr. produse"])
            df_cat["% din total"] = (df_cat["Nr. produse"] / df_cat["Nr. produse"].sum() * 100).round(1).astype(str) + "%"
            st.dataframe(df_cat, width="stretch", hide_index=True)

            st.markdown("---")
            st.markdown("**Produse cu categorie nemapată:**")
            cat_rows = []
            for p in unmapped:
                if p.get("motiv_categorie_nemapata"):
                    cat_rows.append({
                        "ID":    p.get("id", ""),
                        "Titlu": p.get("titlu", "")[:80],
                        "Motiv": p.get("motiv_categorie_nemapata", ""),
                    })
            if cat_rows:
                st.dataframe(pd.DataFrame(cat_rows), width="stretch", hide_index=True, height=350)

    # ── Tab 2: motive caracteristici ──────────────────────────────────────────
    with tab2:
        char_reasons = diagnostic.get("motive_caracteristici_nemapate", {})

        if not char_reasons:
            st.success("✅ Toate caracteristicile obligatorii au fost completate.")
        else:
            st.markdown(f"**{len(char_reasons)} caracteristici distincte** nemapate:")

            rows = []
            for char_name, reason_counts in sorted(char_reasons.items()):
                for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
                    rows.append({
                        "Caracteristică":  char_name,
                        "Motiv":           reason,
                        "Nr. produse":     count,
                    })
            df_chars = pd.DataFrame(rows).sort_values("Nr. produse", ascending=False)
            st.dataframe(df_chars, width="stretch", hide_index=True, height=400)

            # Cele mai frecvente caracteristici problematice
            st.markdown("---")
            st.markdown("**Top caracteristici problematice:**")
            top = sorted(
                {cn: sum(rc.values()) for cn, rc in char_reasons.items()}.items(),
                key=lambda x: -x[1]
            )[:10]
            df_top = pd.DataFrame(top, columns=["Caracteristică", "Total produse afectate"])
            st.dataframe(df_top, width="stretch", hide_index=True)

    # ── Tab 3: detalii per produs ─────────────────────────────────────────────
    with tab3:
        search = st.text_input("Caută titlu produs", placeholder="ex: Nike Air Force...")
        show_filter = st.radio(
            "Afișează",
            ["Toate nemapate", "Categorie nemapată", "Caracteristici nemapate"],
            horizontal=True,
        )

        filtered = []
        for p in unmapped:
            if search and search.lower() not in p.get("titlu", "").lower():
                continue
            if show_filter == "Categorie nemapată" and not p.get("motiv_categorie_nemapata"):
                continue
            if show_filter == "Caracteristici nemapate" and not p.get("motiv_caracteristici_nemapate"):
                continue
            filtered.append(p)

        st.caption(f"Afișez {len(filtered)} din {len(unmapped)} produse nemapate")

        for p in filtered[:100]:
            cat_reason   = p.get("motiv_categorie_nemapata")
            chars_reasons = p.get("motiv_caracteristici_nemapate", {})

            with st.expander(f"**{p.get('titlu', '')}** — ID: {p.get('id', '')}"):
                c1, c2 = st.columns(2)
                c1.write(f"**Cod eroare:** {p.get('eroare', '—')}")
                c2.write(f"**Categorie:** {p.get('categorie_noua', '—') or '—'}")

                if cat_reason:
                    st.error(f"📂 **Motiv categorie:** {cat_reason}")

                if chars_reasons:
                    st.warning("🏷 **Motive caracteristici nemapate:**")
                    for char_name, reason in chars_reasons.items():
                        st.markdown(f"- `{char_name}` → {reason}")

                obligatorii = p.get("caracteristici_obligatorii_lipsa", [])
                if obligatorii:
                    st.markdown(f"**Obligatorii lipsă:** {', '.join(obligatorii)}")

        if len(filtered) > 100:
            st.caption(f"... și încă {len(filtered) - 100} produse (restrânge cu căutarea)")

    # ── Tab 4: AI Metrics ─────────────────────────────────────────────────────
    with tab_ai_metrics:
        section_header("AI Run Metrics", "Telemetrie și statistici pentru rulările AI")

        try:
            import duckdb
            from core.reference_store_duckdb import DB_PATH

            con = duckdb.connect(str(DB_PATH), read_only=True)

            # ── Summary KPIs ────────────────────────────────────────────────
            summary = con.execute("""
                SELECT
                    COUNT(*) AS total_runs,
                    ROUND(AVG(CAST(fields_accepted AS DOUBLE) / NULLIF(fields_requested, 0)) * 100, 1) AS accept_rate_pct,
                    ROUND(AVG(cost_usd), 6) AS avg_cost_usd,
                    ROUND(SUM(CAST(retry_count > 0 AS INTEGER)) * 100.0 / NULLIF(COUNT(*), 0), 1) AS retry_rate_pct,
                    ROUND(SUM(CAST(fallback_used AS INTEGER)) * 100.0 / NULLIF(COUNT(*), 0), 1) AS fallback_rate_pct
                FROM ai_run_log
            """).fetchone()

            if summary and summary[0] > 0:
                kpi_row([
                    {"value": f"{summary[0]:,}",              "label": "Total Runs",     "color": "#6366f1"},
                    {"value": f"{summary[1] or 0:.1f}%",      "label": "Accept Rate",    "color": "#22c55e"},
                    {"value": f"${summary[2] or 0:.5f}",       "label": "Avg Cost/Offer"},
                    {"value": f"{summary[3] or 0:.1f}%",      "label": "Retry Rate",     "color": "#f59e0b"},
                ])

                # ── Structured output KPIs ─────────────────────────────────
                try:
                    s_summary = con.execute("""
                        SELECT
                            SUM(CAST(structured_attempted AS INTEGER))  AS attempted,
                            SUM(CAST(structured_success   AS INTEGER))  AS success,
                            SUM(CAST(structured_fallback_used AS INTEGER)) AS fallback,
                            ROUND(AVG(CASE WHEN structured_attempted THEN structured_latency_ms END), 0) AS avg_lat,
                            MODE(CASE WHEN structured_attempted AND structured_model_used != ''
                                      THEN structured_model_used END) AS top_model
                        FROM ai_run_log
                        WHERE structured_mode != 'off'
                    """).fetchone()

                    if s_summary and s_summary[0] and s_summary[0] > 0:
                        st.markdown("#### 🧩 Structured Output")
                        sc1, sc2, sc3, sc4 = st.columns(4)
                        sc1.metric("Attempted", f"{s_summary[0]:,}")
                        sc2.metric("Success", f"{s_summary[1]:,}",
                                   delta=f"{s_summary[1]/s_summary[0]*100:.0f}%" if s_summary[0] else None)
                        sc3.metric("Fallback la text", f"{s_summary[2]:,}")
                        sc4.metric("Avg latency", f"{int(s_summary[3] or 0)} ms")
                        if s_summary[4]:
                            st.caption(f"Model structured: **{s_summary[4]}**")
                    else:
                        st.caption("🧩 Structured Output: nicio rulare cu structured activat încă.")
                except Exception:
                    pass  # coloane noi pot lipsi pe DB vechi

                # ── Shadow Mode Comparison ─────────────────────────────────
                try:
                    shadow_rows = con.execute("""
                        SELECT shadow_diff FROM ai_run_log
                        WHERE structured_mode = 'shadow' AND shadow_diff IS NOT NULL
                        ORDER BY created_at DESC LIMIT 100
                    """).fetchall()

                    if shadow_rows:
                        st.markdown("#### 🔀 Shadow Mode Comparison")
                        total_agree = 0
                        total_keys = 0
                        conflict_examples = []

                        for (raw_diff,) in shadow_rows:
                            try:
                                d = json.loads(raw_diff) if isinstance(raw_diff, str) else raw_diff
                                agree = len(d.get("agree", []))
                                all_keys = len(set(d.get("agree", []) + d.get("only_structured", []) + d.get("only_plain", [])))
                                total_agree += agree
                                total_keys += all_keys
                                if d.get("value_conflicts"):
                                    conflict_examples.append(d["value_conflicts"])
                            except Exception:
                                pass

                        agreement_rate = round(total_agree / max(total_keys, 1) * 100, 1)
                        sh1, sh2, sh3 = st.columns(3)
                        sh1.metric("Shadow Runs", f"{len(shadow_rows):,}")
                        sh2.metric("Agreement Rate", f"{agreement_rate:.1f}%",
                                   help="% câmpuri unde structured și plain au același rezultat")
                        sh3.metric("Runs cu conflicte", f"{len(conflict_examples):,}")

                        if conflict_examples:
                            with st.expander("Exemple conflicte structured vs plain"):
                                for cf in conflict_examples[:5]:
                                    st.json(cf)
                except Exception:
                    pass  # shadow_diff poate lipsi pe DB fără migrare

                # ── Per marketplace ────────────────────────────────────────
                st.markdown("#### Pe marketplace")
                df_mp = con.execute("""
                    SELECT
                        marketplace,
                        COUNT(*) AS runs,
                        ROUND(AVG(CAST(fields_accepted AS DOUBLE) / NULLIF(fields_requested, 0)) * 100, 1) AS accept_rate,
                        ROUND(SUM(cost_usd), 4) AS total_cost_usd
                    FROM ai_run_log
                    GROUP BY marketplace
                    ORDER BY runs DESC
                    LIMIT 10
                """).df()
                st.dataframe(df_mp, width="stretch")

                # ── Knowledge store size ───────────────────────────────────
                pk_count = con.execute("SELECT COUNT(*) FROM product_knowledge").fetchone()[0]
                st.info(f"Knowledge store: **{pk_count:,}** produse indexate")
            else:
                st.info("Nu există date de telemetry încă. Procesează câteva oferte pentru a vedea metrici.")

            con.close()

        except Exception as e:
            st.warning(f"AI Metrics indisponibil: {e}")

    # ── Tab 5: Logs Unificate ──────────────────────────────────────────────────
    with tab_logs:
        section_header("Logs Unificate", "Toate sursele de log într-un singur loc")

        col_lvl, col_src, col_n = st.columns(3)
        level_filter = col_lvl.selectbox(
            "Nivel minim", ["ALL", "DEBUG", "INFO", "WARNING", "ERROR"], key="ulog_level"
        )
        source_filter = col_src.selectbox(
            "Sursă", ["ALL", "app", "process", "ai", "vision"], key="ulog_source"
        )
        max_lines = int(col_n.number_input("Max linii", min_value=50, max_value=2000, value=500, step=50, key="ulog_max"))

        entries = []
        _log_base = Path(__file__).parent.parent / "data" / "logs"
        _ai_logs_dir = Path(__file__).parent.parent / "data" / "ai_logs"

        # Source 1: app.log
        app_log_path = _log_base / "app.log"
        if app_log_path.exists():
            try:
                for line in app_log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-1000:]:
                    m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?\[(\w+)\]\s+(.*)", line)
                    if m:
                        entries.append({"ts": m.group(1), "level": m.group(2), "source": "app", "message": m.group(3)})
            except Exception:
                pass

        # Source 2: process JSON logs
        try:
            for log_meta in list_logs()[:5]:
                try:
                    ld = read_log(log_meta["path"])
                    ts = log_meta.get("timestamp", "")[:19].replace("T", " ")
                    sumar = ld.get("sumar", {})
                    entries.append({
                        "ts": ts, "level": "INFO", "source": "process",
                        "message": (
                            f"Run: {log_meta.get('marketplace','')} | {log_meta.get('fisier','')} | "
                            f"{sumar.get('cu_erori_procesate', sumar.get('total', 0))} produse"
                        )
                    })
                except Exception:
                    pass
        except Exception:
            pass

        # Source 3: AI JSON logs
        if _ai_logs_dir.exists():
            try:
                ai_files = sorted(_ai_logs_dir.glob("*.json"), reverse=True)[:3]
                for ai_path in ai_files:
                    try:
                        for entry in json.loads(ai_path.read_text(encoding="utf-8", errors="replace")):
                            ts = str(entry.get("timestamp", ""))[:19].replace("T", " ")
                            entries.append({
                                "ts": ts, "level": "INFO", "source": "ai",
                                "message": (
                                    f"[{entry.get('type','')}] {entry.get('marketplace','')} | "
                                    f"offer={entry.get('request',{}).get('offer_id','')}"
                                )
                            })
                    except Exception:
                        pass
            except Exception:
                pass

        # Source 4: Vision JSONL logs
        vision_logs_dir = _log_base / "vision_runs"
        if vision_logs_dir.exists():
            try:
                for vf in sorted(vision_logs_dir.glob("*.jsonl"), reverse=True)[:2]:
                    try:
                        for line in vf.read_text(encoding="utf-8", errors="replace").splitlines()[-200:]:
                            ve = json.loads(line)
                            ts = str(ve.get("ts", ""))[:19].replace("T", " ")
                            entries.append({
                                "ts": ts,
                                "level": ve.get("level", "INFO"),
                                "source": "vision",
                                "message": (
                                    f"[{ve.get('stage','')}:{ve.get('event','')}] "
                                    f"offer={ve.get('offer_id','')} status={ve.get('status','')}"
                                ),
                            })
                    except Exception:
                        pass
            except Exception:
                pass

        # Filter by level
        level_order = {"DEBUG": 0, "INFO": 1, "WARNING": 2, "ERROR": 3}
        if level_filter != "ALL":
            min_ord = level_order.get(level_filter, 0)
            entries = [e for e in entries if level_order.get(e["level"], 0) >= min_ord]

        # Filter by source
        if source_filter != "ALL":
            entries = [e for e in entries if e["source"] == source_filter]

        # Sort descending by ts, limit
        entries.sort(key=lambda e: e["ts"], reverse=True)
        entries = entries[:max_lines]

        if entries:
            df_logs = pd.DataFrame(entries)[["ts", "level", "source", "message"]]
            st.dataframe(df_logs, width="stretch", hide_index=True, height=500)
            st.caption(f"{len(entries)} intrări afișate")
        else:
            st.info("Niciun log disponibil pentru filtrele selectate.")

    # ── Tab: App Log ───────────────────────────────────────────────────────────
    st.markdown("---")
    with st.expander("📋 App Log (data/logs/app.log)", expanded=False):
        log_path = Path(__file__).parent.parent / "data" / "logs" / "app.log"
        if not log_path.exists():
            st.info("Niciun log de aplicație încă. Rulează o procesare.")
        else:
            level_filter = st.selectbox(
                "Nivel minim",
                ["DEBUG", "INFO", "WARNING", "ERROR"],
                index=1,
                key="app_log_level",
            )
            levels = ["DEBUG", "INFO", "WARNING", "ERROR"]
            min_idx = levels.index(level_filter)
            try:
                lines = log_path.read_text(encoding="utf-8").splitlines()
                filtered_lines = [
                    ln for ln in lines
                    if any(f"[{lvl}]" in ln for lvl in levels[min_idx:])
                ]
                last_n = filtered_lines[-300:]
                st.code("\n".join(last_n), language="text")
                st.caption(f"Afișez ultimele {len(last_n)} din {len(filtered_lines)} linii filtrate (total {len(lines)} linii)")
                if st.button("Descarcă app.log"):
                    st.download_button(
                        "app.log",
                        data=log_path.read_bytes(),
                        file_name="app.log",
                        mime="text/plain",
                    )
            except Exception as e:
                st.error(f"Eroare la citire log: {e}")
