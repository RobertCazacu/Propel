import streamlit as st
from pathlib import Path
from core.state import (
    all_marketplace_names, get_marketplace, set_marketplace,
    add_custom_marketplace, PREDEFINED_MARKETPLACES,
    get_error_codes, set_error_codes,
    clear_marketplace_data, remove_custom_marketplace,
    get_backend, load_marketplace_on_select, is_marketplace_available,
    _cached_load_marketplace_data,
)
from pages.ui_helpers import hero_header, section_header

from core.loader import MarketplaceData
from core.app_logger import get_logger
log = get_logger("marketplace.setup")


def _do_save_unified(selected: str, cat_src, char_src, val_src, source_type: str = "upload"):
    """Unified save function — routes to DuckDB, Parquet, or both based on REFERENCE_BACKEND."""
    from core import reference_store_duckdb as duckdb_store
    from core.loader import load_categories, load_characteristics, load_values

    backend = get_backend()

    progress = st.progress(0)
    status   = st.empty()

    def _step(pct: int, msg: str):
        progress.progress(pct)
        status.info(f"⏳ {msg}")

    try:
        _step(5,  "Citire categorii (fișier mare — poate dura câteva minute)...")
        cats  = load_categories(cat_src)
        _step(55, "Citire valori permise...")
        vals  = load_values(val_src)
        _step(70, "Citire caracteristici...")
        chars = load_characteristics(char_src)
    except Exception as e:
        progress.empty()
        status.empty()
        st.error(f"❌ Eroare la parsarea fișierelor: {e}")
        return

    # ── DuckDB save ───────────────────────────────────────────────────────
    if backend in ("duckdb", "dual"):
        try:
            _step(75, "Inițializare DuckDB...")
            mp_id = duckdb_store.marketplace_id_slug(selected)
            duckdb_store.init_db(duckdb_store.DB_PATH)
            duckdb_store.ensure_marketplace(duckdb_store.DB_PATH, mp_id, selected)

            _step(80, "Import date în DuckDB...")
            sources = {
                "categories":      getattr(cat_src, "name", str(cat_src)),
                "characteristics": getattr(char_src, "name", str(char_src)),
                "values":          getattr(val_src,  "name", str(val_src)),
            }
            run_id = duckdb_store.import_marketplace(
                mp_id, cats, chars, vals, source_type, sources
            )

            _step(92, "Reîncărcare date în sesiune...")
            cats2, chars2, vals2 = duckdb_store.load_marketplace_data(mp_id)
            mp_new = MarketplaceData(selected)
            mp_new.load_from_dataframes(cats2, chars2, vals2)
            st.session_state["marketplaces"][selected] = mp_new
            st.session_state.pop(f"_reload_{selected}", None)
            # Invalidează cache-ul după import nou
            _cached_load_marketplace_data.clear()

            _step(98, "Finalizare...")
            summary = duckdb_store.get_import_summary(run_id)
            issues  = duckdb_store.get_issues(run_id)

            progress.progress(100)
            status.empty()
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
            progress.empty()
            status.empty()
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
                progress.progress(100)
                status.empty()
                st.success(
                    f"✅ Date salvate (Parquet) pentru **{selected}**: "
                    f"{stats['categories']} categorii, "
                    f"{stats['characteristics']} caracteristici, "
                    f"{stats['values']:,} valori permise."
                )
            else:
                log.info("Dual mode: Parquet also written for %s", selected)
        except Exception as e:
            progress.empty()
            status.empty()
            st.error(f"❌ Eroare la salvare Parquet: {e}")
            return

    st.rerun()


def render():
    hero_header("⚙️ Setup Marketplace", "Configurează fișierele de referință pentru fiecare marketplace.")

    # ── Marketplace selector + Add new ────────────────────────────────────────
    mp_names = all_marketplace_names()
    col_sel, col_add = st.columns([5, 1])
    with col_sel:
        selected = st.selectbox(
            "Marketplace",
            mp_names,
            label_visibility="collapsed",
        )
    with col_add:
        if st.button("➕ Nou", width="stretch", help="Adaugă un marketplace personalizat"):
            st.session_state["_show_add_mp"] = not st.session_state.get("_show_add_mp", False)

    if st.session_state.get("_show_add_mp"):
        c1, c2, c3 = st.columns([4, 1, 1])
        new_name = c1.text_input(
            "Nume marketplace nou",
            placeholder="ex: Libris, PCGarage, OLX...",
            label_visibility="collapsed",
            key="new_mp_name_input",
        )
        if c2.button("✓ Adaugă", type="primary", key="btn_add_mp"):
            if new_name.strip():
                add_custom_marketplace(new_name.strip())
                st.session_state.pop("_show_add_mp", None)
                st.success(f"Marketplace **{new_name}** adăugat!")
                st.rerun()
        if c3.button("✕", key="btn_cancel_add_mp"):
            st.session_state.pop("_show_add_mp", None)
            st.rerun()

    if not selected:
        return

    # Lazy-load marketplace data la selecție (cached — rapid dacă deja încărcat)
    load_marketplace_on_select(selected)
    mp = get_marketplace(selected)
    backend = get_backend()
    is_loaded = bool(mp and mp.is_loaded())

    # ── Status card ───────────────────────────────────────────────────────────
    if is_loaded:
        stats = mp.stats()
        backend_tag = "🦆 DuckDB" if backend in ("duckdb", "dual") else "📦 Parquet"
        from pages.ui_helpers import badge_html
        st.markdown(
            f'<div class="setup-status-card">'
            f'<div style="display:flex;align-items:center;justify-content:space-between">'
            f'<span style="font-size:1rem;font-weight:700;color:#f1f5f9">{selected}</span>'
            f'<div style="display:flex;gap:6px">'
            f'{badge_html("Configurat", "success")}'
            f'{badge_html(backend_tag, "info")}'
            f'</div></div>'
            f'<div class="setup-stats">'
            f'<div class="setup-stat"><span class="setup-stat-val">{stats["categories"]}</span>'
            f'<span class="setup-stat-lbl">Categorii</span></div>'
            f'<div class="setup-stat"><span class="setup-stat-val">{stats["characteristics"]}</span>'
            f'<span class="setup-stat-lbl">Caracteristici</span></div>'
            f'<div class="setup-stat"><span class="setup-stat-val">{stats["values"]:,}</span>'
            f'<span class="setup-stat-lbl">Valori permise</span></div>'
            f'</div></div>',
            unsafe_allow_html=True,
        )
        col_reload, _ = st.columns([2, 5])
        if col_reload.button("🔄 Reîncarcă fișierele", type="secondary", width="stretch"):
            st.session_state[f"_reload_{selected}"] = True
    else:
        st.markdown(
            f'<div class="setup-empty-card">'
            f'<div style="font-size:2rem">📂</div>'
            f'<div style="font-size:1rem;font-weight:700;color:#f1f5f9;margin-top:8px">{selected}</div>'
            f'<div style="color:#6b7280;font-size:0.85rem;margin-top:4px">'
            f'Încarcă cele 3 fișiere de referință pentru a configura acest marketplace.</div>'
            f'</div>',
            unsafe_allow_html=True,
        )

    if backend == "parquet":
        st.warning(
            "⚠️ **Backend Parquet este deprecat.** "
            "Migrează la DuckDB: `scripts/migrate_parquet_to_duckdb.py` și setează `REFERENCE_BACKEND=duckdb` în `.env`."
        )

    # ── Upload section (visible when not loaded or reloading) ─────────────────
    if not is_loaded or st.session_state.get(f"_reload_{selected}"):
        section_header("Fișiere de referință", "Trei fișiere necesare — Excel sau CSV")

        tab_upload, tab_local = st.tabs(["⬆️ Upload direct", "📂 Cale locală (fișiere mari)"])

        with tab_upload:
            from core.templates import categories_template, characteristics_template, values_template

            col1, col2, col3 = st.columns(3)

            with col1:
                st.markdown(
                    '<div class="file-slot-header">'
                    '<span class="file-slot-icon">📂</span>'
                    '<span class="file-slot-title">Categorii</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.caption("`id` · `emag_id` · `name` · `parent_id`")
                st.download_button(
                    "⬇️ Descarcă model",
                    data=categories_template(),
                    file_name="model_categories.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_cat_{selected}",
                    width="stretch",
                )
                cat_file = st.file_uploader(
                    "Categorii",
                    type=["xlsx", "xls", "csv", "tsv"],
                    key=f"cat_{selected}",
                    label_visibility="collapsed",
                )

            with col2:
                st.markdown(
                    '<div class="file-slot-header">'
                    '<span class="file-slot-icon">🏷</span>'
                    '<span class="file-slot-title">Caracteristici</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.caption("`id` · `category_id` · `name` · `mandatory`")
                st.download_button(
                    "⬇️ Descarcă model",
                    data=characteristics_template(),
                    file_name="model_characteristics.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_char_{selected}",
                    width="stretch",
                )
                char_file = st.file_uploader(
                    "Caracteristici",
                    type=["xlsx", "xls", "csv", "tsv"],
                    key=f"char_{selected}",
                    label_visibility="collapsed",
                )

            with col3:
                st.markdown(
                    '<div class="file-slot-header">'
                    '<span class="file-slot-icon">📋</span>'
                    '<span class="file-slot-title">Valori permise</span>'
                    '</div>',
                    unsafe_allow_html=True,
                )
                st.caption("`category_id` · `characteristic_id` · `value`")
                st.download_button(
                    "⬇️ Descarcă model",
                    data=values_template(),
                    file_name="model_values.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_val_{selected}",
                    width="stretch",
                )
                val_file = st.file_uploader(
                    "Valori permise",
                    type=["xlsx", "xls", "csv", "tsv"],
                    key=f"val_{selected}",
                    label_visibility="collapsed",
                )

            # Progress indicator
            n_up = sum(1 for f in [cat_file, char_file, val_file] if f)
            if n_up < 3:
                st.markdown(
                    f'<div class="upload-progress">'
                    f'<span style="color:#6b7280;font-size:12px">{n_up} din 3 fișiere încărcate</span>'
                    f'<div class="upload-bar">'
                    f'<div class="upload-bar-fill" style="width:{n_up/3*100:.0f}%"></div>'
                    f'</div></div>',
                    unsafe_allow_html=True,
                )

            if cat_file and char_file and val_file:
                if st.button(
                    f"💾 Salvează datele pentru {selected}",
                    type="primary",
                    width="stretch",
                    key=f"save_upload_{selected}",
                ):
                    _do_save_unified(selected, cat_file, char_file, val_file, source_type="upload")

        with tab_local:
            st.caption(
                "Fișierele sunt citite direct de pe disk — fără limită de mărime. "
                "Acceptă `.xlsx`, `.xls`, `.csv`, `.tsv`."
            )
            cat_path  = st.text_input("📂 Categorii",      key=f"lp_cat_{selected}",  placeholder=r"C:\date\emag_categories.csv")
            char_path = st.text_input("🏷 Caracteristici", key=f"lp_char_{selected}", placeholder=r"C:\date\emag_characteristics.xlsx")
            val_path  = st.text_input("📋 Valori permise", key=f"lp_val_{selected}",  placeholder=r"C:\date\characteristic_values.csv")

            paths_ok = True
            for label, p in [("Categorii", cat_path), ("Caracteristici", char_path), ("Valori", val_path)]:
                if p.strip():
                    if Path(p.strip()).exists():
                        size_mb = Path(p.strip()).stat().st_size / 1_048_576
                        st.caption(f"✅ {label}: {size_mb:.1f} MB")
                    else:
                        st.caption(f"❌ {label}: fișierul nu există")
                        paths_ok = False

            all_paths_filled = all(p.strip() for p in [cat_path, char_path, val_path])
            if all_paths_filled and paths_ok:
                if st.button(
                    f"💾 Salvează datele pentru {selected}",
                    type="primary",
                    width="stretch",
                    key=f"save_local_{selected}",
                ):
                    _do_save_unified(selected, cat_path.strip(), char_path.strip(),
                                     val_path.strip(), source_type="local_path")
            elif all_paths_filled and not paths_ok:
                st.warning("⚠️ Corectează căile marcate cu ❌ înainte de a salva.")
            else:
                st.info("Completează toate cele 3 căi pentru a activa salvarea.")

    # ── Configuration + Preview (only when loaded) ────────────────────────────
    mp = get_marketplace(selected)
    if mp and mp.is_loaded():

        section_header("⚙️ Configurare", "Coduri de eroare și provider AI")

        col_err, col_ai = st.columns(2)

        with col_err:
            st.markdown("**🚨 Coduri de eroare procesabile**")
            st.caption("eMAG: `1007, 1009, 1010` · Trendyol: `3111`")
            err_cfg = get_error_codes(selected)
            codes_input = st.text_input(
                "Coduri de eroare",
                value=", ".join(err_cfg["processable_codes"]),
                key=f"err_codes_{selected}",
                placeholder="ex: 1007, 1009, 1010",
                label_visibility="collapsed",
                help="Produsele cu aceste coduri vor fi procesate automat.",
            )
            if st.button("💾 Salvează coduri", key=f"save_err_{selected}", width="stretch"):
                new_codes = [c.strip() for c in codes_input.split(",") if c.strip()]
                set_error_codes(selected, {"processable_codes": new_codes})
                st.success(f"✅ Salvat: `{', '.join(new_codes)}`")

        with col_ai:
            from core.ai_enricher import is_configured as ai_configured
            from core.llm_router import get_router
            st.markdown("**🤖 Provider AI activ**")
            st.caption("Folosit pentru completarea automată a caracteristicilor.")
            if ai_configured():
                try:
                    router = get_router()
                    st.success(f"✅ **{router.provider_name.upper()}** — activ")
                except Exception as e:
                    st.error(f"❌ Eroare provider: {e}")
            else:
                st.warning("⚠️ Niciun provider configurat — se folosesc doar reguli.")
            st.info("Configurează din **🤖 LLM Providers**.")

        # Preview (collapsed)
        with st.expander("📋 Previzualizare date încărcate", expanded=False):
            tab1, tab2, tab3 = st.tabs(["Categorii", "Caracteristici", "Valori permise"])
            with tab1:
                st.dataframe(mp.categories.head(50), width="stretch", height=280)
            with tab2:
                df_chars = mp.characteristics.copy()
                df_chars["mandatory"] = df_chars["mandatory"].apply(
                    lambda x: "✅ Da" if str(x) in ("1", "True", "true", "1.0") else "○ Nu"
                )
                st.dataframe(df_chars.head(100), width="stretch", height=280)
            with tab3:
                st.dataframe(mp.values.head(100), width="stretch", height=280)

        # DuckDB status (collapsed)
        if backend in ("duckdb", "dual"):
            with st.expander("🦆 Status DuckDB", expanded=False):
                from core import reference_store_duckdb as _ddb
                _mp_id = _ddb.marketplace_id_slug(selected)
                db_status = _ddb.get_db_status(_mp_id)
                if db_status["available"]:
                    st.success(
                        f"✅ Activ — import: `{db_status['imported_at']}`  \n"
                        f"**{db_status['categories']}** categorii · "
                        f"**{db_status['characteristics']}** caracteristici · "
                        f"**{db_status['values']:,}** valori  \n"
                        f"Fișier DB: `{db_status['db_path']}`"
                    )
                else:
                    st.warning(f"⚠️ Indisponibil: {db_status.get('reason', 'necunoscut')}")
                if st.button("🔍 Verifică integritatea", key="ddb_check", width="stretch"):
                    with st.spinner("Verificare..."):
                        try:
                            cats_r, chars_r, vals_r = _ddb.load_marketplace_data(_mp_id)
                            from core.loader import MarketplaceData as _MD
                            mp_test = _MD(selected)
                            mp_test.load_from_dataframes(cats_r, chars_r, vals_r)
                            if mp_test.is_loaded():
                                st.success(
                                    f"✅ OK — {mp_test.stats()['categories']} categorii · "
                                    f"{mp_test.stats()['values']:,} valori"
                                )
                            else:
                                st.error("❌ Date goale în DuckDB — reimportă fișierele.")
                        except Exception as e:
                            st.error(f"❌ Eroare: {e}")

    # ── Dangerous zone ────────────────────────────────────────────────────────
    st.markdown('<div style="margin-top:2rem"></div>', unsafe_allow_html=True)

    with st.expander("⚠️ Zona periculoasă", expanded=False):
        st.caption("Acțiuni ireversibile — nu pot fi anulate.")

        col_clear, col_delete = st.columns(2)

        with col_clear:
            st.markdown("**Șterge datele încărcate**")
            st.caption("Elimină categoriile, caracteristicile și valorile. Marketplace-ul rămâne în listă.")
            if st.button("🗑️ Șterge datele", key=f"btn_clear_{selected}", width="stretch"):
                st.session_state[f"confirm_clear_{selected}"] = True

        with col_delete:
            if selected not in PREDEFINED_MARKETPLACES:
                st.markdown("**Șterge marketplace-ul**")
                st.caption("Elimină complet marketplace-ul și toate datele asociate.")
                if st.button("🗑️ Șterge marketplace", key=f"btn_delete_{selected}", width="stretch"):
                    st.session_state[f"confirm_delete_{selected}"] = True
            else:
                st.markdown("**Șterge marketplace-ul**")
                st.caption("Marketplace-urile predefinite nu pot fi șterse.")
                st.button("🗑️ Șterge marketplace", key=f"btn_delete_{selected}", disabled=True, width="stretch")

        if st.session_state.get(f"confirm_clear_{selected}"):
            st.warning(f"Confirmi ștergerea **tuturor datelor** pentru **{selected}**?")
            c1, c2 = st.columns(2)
            if c1.button("✅ Da, șterge datele", key=f"confirm_clear_yes_{selected}", type="primary"):
                clear_marketplace_data(selected)
                st.session_state.pop(f"confirm_clear_{selected}", None)
                st.success(f"✅ Datele pentru **{selected}** au fost șterse.")
                st.rerun()
            if c2.button("❌ Anulează", key=f"confirm_clear_no_{selected}"):
                st.session_state.pop(f"confirm_clear_{selected}", None)
                st.rerun()

        if st.session_state.get(f"confirm_delete_{selected}"):
            st.warning(f"Confirmi ștergerea **marketplace-ului {selected}** și a tuturor datelor?")
            c1, c2 = st.columns(2)
            if c1.button("✅ Da, șterge marketplace-ul", key=f"confirm_delete_yes_{selected}", type="primary"):
                remove_custom_marketplace(selected)
                st.session_state.pop(f"confirm_delete_{selected}", None)
                st.success(f"✅ Marketplace-ul **{selected}** a fost șters.")
                st.rerun()
            if c2.button("❌ Anulează", key=f"confirm_delete_no_{selected}"):
                st.session_state.pop(f"confirm_delete_{selected}", None)
                st.rerun()

        with st.expander("🔌 Conexiune MySQL (coming soon)", expanded=False):
            st.info("Conectare directă la MySQL/MariaDB — în dezvoltare.")
            col1, col2 = st.columns(2)
            col1.text_input("Host", placeholder="localhost", disabled=True)
            col1.text_input("Database", placeholder="easysales", disabled=True)
            col2.text_input("User", placeholder="root", disabled=True)
            col2.text_input("Password", type="password", disabled=True)
            st.button("Testează conexiunea", disabled=True)
            st.caption("🚧 Funcționalitate în dezvoltare")
