import streamlit as st
from pathlib import Path
from core.state import (
    all_marketplace_names, get_marketplace, set_marketplace,
    add_custom_marketplace, PREDEFINED_MARKETPLACES,
    get_error_codes, set_error_codes, DUCKDB_MARKETPLACES,
    clear_marketplace_data, remove_custom_marketplace,
    get_backend,
)

from core.loader import MarketplaceData
from core.app_logger import get_logger
log = get_logger("marketplace.setup")


def _do_save_unified(selected: str, cat_src, char_src, val_src, source_type: str = "upload"):
    """Unified save function — routes to DuckDB, Parquet, or both based on REFERENCE_BACKEND."""
    from core import reference_store_duckdb as duckdb_store
    from core.loader import load_categories, load_characteristics, load_values

    backend = get_backend()

    with st.spinner("Se procesează și se salvează..."):
        try:
            cats  = load_categories(cat_src)
            chars = load_characteristics(char_src)
            vals  = load_values(val_src)
        except Exception as e:
            st.error(f"❌ Eroare la parsarea fișierelor: {e}")
            return

        # ── DuckDB save ───────────────────────────────────────────────────────
        if backend in ("duckdb", "dual"):
            try:
                mp_id = duckdb_store.marketplace_id_slug(selected)
                duckdb_store.init_db(duckdb_store.DB_PATH)
                duckdb_store.ensure_marketplace(duckdb_store.DB_PATH, mp_id, selected)

                sources = {
                    "categories":      getattr(cat_src, "name", str(cat_src)),
                    "characteristics": getattr(char_src, "name", str(char_src)),
                    "values":          getattr(val_src,  "name", str(val_src)),
                }
                run_id = duckdb_store.import_marketplace(
                    mp_id, cats, chars, vals, source_type, sources
                )

                cats2, chars2, vals2 = duckdb_store.load_marketplace_data(mp_id)
                mp_new = MarketplaceData(selected)
                mp_new.load_from_dataframes(cats2, chars2, vals2)
                st.session_state["marketplaces"][selected] = mp_new
                st.session_state.pop(f"_reload_{selected}", None)

                summary = duckdb_store.get_import_summary(run_id)
                issues  = duckdb_store.get_issues(run_id)

                st.success(
                    f"✅ Date salvate în DuckDB pentru **{selected}**: "
                    f"{summary['categories']} categorii, "
                    f"{summary['characteristics']} caracteristici, "
                    f"{summary['values']:,} valori. "
                    f"({summary['warnings']} warnings, {summary['errors']} errors)"
                )

                errors_list   = [i for i in issues if i["severity"] == "error"]
                warnings_list = [i for i in issues if i["severity"] == "warning"]
                for iss in errors_list:
                    st.error(f"❌ [{iss['issue_type']}] {iss['message']}")
                if warnings_list:
                    with st.expander(f"⚠️ {len(warnings_list)} warning-uri la import"):
                        for iss in warnings_list:
                            st.warning(f"[{iss['issue_type']}] {iss['message']}")

            except Exception as e:
                st.error(f"❌ Eroare la import DuckDB: {e}")
                log.error("DuckDB save failed for %s: %s", selected, e, exc_info=True)
                return

        # ── Parquet save (dual or parquet-only) ──────────────────────────────
        if backend in ("parquet", "dual"):
            try:
                from core.state import DATA_DIR
                mp_parquet = MarketplaceData(selected)
                if backend == "dual":
                    # Files already parsed for DuckDB step above.
                    # Streamlit UploadedFile cursor is at EOF — MUST NOT re-read.
                    # Reuse the already-parsed DataFrames.
                    mp_parquet.load_from_dataframes(cats, chars, vals)
                else:
                    # parquet-only: first parse
                    mp_parquet.load_from_files(cat_src, char_src, val_src)
                folder = DATA_DIR / selected.replace(" ", "_")
                mp_parquet.save_to_disk(folder)
                if backend == "parquet":
                    st.session_state["marketplaces"][selected] = mp_parquet
                    st.session_state.pop(f"_reload_{selected}", None)
                    stats = mp_parquet.stats()
                    st.success(
                        f"✅ Date salvate (Parquet) pentru **{selected}**: "
                        f"{stats['categories']} categorii, "
                        f"{stats['characteristics']} caracteristici, "
                        f"{stats['values']:,} valori permise."
                    )
                else:
                    log.info("Dual mode: Parquet also written for %s", selected)
            except Exception as e:
                st.error(f"❌ Eroare la salvare Parquet: {e}")
                return

        st.rerun()


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

    # ── Badge DuckDB pilot ─────────────────────────────────────────────────────
    backend = get_backend()
    if backend in ("duckdb", "dual"):
        st.info(f"🦆 **Backend: DuckDB** (`REFERENCE_BACKEND={backend}`) — `data/reference_data.duckdb`")
    elif backend == "parquet":
        st.warning("⚠️ **Backend: Parquet** (`REFERENCE_BACKEND=parquet`) — date stocate local ca fișiere.")

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
            "📌 **Formate acceptate:** Excel (`.xlsx`, `.xls`) și CSV (`.csv`, `.tsv`).  \n"
            "Aplicația detectează automat coloanele și ignoră ce nu are nevoie."
        )

        tab_upload, tab_local = st.tabs(["⬆️ Upload fișiere", "📂 Cale locală (fișiere mari)"])

        # ── Tab 1: Upload (existent, fișiere < 200 MB) ────────────────────────
        with tab_upload:
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
                    "emag_categories",
                    type=["xlsx", "xls", "csv", "tsv"],
                    key=f"cat_{selected}",
                    help="Fișierul cu categoriile marketplace-ului."
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
                    "emag_characteristics",
                    type=["xlsx", "xls", "csv", "tsv"],
                    key=f"char_{selected}",
                    help="Fișierul cu caracteristicile per categorie."
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
                    "characteristic_values",
                    type=["xlsx", "xls", "csv", "tsv"],
                    key=f"val_{selected}",
                    help="Fișierul cu valorile permise."
                )

            if cat_file and char_file and val_file:
                if st.button(f"💾 Salvează datele pentru {selected}", type="primary",
                             use_container_width=True, key=f"save_upload_{selected}"):
                    _do_save_unified(selected, cat_file, char_file, val_file, source_type="upload")
            else:
                st.warning("⚠️ Încarcă toate cele 3 fișiere pentru a putea salva.")

        # ── Tab 2: Cale locală (fără limită de mărime) ────────────────────────
        with tab_local:
            st.markdown(
                "Introdu căile complete ale fișierelor de pe disk. "
                "Fișierele **nu se uploadează** — sunt citite direct, fără limită de mărime.  \n"
                "Acceptă `.xlsx`, `.xls`, `.csv`, `.tsv`."
            )

            cat_path  = st.text_input(
                "Cale fișier Categorii",
                key=f"lp_cat_{selected}",
                placeholder=r"C:\date\emag_categories.csv",
            )
            char_path = st.text_input(
                "Cale fișier Caracteristici",
                key=f"lp_char_{selected}",
                placeholder=r"C:\date\emag_characteristics.xlsx",
            )
            val_path  = st.text_input(
                "Cale fișier Valori permise",
                key=f"lp_val_{selected}",
                placeholder=r"C:\date\characteristic_values.csv",
            )

            # Validare live a căilor
            paths_ok = True
            for label, p in [("Categorii", cat_path), ("Caracteristici", char_path), ("Valori", val_path)]:
                if p.strip():
                    if Path(p.strip()).exists():
                        size_mb = Path(p.strip()).stat().st_size / 1_048_576
                        st.caption(f"✅ {label}: găsit ({size_mb:.1f} MB)")
                    else:
                        st.caption(f"❌ {label}: fișierul nu există la calea specificată")
                        paths_ok = False

            all_paths_filled = all(p.strip() for p in [cat_path, char_path, val_path])

            if all_paths_filled and paths_ok:
                if st.button(f"💾 Salvează datele pentru {selected}", type="primary",
                             use_container_width=True, key=f"save_local_{selected}"):
                    _do_save_unified(selected, cat_path.strip(), char_path.strip(),
                                     val_path.strip(), source_type="local_path")
            elif all_paths_filled and not paths_ok:
                st.warning("⚠️ Corectează căile marcate cu ❌ înainte de a salva.")
            else:
                st.info("Completează toate cele 3 căi pentru a activa salvarea.")

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

    # ── DuckDB status panel ────────────────────────────────────────────────────
    if get_backend() in ("duckdb", "dual"):
        st.markdown("---")
        st.subheader("🦆 Status DuckDB")
        from core import reference_store_duckdb as _ddb
        _mp_id = _ddb.marketplace_id_slug(selected)
        db_status = _ddb.get_db_status(_mp_id)
        if db_status["available"]:
            st.success(
                f"✅ DuckDB activ — ultimul import: `{db_status['imported_at']}`  \n"
                f"**{db_status['categories']}** categorii · "
                f"**{db_status['characteristics']}** caracteristici · "
                f"**{db_status['values']:,}** valori  \n"
                f"Fișier DB: `{db_status['db_path']}`"
            )
        else:
            st.warning(f"⚠️ DuckDB nu este disponibil: {db_status.get('reason', 'necunoscut')}")

        if st.button("🔍 Verifică integritatea DuckDB", key="ddb_check"):
            with st.spinner("Verificare..."):
                try:
                    cats_r, chars_r, vals_r = _ddb.load_marketplace_data(_mp_id)
                    from core.loader import MarketplaceData as _MD
                    mp_test = _MD(selected)
                    mp_test.load_from_dataframes(cats_r, chars_r, vals_r)
                    if mp_test.is_loaded():
                        st.success(
                            f"✅ Integritate OK — datele din DuckDB sunt compatibile cu procesarea.  \n"
                            f"Categorii: {mp_test.stats()['categories']} · "
                            f"Valori indexate: {mp_test.stats()['values']:,}"
                        )
                    else:
                        st.error("❌ Date goale în DuckDB — reimportă fișierele.")
                except Exception as e:
                    st.error(f"❌ Eroare la verificare: {e}")

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
    # ── Zona periculoasă ───────────────────────────────────────────────────────
    st.markdown("---")
    st.subheader("⚠️ Zona periculoasă")

    col_clear, col_delete = st.columns(2)

    # ── Șterge datele ─────────────────────────────────────────────────────────
    with col_clear:
        st.markdown("**Șterge datele încărcate**")
        st.caption("Elimină categoriile, caracteristicile și valorile pentru acest marketplace. Marketplace-ul rămâne în listă.")
        if st.button("🗑️ Șterge datele", key=f"btn_clear_{selected}", use_container_width=True):
            st.session_state[f"confirm_clear_{selected}"] = True

    if st.session_state.get(f"confirm_clear_{selected}"):
        st.warning(f"Ești sigur că vrei să ștergi **toate datele** pentru **{selected}**? Acțiunea nu poate fi anulată.")
        c1, c2 = st.columns(2)
        if c1.button("✅ Da, șterge datele", key=f"confirm_clear_yes_{selected}", type="primary"):
            clear_marketplace_data(selected)
            st.session_state.pop(f"confirm_clear_{selected}", None)
            st.success(f"✅ Datele pentru **{selected}** au fost șterse.")
            st.rerun()
        if c2.button("❌ Anulează", key=f"confirm_clear_no_{selected}"):
            st.session_state.pop(f"confirm_clear_{selected}", None)
            st.rerun()

    # ── Șterge marketplace ────────────────────────────────────────────────────
    with col_delete:
        if selected not in PREDEFINED_MARKETPLACES:
            st.markdown("**Șterge marketplace-ul**")
            st.caption("Elimină complet marketplace-ul și toate datele asociate.")
            if st.button("🗑️ Șterge marketplace", key=f"btn_delete_{selected}", use_container_width=True):
                st.session_state[f"confirm_delete_{selected}"] = True
        else:
            st.markdown("**Șterge marketplace-ul**")
            st.caption("Marketplace-urile predefinite nu pot fi șterse.")
            st.button("🗑️ Șterge marketplace", key=f"btn_delete_{selected}", disabled=True, use_container_width=True)

    if st.session_state.get(f"confirm_delete_{selected}"):
        st.warning(f"Ești sigur că vrei să ștergi **marketplace-ul {selected}** și toate datele asociate? Acțiunea nu poate fi anulată.")
        c1, c2 = st.columns(2)
        if c1.button("✅ Da, șterge marketplace-ul", key=f"confirm_delete_yes_{selected}", type="primary"):
            remove_custom_marketplace(selected)
            st.session_state.pop(f"confirm_delete_{selected}", None)
            st.success(f"✅ Marketplace-ul **{selected}** a fost șters.")
            st.rerun()
        if c2.button("❌ Anulează", key=f"confirm_delete_no_{selected}"):
            st.session_state.pop(f"confirm_delete_{selected}", None)
            st.rerun()

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
