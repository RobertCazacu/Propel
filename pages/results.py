import streamlit as st
import io
import pandas as pd
from core.exporter import export_excel, export_model_format


def render():
    st.title("📊 Rezultate & Export")
    st.markdown("---")

    results = st.session_state.get("process_results", [])
    if not results:
        st.info("Nu există rezultate de afișat. Mergi la **📁 Process Offers** și rulează procesarea.")
        return

    processed_mp = st.session_state.get("process_results_mp", "")
    if processed_mp:
        st.caption(f"Rezultate generate pentru: **{processed_mp}**")

    # ── Summary metrics ────────────────────────────────────────────────────────
    n_updated  = sum(1 for r in results if r.get("new_chars") or r.get("cleared"))
    n_chars    = sum(len(r.get("new_chars", {})) for r in results)
    n_cleared  = sum(len(r.get("cleared", [])) for r in results)
    n_manual   = sum(1 for r in results if r.get("needs_manual"))
    n_cat_fix  = sum(1 for r in results if r.get("action") in ("cat_assigned", "cat_corrected"))

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Produse actualizate", n_updated)
    col2.metric("Caracteristici adăugate", n_chars)
    col3.metric("Valori șterse (invalide)", n_cleared)
    col4.metric("Categorii fixate", n_cat_fix)
    col5.metric("🟡 Review manual", n_manual, delta_color="inverse")

    st.markdown("---")

    # ── Filters ────────────────────────────────────────────────────────────────
    col1, col2 = st.columns([3, 1])
    filter_type = col1.selectbox(
        "Filtrează",
        ["Toate", "Cu caracteristici adăugate", "Necesită review manual",
         "Categorie fixată", "Valori șterse"],
    )
    search_text = col2.text_input("Caută titlu", placeholder="...")

    def matches(r):
        if search_text and search_text.lower() not in str(r.get("title", "")).lower():
            return False
        if filter_type == "Cu caracteristici adăugate" and not r.get("new_chars"):
            return False
        if filter_type == "Necesită review manual" and not r.get("needs_manual"):
            return False
        if filter_type == "Categorie fixată" and r.get("action") not in ("cat_assigned", "cat_corrected"):
            return False
        if filter_type == "Valori șterse" and not r.get("cleared"):
            return False
        return True

    filtered = [r for r in results if matches(r)]
    st.caption(f"Afișez {len(filtered)} din {len(results)} produse")

    # ── Results table ──────────────────────────────────────────────────────────
    if not filtered:
        st.info("Niciun produs nu corespunde filtrului selectat.")
    else:
        # Build display dataframe
        rows = []
        for r in filtered:
            action_map = {
                "cat_assigned":  "🔵 Cat. atribuită",
                "cat_corrected": "🟠 Cat. corectată",
                "updated":       "🟢 Actualizat",
                "skip":          "⚪ Nemodificat",
            }
            action_label = action_map.get(r.get("action", "skip"), "⚪")
            if r.get("needs_manual"):
                action_label += " 🟡"

            new_chars = r.get("new_chars", {})
            chars_summary = ", ".join(f"{k}: {v}" for k, v in list(new_chars.items())[:4])
            if len(new_chars) > 4:
                chars_summary += f" ... (+{len(new_chars)-4})"

            rows.append({
                "ID":           r.get("id", ""),
                "Titlu":        str(r.get("title", ""))[:70],
                "Categorie":    r.get("new_category", ""),
                "Status":       action_label,
                "Char. adăugate": len(new_chars),
                "Detalii":      chars_summary or "—",
                "Șterse":       ", ".join(r.get("cleared", [])) or "—",
            })

        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, height=400,
                     column_config={
                         "Char. adăugate": st.column_config.NumberColumn(format="%d"),
                     })

    # ── Manual review section ──────────────────────────────────────────────────
    manual = [r for r in results if r.get("needs_manual")]
    if manual:
        st.markdown("---")
        with st.expander(f"🟡 Produse care necesită completare manuală ({len(manual)})", expanded=False):
            st.markdown("Aceste produse au **caracteristici obligatorii** pe care nu le-am putut completa automat (ex: Mărime pentru categorii copii în format CM).")
            for r in manual[:50]:
                missing = r.get("missing_mandatory", [])
                st.markdown(
                    f"- **{r.get('title', '')}** `{r.get('new_category', '')}` "
                    f"→ lipsește: {', '.join(missing) if missing else 'verificare necesară'}"
                )
            if len(manual) > 50:
                st.caption(f"... și încă {len(manual)-50} produse")

    # ── Export ─────────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⬇️ Export Excel")

    st.markdown("""
**Legenda culori:**
🟢 Verde — caracteristici noi &nbsp;|&nbsp; 🔵 Albastru — categorie atribuită &nbsp;|&nbsp;
🟠 Portocaliu — categorie corectată &nbsp;|&nbsp; 🔴 Roșu — valori invalide șterse &nbsp;|&nbsp; 🟡 Galben — review manual
    """)

    file_bytes = st.session_state.get("offers_file_buf")
    char_pairs = st.session_state.get("offers_pairs", [])
    file_name  = st.session_state.get("offers_file_name", "offers.xlsx")
    base_name  = file_name.rsplit(".", 1)[0]
    products   = st.session_state.get("offers_products", [])

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("**Format original** *(modifică fișierul sursă)*")
        st.caption("Funcționează doar dacă fișierul original are coloanele `Offer ch. N name/val`.")
        if file_bytes and st.button("📥 Export format original", use_container_width=True):
            with st.spinner("Se generează..."):
                try:
                    output_bytes = export_excel(io.BytesIO(file_bytes), results, char_pairs)
                    st.download_button(
                        f"⬇️ Descarcă {base_name}_fixed.xlsx",
                        data=output_bytes,
                        file_name=f"{base_name}_fixed.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"Eroare: {e}")

    with col2:
        st.markdown("**Format model import** *(recomandat)*")
        st.caption("Construiește fișierul de la zero cu coloanele `Characteristic Name / Value`.")
        if st.button("📥 Export format model import", type="primary", use_container_width=True):
            with st.spinner("Se generează..."):
                try:
                    output_bytes = export_model_format(
                        io.BytesIO(file_bytes) if file_bytes else None,
                        results,
                        products,
                    )
                    st.download_button(
                        f"⬇️ Descarcă {base_name}_model.xlsx",
                        data=output_bytes,
                        file_name=f"{base_name}_model.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        use_container_width=True,
                    )
                except Exception as e:
                    st.error(f"Eroare: {e}")
