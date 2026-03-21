import streamlit as st
from core.state import (
    all_marketplace_names, get_marketplace, set_marketplace,
    add_custom_marketplace, PREDEFINED_MARKETPLACES,
    get_error_codes, set_error_codes,
)

from core.loader import MarketplaceData


def render():
    st.title("⚙️ Setup Marketplace")
    st.markdown("Încarcă fișierele de referință pentru fiecare marketplace. Datele se salvează local și nu trebuie reîncărcate la fiecare sesiune.")
    st.markdown("---")

    # ── Add custom marketplace ─────────────────────────────────────────────────
    with st.expander("➕ Adaugă marketplace nou"):
        col1, col2 = st.columns([3, 1])
        new_name = col1.text_input("Nume marketplace", placeholder="ex: Libris, PCGarage, OLX...")
        if col2.button("Adaugă", use_container_width=True) and new_name.strip():
            add_custom_marketplace(new_name.strip())
            st.success(f"Marketplace '{new_name}' adăugat!")
            st.rerun()

    st.markdown("---")

    # ── Per-marketplace setup ──────────────────────────────────────────────────
    mp_names = all_marketplace_names()
    selected = st.selectbox("Selectează marketplace", mp_names)

    if not selected:
        return

    mp = get_marketplace(selected)
    if mp and mp.is_loaded():
        stats = mp.stats()
        st.success(
            f"✅ **{selected}** este configurat: "
            f"{stats['categories']} categorii, "
            f"{stats['characteristics']} caracteristici, "
            f"{stats['values']:,} valori permise."
        )
        if st.button("🔄 Reîncarcă datele (suprascrie)", type="secondary"):
            st.session_state[f"_reload_{selected}"] = True

    if not (mp and mp.is_loaded()) or st.session_state.get(f"_reload_{selected}"):
        st.markdown("### Încarcă fișierele de referință")
        st.info(
            "📌 **Format așteptat:** Excel (.xlsx) cu coloanele standard. "
            "Descarcă modelele de mai jos pentru a vedea exact structura necesară.\n\n"
            "Aplicația detectează automat coloanele și ignoră ce nu are nevoie."
        )

        from core.templates import categories_template, characteristics_template, values_template

        col1, col2, col3 = st.columns(3)
        with col1:
            st.markdown("#### 📂 Categorii")
            st.download_button(
                "⬇️ Descarcă model",
                data=categories_template(),
                file_name="model_categories.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_cat_{selected}",
                use_container_width=True,
            )
            st.caption("Câmpuri: `id`, `emag_id`, `name`, `parent_id`")
            cat_file = st.file_uploader(
                "emag_categories.xlsx",
                type=["xlsx", "xls"],
                key=f"cat_{selected}",
                help="Fișierul cu categoriile marketplace-ului. Trebuie să aibă cel puțin coloanele: id, name."
            )
        with col2:
            st.markdown("#### 🏷 Caracteristici")
            st.download_button(
                "⬇️ Descarcă model",
                data=characteristics_template(),
                file_name="model_characteristics.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_char_{selected}",
                use_container_width=True,
            )
            st.caption("Câmpuri: `id`, `category_id`, `name`, `mandatory`")
            char_file = st.file_uploader(
                "emag_characteristics.xlsx",
                type=["xlsx", "xls"],
                key=f"char_{selected}",
                help="Fișierul cu caracteristicile per categorie. Trebuie să aibă: id, category_id, name, mandatory."
            )
        with col3:
            st.markdown("#### 📋 Valori permise")
            st.download_button(
                "⬇️ Descarcă model",
                data=values_template(),
                file_name="model_values.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_val_{selected}",
                use_container_width=True,
            )
            st.caption("Câmpuri: `category_id`, `characteristic_id`, `characteristic_name`, `value`")
            val_file = st.file_uploader(
                "characteristic_values.xlsx",
                type=["xlsx", "xls"],
                key=f"val_{selected}",
                help="Fișierul cu valorile permise. Trebuie să aibă: category_id, characteristic_name, value."
            )

        if cat_file and char_file and val_file:
            if st.button(f"💾 Salvează datele pentru {selected}", type="primary", use_container_width=True):
                with st.spinner("Se procesează fișierele..."):
                    try:
                        mp_new = MarketplaceData(selected)
                        mp_new.load_from_files(cat_file, char_file, val_file)
                        set_marketplace(selected, mp_new)
                        stats = mp_new.stats()
                        st.session_state.pop(f"_reload_{selected}", None)
                        st.success(
                            f"✅ Date salvate pentru **{selected}**: "
                            f"{stats['categories']} categorii, "
                            f"{stats['characteristics']} caracteristici, "
                            f"{stats['values']:,} valori."
                        )
                        st.rerun()
                    except Exception as e:
                        st.error(f"Eroare la procesare: {e}")
        else:
            st.warning("⚠️ Încarcă toate cele 3 fișiere pentru a putea salva.")

    # ── Preview section ────────────────────────────────────────────────────────
    mp = get_marketplace(selected)
    if mp and mp.is_loaded():
        st.markdown("---")
        st.subheader("📋 Previzualizare date")

        tab1, tab2, tab3 = st.tabs(["Categorii", "Caracteristici", "Valori permise (sample)"])

        with tab1:
            st.dataframe(mp.categories.head(50), use_container_width=True, height=300)

        with tab2:
            df_chars = mp.characteristics.copy()
            df_chars["mandatory"] = df_chars["mandatory"].apply(
                lambda x: "✅ Da" if str(x) in ("1", "True", "true", "1.0") else "○ Nu"
            )
            st.dataframe(df_chars.head(100), use_container_width=True, height=300)

        with tab3:
            st.dataframe(mp.values.head(100), use_container_width=True, height=300)

    # ── Error code configuration ───────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🚨 Coduri de eroare")
    st.markdown(
        "Fiecare marketplace folosește coduri proprii pentru tipurile de erori. "
        "Configurează codurile corecte pentru a activa procesarea automată."
    )

    err_cfg = get_error_codes(selected)
    st.markdown(
        "Introdu codurile de eroare care trebuie procesate. "
        "Sistemul va încerca automat toate fixurile disponibile (categorie + caracteristici) "
        "pentru orice produs cu aceste coduri."
    )

    codes_input = st.text_input(
        "Coduri de procesat (separate prin virgulă)",
        value=", ".join(err_cfg["processable_codes"]),
        key=f"err_codes_{selected}",
        placeholder="ex: 1007, 1009, 1010",
        help="eMAG: 1007, 1009, 1010 — Trendyol: 3111",
    )

    if st.button("💾 Salvează coduri de eroare", key=f"save_err_{selected}"):
        new_codes = [c.strip() for c in codes_input.split(",") if c.strip()]
        set_error_codes(selected, {"processable_codes": new_codes})
        st.success(f"✅ Coduri salvate pentru **{selected}**: {new_codes}")

    # ── AI Provider ────────────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("🤖 Provider AI")

    from core.ai_enricher import is_configured as ai_configured
    from core.llm_router import get_router

    if ai_configured():
        try:
            router = get_router()
            st.success(f"✅ Provider activ: **{router.provider_name.upper()}**")
        except Exception as e:
            st.error(f"❌ Eroare provider: {e}")
    else:
        st.warning("⚠️ Niciun provider AI configurat. Procesarea folosește doar reguli de bază.")

    st.info("🔧 Configurează și schimbă providerii AI din pagina **🤖 LLM Providers** din navigație.")
    with st.expander("🔌 Conexiune MySQL (coming soon)"):
        st.info(
            "În viitor poți conecta direct la baza de date MySQL/MariaDB "
            "pentru a încărca automat categoriile și valorile, fără fișiere Excel.\n\n"
            "**Câmpuri necesare:**"
        )
        col1, col2 = st.columns(2)
        col1.text_input("Host", placeholder="localhost", disabled=True)
        col1.text_input("Database", placeholder="easysales", disabled=True)
        col2.text_input("User", placeholder="root", disabled=True)
        col2.text_input("Password", type="password", disabled=True)
        st.button("Testează conexiunea", disabled=True)
        st.caption("🚧 Funcționalitate în dezvoltare")
