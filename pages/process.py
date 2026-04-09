import streamlit as st
import io
import time
import re
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.state import all_marketplace_names, get_marketplace, get_error_codes, get_all_processable_codes, is_marketplace_available, load_marketplace_on_select
from core.offers_parser import extract_products, get_error_code
from core.processor import process_product, validate_existing, explain_missing_chars
from core.app_logger import get_logger

log = get_logger("marketplace.process")

# Prețuri claude-haiku-4-5-20251001 (per token)
_AI_PRICE_INPUT  = 0.80 / 1_000_000   # $0.80 per million input tokens
_AI_PRICE_OUTPUT = 4.00 / 1_000_000   # $4.00 per million output tokens
_AI_BATCH_SIZE   = 60


def _resolve_category(title: str, current_cat: str, mp) -> tuple[str, str]:
    """
    Returns (final_category, action) where action is one of:
    'ok', 'unknown'
    """
    if current_cat and mp.category_id(current_cat):
        return current_cat, "ok"
    log.debug(
        "_resolve_category UNKNOWN: titlu=%r, cat_curenta=%r",
        title[:60], current_cat,
    )
    return "", "unknown"


def _estimate_ai_cost(to_process: list, mp) -> dict:
    """
    Estimează tokenii și costul API înainte de procesare.
    - Category AI: produse fără categorie valabilă → batch calls
    - Char AI: produse cu categorii valabile dar cu caracteristici obligatorii lipsă → per-product calls
    """
    _resolve_cache: dict = {}
    def _resolve_cached(title: str, cat: str):
        key = (title, cat)
        if key not in _resolve_cache:
            _resolve_cache[key] = _resolve_category(title, cat, mp)
        return _resolve_cache[key]

    # ── Category AI ──────────────────────────────────────────────────────────
    needs_cat_ai = [
        p for p in to_process
        if _resolve_cached(str(p.get("name") or ""), str(p.get("category") or ""))[1] == "unknown"
    ]
    n_cat_ai  = len(needs_cat_ai)
    n_batches = math.ceil(n_cat_ai / _AI_BATCH_SIZE) if n_cat_ai else 0
    n_cats    = len(mp.category_list())

    # Input: header instrucțiuni (~300 tok) + lista categorii (~5 tok/cat) + titlu produs (~30 tok/produs)
    cat_input  = n_batches * (300 + n_cats * 5) + n_cat_ai * 30
    # Output: ~15 tokeni per nume categorie
    cat_output = n_cat_ai * 15

    # ── Char AI ───────────────────────────────────────────────────────────────
    n_char_ai   = 0
    char_input  = 0
    char_output = 0

    for prod in to_process:
        title = str(prod.get("name") or "")
        cat   = str(prod.get("category") or "")
        _, action = _resolve_cached(title, cat)
        if action != "ok":
            continue  # categoria nu e rezolvată — se estimează separat mai jos
        cat_id = mp.category_id(cat)
        if not cat_id:
            continue
        mandatory = mp.mandatory_chars(cat_id)
        existing  = dict(prod.get("existing_chars") or {})
        missing   = [c for c in mandatory if not existing.get(c)]
        if missing:
            n_char_ai   += 1
            char_input  += 350          # info produs + opțiuni caractere
            char_output += min(len(missing) * 25, 300)

    # Pentru produsele la care AI va atribui categoria: estimare conservatoare
    # (50% vor necesita completare caractere, medie 2 câmpuri)
    ai_cat_chars_est = int(n_cat_ai * 0.5)
    n_char_ai   += ai_cat_chars_est
    char_input  += ai_cat_chars_est * 350
    char_output += ai_cat_chars_est * 50

    total_input  = cat_input  + char_input
    total_output = cat_output + char_output
    cost_usd     = total_input * _AI_PRICE_INPUT + total_output * _AI_PRICE_OUTPUT

    return {
        "n_cat_ai":    n_cat_ai,
        "n_batches":   n_batches,
        "n_char_ai":   n_char_ai,
        "cat_input":   cat_input,
        "cat_output":  cat_output,
        "char_input":  char_input,
        "char_output": char_output,
        "total_input":  total_input,
        "total_output": total_output,
        "cost_usd":    cost_usd,
    }


def _audit_products(products: list, mp) -> dict:
    """
    Verifica produsele inainte de procesare.
    Returneaza raport cu categorii valide/invalide pentru marketplace-ul curent.
    """
    valid = 0
    invalid = 0
    invalid_cats: dict = {}

    for p in products:
        cat = str(p.get("category") or "").strip()
        if cat and mp.category_id(cat):
            valid += 1
        else:
            invalid += 1
            label = cat if cat else "(fara categorie)"
            invalid_cats[label] = invalid_cats.get(label, 0) + 1

    return {
        "valid":        valid,
        "invalid":      invalid,
        "invalid_cats": dict(sorted(invalid_cats.items(), key=lambda x: -x[1])),
    }


def _prevent_sleep():
    """Previne sleep/standby Windows pe durata procesarii. Returneaza functia de reset."""
    try:
        import ctypes
        ES_CONTINUOUS        = 0x80000000
        ES_SYSTEM_REQUIRED   = 0x00000001
        ES_AWAYMODE_REQUIRED = 0x00000040
        ctypes.windll.kernel32.SetThreadExecutionState(
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_AWAYMODE_REQUIRED
        )
        log.info("Sleep prevention activat — PC-ul nu va intra in standby pe durata procesarii.")
        def _reset():
            ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            log.info("Sleep prevention dezactivat.")
        return _reset
    except Exception as e:
        log.debug("Sleep prevention indisponibil: %s", e)
        return lambda: None


def _process_all(products, mp, progress_bar, status_text, use_ai=False, marketplace="", image_options=None,
                 resume_results=None, resume_ids=None, checkpoint_filename=""):
    from core.ai_logger import start_run as _ai_start_run
    _ai_start_run(marketplace)

    _reset_sleep = _prevent_sleep()

    # ── Vision run logger (optional) ──────────────────────────────────────────
    _run_logger = None
    if image_options and any(image_options.get(k) for k in ("enable_color", "enable_product_hint", "enable_yolo", "enable_clip")):
        try:
            from core.vision.vision_logger import new_run_logger
            _run_logger = new_run_logger(marketplace)
        except Exception:
            pass
    if image_options is not None:
        image_options = {**image_options, "run_logger": _run_logger}

    results = []
    total = len(products)
    processable_codes = get_all_processable_codes(marketplace)

    # ── Diagnostic log la start ────────────────────────────────────────────────
    mp_stats = mp.stats() if mp else {}
    n_with_cat    = sum(1 for p in products if str(p.get("category") or "").strip())
    n_valid_cat   = sum(1 for p in products if mp.category_id(str(p.get("category") or "").strip()))
    n_processable = sum(
        1 for p in products
        if get_error_code(str(p.get("error") or "")) in processable_codes
    )
    log.info(
        "START procesare [%s]: %d produse total, %d de procesat (cod eligibil), "
        "%d cu categorie in fisier, %d cu categorie valida in MP, AI=%s, "
        "MP incarcat: %d categorii / %d caracteristici / %d valori",
        marketplace, total, n_processable,
        n_with_cat, n_valid_cat, use_ai,
        mp_stats.get("categories", 0), mp_stats.get("characteristics", 0), mp_stats.get("values", 0),
    )
    if n_processable == 0:
        codes_in_file = set(
            get_error_code(str(p.get("error") or "")) for p in products
        ) - {None}
        if not codes_in_file:
            log.warning(
                "[%s] NICIUN produs nu are cod de eroare in fisier (%d produse). "
                "Coloana 'Eroare oferta' lipseste sau este goala — nu se va procesa nimic.",
                marketplace, total,
            )
        else:
            log.warning(
                "[%s] Niciun produs nu are cod eligibil. "
                "Coduri in fisier: %s. Coduri procesabile configurate: %s.",
                marketplace, sorted(codes_in_file),
                sorted(get_all_processable_codes(marketplace)),
            )

    if n_valid_cat == 0 and n_processable > 0:
        if use_ai:
            log.info(
                "[%s] Niciun produs nu are o categorie valida — AI activ, "
                "categoriile vor fi determinate automat prin batch AI.",
                marketplace,
            )
        else:
            log.warning(
                "[%s] Niciun produs nu are o categorie valida pentru acest marketplace. "
                "AI dezactivat — categoriile NU pot fi determinate automat.",
                marketplace,
            )

    # ── Pre-procesare batch categorie (1 apel API pentru toate produsele fara categorie) ─
    if use_ai:
        try:
            from core.ai_enricher import suggest_categories_batch, is_configured
            _ai_ok = is_configured()
            if not _ai_ok:
                st.warning("⚠️ AI dezactivat — ANTHROPIC_API_KEY lipsește sau e invalidă în .env. Categoriile nu vor fi determinate automat.")
                log.warning("[%s] is_configured() = False — API key lipseste sau invalida.", marketplace)
            if _ai_ok:
                # Colecteaza produsele care au nevoie de AI pentru categorie
                needs_ai_cat = []
                for prod in products:
                    err_code = get_error_code(str(prod.get("error") or ""))
                    if err_code not in processable_codes:
                        continue
                    cat = str(prod.get("category") or "")
                    title = str(prod.get("name") or "")
                    _, cat_action = _resolve_category(title, cat, mp)
                    if cat_action == "unknown":
                        needs_ai_cat.append({
                            "id": prod.get("id") or title,
                            "title": title,
                            "description": str(prod.get("description") or ""),
                        })

                if needs_ai_cat:
                    _n_cat_batches = max(1, math.ceil(len(needs_ai_cat) / 60))
                    _cat_cb_count  = [0]
                    status_text.text(
                        f"⏳ Faza 1/2 — AI categorii: {len(needs_ai_cat)} produse"
                        f" → {_n_cat_batches} batch-uri (5 paralel)..."
                    )
                    progress_bar.progress(1)

                    def _cat_status_cb(msg):
                        _cat_cb_count[0] += 1
                        pct = min(38, max(1, int(_cat_cb_count[0] / _n_cat_batches * 40)))
                        progress_bar.progress(pct)
                        status_text.text(f"⏳ Faza 1/2 — {msg}")

                    ai_cat_map = suggest_categories_batch(
                        needs_ai_cat,
                        mp.category_list(),
                        marketplace=marketplace,
                        status_callback=_cat_status_cb,
                    )
                    progress_bar.progress(40)
                    status_text.text(f"✅ Faza 1/2 completă — {len(needs_ai_cat)} categorii determinate.")
                    # Injecteaza categoriile in produse
                    for prod in products:
                        pid = prod.get("id") or str(prod.get("name") or "")
                        if pid in ai_cat_map and ai_cat_map[pid]:
                            prod["_ai_category"] = ai_cat_map[pid]
        except Exception as e:
            log.error("Exceptie batch AI categorii: %s", e, exc_info=True)
            st.error(f"❌ Eroare AI categorii: {e}")
            status_text.text(f"⚠️ AI categorii eșuat: {e}")

    # Bad values patterns to clear
    SIZE_INTL = {"S INTL", "M INTL", "L INTL", "XL INTL", "XXL INTL",
                 "XS INTL", "2XL INTL", "10XL INTL", "3XL INTL"}

    # ── Per-product worker (runs in ThreadPoolExecutor) ───────────────────────
    def _process_one(i_prod):
        i, prod = i_prod
        title   = str(prod.get("name") or "")
        desc    = str(prod.get("description") or "")
        cat     = str(prod.get("category") or "")
        err_raw = str(prod.get("error") or "")
        err_code = get_error_code(err_raw)
        existing = dict(prod.get("existing_chars") or {})

        result = {
            "id":           prod.get("id"),
            "title":        title,
            "original_cat": cat,
            "error_code":   err_code,
            "action":       "skip",
            "new_category": cat if mp.category_id(cat) else "",
            "cleared":      [],
            "new_chars":    {},
            "needs_manual": False,
            "mapping_log":  {},
        }

        if err_code not in processable_codes:
            return i, result

        # ── Fix category ──────────────────────────────────────────────────────
        final_cat, cat_action = _resolve_category(title, cat, mp)

        if cat_action == "assigned":
            result["action"]       = "cat_assigned"
            result["new_category"] = final_cat
        elif cat_action == "corrected":
            result["action"]       = "cat_corrected"
            result["new_category"] = final_cat

        if not final_cat or not mp.category_id(final_cat):
            ai_cat = prod.get("_ai_category")
            if ai_cat and mp.category_id(ai_cat):
                final_cat = ai_cat
                result["action"] = "cat_assigned"
                result["new_category"] = ai_cat
            elif ai_cat == "__timeout__":
                result["mapping_log"]["category_reason"] = (
                    "AI timeout — modelul nu a raspuns la timp. "
                    "Mareste OLLAMA_TIMEOUT in .env sau foloseste un model mai rapid."
                )
            elif ai_cat:
                result["mapping_log"]["category_reason"] = (
                    f"AI a sugerat '{ai_cat}' dar nu există în lista marketplace-ului"
                )
            elif use_ai:
                result["mapping_log"]["category_reason"] = (
                    "AI a returnat null — categoria nu a putut fi determinată din titlu"
                )
            else:
                result["mapping_log"]["category_reason"] = (
                    "AI dezactivat — categoria nu a putut fi determinată automat"
                )

            if not final_cat or not mp.category_id(final_cat):
                result["needs_manual"] = True
                return i, result

        cat_id = mp.category_id(final_cat)

        # ── Clear invalid existing values ─────────────────────────────────────
        # Orice valoare care nu există în lista de valori permise pentru
        # categoria și marketplace-ul curent este ștearsă automat.
        invalid = validate_existing(existing, final_cat, mp)
        cleared = []
        for char_name, bad_val in invalid.items():
            log.debug("Clear valoare invalida [%s]=%r (titlu: %s)", char_name, bad_val, title[:60])
            cleared.append(char_name)
            existing[char_name] = None

        # ── Sterge caracteristici care nu apartin acestui marketplace ──────────
        # Previne propagarea campurilor din alte marketplace-uri (RO/BG in HU etc.)
        for char_name in list(existing.keys()):
            if existing.get(char_name) and not mp.has_char(cat_id, char_name):
                if char_name not in cleared:
                    cleared.append(char_name)
                existing[char_name] = None

        result["cleared"] = cleared

        # ── Auto-fill characteristics ─────────────────────────────────────────
        new_chars = process_product(title, desc, final_cat, existing, mp, use_ai=use_ai, marketplace=marketplace, offer_id=str(prod.get("id", "")), product_meta={k: prod.get(k) for k in ("ean", "brand", "sku", "weight", "warranty")})
        result["new_chars"] = new_chars

        # ── Image analysis hook (optional, backward-compatible) ───────────────
        _img_any = image_options and (
            image_options.get("enable_color")
            or image_options.get("enable_product_hint")
            or image_options.get("enable_yolo")
            or image_options.get("enable_clip")
        )
        if _img_any:
            # Take only the first URL (column may contain comma-separated list)
            raw_urls = str(prod.get("image_url") or "")
            image_url = raw_urls.split(",")[0].strip()
            img_log_result = None
            try:
                from core.vision import analyze_product_image
                img_result = analyze_product_image(
                    image_url=image_url,
                    category=final_cat,
                    existing_chars={**existing, **new_chars},
                    valid_values_for_cat=mp._valid_values.get(cat_id, {}),
                    mandatory_chars=mp.mandatory_chars(cat_id),
                    marketplace=marketplace,
                    offer_id=str(prod.get("id", "")),
                    enable_color=image_options.get("enable_color", False),
                    enable_product_hint=image_options.get("enable_product_hint", False),
                    vision_provider=image_options.get("vision_provider"),
                    sku=str(prod.get("id", "")),
                    enable_yolo=image_options.get("enable_yolo", False),
                    enable_clip=image_options.get("enable_clip", False),
                    yolo_model=image_options.get("yolo_model", "yolov8n.pt"),
                    clip_model=image_options.get("clip_model", "ViT-B-32"),
                    yolo_conf=image_options.get("yolo_conf", 0.35),
                    clip_conf=image_options.get("clip_conf", 0.25),
                    suggestion_only=image_options.get("suggestion_only", False),
                    save_debug=image_options.get("save_debug", False),
                    run_logger=image_options.get("run_logger"),
                )
                result["image_analysis"] = img_result.to_dict()
                img_log_result = img_result.to_dict()

                # Fusion: dacă vision a returnat un hint de categorie, fusionăm cu cel text
                if img_result.product_type_hint and img_result.product_type_confidence > 0:
                    try:
                        from core.vision.fusion import (
                            fuse_category, action_to_confidence,
                            TextCategoryResult, ImageCategoryResult,
                        )
                        _fusion = fuse_category(
                            TextCategoryResult(
                                candidate=final_cat,
                                confidence=action_to_confidence(cat_action),
                                source=cat_action,
                            ),
                            ImageCategoryResult(
                                candidate=img_result.product_type_hint,
                                confidence=img_result.product_type_confidence,
                                source="vision_hint",
                            ),
                            rules={},
                            run_logger=image_options.get("run_logger"),
                            offer_id=str(prod.get("id", "")),
                        )
                        if _fusion.final_category and mp.category_id(_fusion.final_category):
                            final_cat = _fusion.final_category
                            result["new_category"] = final_cat
                            result["fusion_reason"] = _fusion.reason
                    except Exception:
                        pass

                # Only auto-fill if not suggestion_only mode
                if not image_options.get("suggestion_only", False):
                    # Strict gate: only add image suggestions that exist in tables
                    from core.char_validator import validate_new_chars_strict
                    img_validated, img_audit = validate_new_chars_strict(
                        img_result.suggested_attributes, cat_id, mp, source="image"
                    )
                    all_filled = {**existing, **new_chars}
                    for char_name, val in img_validated.items():
                        if not all_filled.get(char_name):
                            new_chars[char_name] = val

                    # Rejected image suggestions → chars_reasons + needs_manual flag
                    img_rejected = [e for e in img_audit if not e["accept"]]
                    if img_rejected:
                        result.setdefault("chars_reasons", []).extend(img_rejected)

                    # Structured log for all image chars (accepted + rejected)
                    if img_audit:
                        try:
                            from core.ai_logger import log_char_source_detail
                            log_char_source_detail(
                                offer_id=str(prod.get("id", "")),
                                marketplace=marketplace,
                                title=title,
                                category=final_cat,
                                char_entries=[
                                    {
                                        "char_name":           e["char_input"],
                                        "char_canonical":      e["char_canonical"],
                                        "source":              "image",
                                        "value_input":         e["value_input"],
                                        "value_mapped":        e["value_mapped"],
                                        "allowed_values_count": len(
                                            mp._valid_values.get(cat_id, {}).get(
                                                e["char_canonical"] or e["char_input"], set()
                                            )
                                        ),
                                        "validation_pass":     e["accept"],
                                    }
                                    for e in img_audit
                                ],
                            )
                        except Exception:
                            pass
                    result["new_chars"] = new_chars
            except Exception as e:
                err_dict = {"skipped_reason": str(e), "download_success": False}
                result["image_analysis"] = err_dict
                img_log_result = err_dict
            finally:
                if img_log_result is not None:
                    try:
                        from core.ai_logger import log_image_analysis
                        log_image_analysis(
                            offer_id=str(prod.get("id", "")),
                            marketplace=marketplace,
                            image_url=image_url,
                            enable_color=image_options.get("enable_color", False),
                            enable_product_hint=image_options.get("enable_product_hint", False),
                            result=img_log_result,
                        )
                    except Exception:
                        pass

        # ── Check mandatory still missing ─────────────────────────────────────
        mandatory = mp.mandatory_chars(cat_id)
        all_chars = {**existing, **new_chars}
        missing_mandatory = [c for c in mandatory if not all_chars.get(c)]
        if missing_mandatory:
            result["needs_manual"] = True
            result["missing_mandatory"] = missing_mandatory
            result["mapping_log"]["chars_reasons"] = explain_missing_chars(
                mp, cat_id, all_chars, use_ai
            )

        if result["action"] == "skip" and (new_chars or cleared):
            result["action"] = "updated"

        return i, result

    # ── Execuție paralelă — 8 produse procesate simultan ──────────────────────
    from core.logger import save_checkpoint, clear_checkpoint, CHECKPOINT_EVERY

    # Pre-populare cu rezultatele din checkpoint (dacă se reia)
    _resume_ids: set = set(resume_ids or [])
    results_ordered = [None] * total
    completed_count = 0
    _checkpoint_done_ids: set = set(_resume_ids)

    # Inserează rezultatele deja procesate la pozițiile corecte
    if resume_results:
        _id_to_idx = {str(prod.get("id", i)): i for i, prod in enumerate(products)}
        for rr in resume_results:
            pos = _id_to_idx.get(str(rr.get("id", "")))
            if pos is not None:
                results_ordered[pos] = rr
                completed_count += 1

    # Filtrează produsele deja procesate
    pending_products = [
        (i, prod) for i, prod in enumerate(products)
        if str(prod.get("id", i)) not in _resume_ids
    ]

    total_display = total  # total real (inclusiv cele deja procesate)
    status_text.text(f"⏳ Faza 2/2 — Procesare caracteristici: {completed_count}/{total_display} produse...")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {executor.submit(_process_one, (i, prod)): i for i, prod in pending_products}
        for future in as_completed(futures):
            idx, res = future.result()
            results_ordered[idx] = res
            completed_count += 1
            _checkpoint_done_ids.add(str(res.get("id", idx)))

            if completed_count % 10 == 0 or completed_count == total_display:
                pct = 40 + int(completed_count / total_display * 60)
                progress_bar.progress(pct)
                status_text.text(f"⏳ Faza 2/2 — Caracteristici: {completed_count}/{total_display} produse...")

            # Checkpoint periodic
            if checkpoint_filename and completed_count % CHECKPOINT_EVERY == 0:
                _done_so_far = [r for r in results_ordered if r is not None]
                save_checkpoint(marketplace, checkpoint_filename, _done_so_far,
                                total_display, _checkpoint_done_ids)

    results = [r for r in results_ordered if r is not None]

    # Șterge checkpointul după rulare completă
    if checkpoint_filename:
        clear_checkpoint(marketplace, checkpoint_filename)

    if _run_logger:
        try:
            _run_logger.finish()
        except Exception:
            pass

    _reset_sleep()  # reactivează sleep-ul normal după procesare
    return results


# ── Page render ────────────────────────────────────────────────────────────────

def render():
    from pages.ui_helpers import hero_header, section_header
    hero_header("📁 Procesare Oferte", "Încarcă fișierul de oferte, selectează marketplace-ul și pornește procesarea automată.")

    # ── Step 1: Select marketplace ────────────────────────────────────────────
    mp_names = all_marketplace_names()
    loaded   = [n for n in mp_names if is_marketplace_available(n)]

    if not loaded:
        st.warning("⚠️ Niciun marketplace configurat. Mergi la **⚙️ Setup Marketplace** mai întâi.")
        return

    section_header("1️⃣ Selectează marketplace")
    selected_mp = st.selectbox("Marketplace", loaded, key="proc_mp")
    load_marketplace_on_select(selected_mp)
    mp = get_marketplace(selected_mp)
    stats = mp.stats()
    from pages.ui_helpers import badge_html as _badge_html
    st.markdown(
        f'<div class="setup-status-card">'
        f'<div style="display:flex;align-items:center;justify-content:space-between">'
        f'<span style="font-size:1rem;font-weight:700;color:#f1f5f9">{selected_mp}</span>'
        f'{_badge_html("Activ", "success")}</div>'
        f'<div class="setup-stats">'
        f'<div class="setup-stat"><span class="setup-stat-val">{stats["categories"]}</span>'
        f'<span class="setup-stat-lbl">Categorii</span></div>'
        f'<div class="setup-stat"><span class="setup-stat-val">{stats["characteristics"]}</span>'
        f'<span class="setup-stat-lbl">Caracteristici</span></div>'
        f'<div class="setup-stat"><span class="setup-stat-val">{stats["values"]:,}</span>'
        f'<span class="setup-stat-lbl">Valori</span></div>'
        f'</div></div>',
        unsafe_allow_html=True,
    )

    # ── Step 2: Upload offers file ─────────────────────────────────────────────
    section_header("2️⃣ Încarcă fișierul de oferte")

    from core.templates import offers_template
    col_dl, col_info = st.columns([1, 3])
    with col_dl:
        st.download_button(
            "⬇️ Descarcă model oferte",
            data=offers_template(),
            file_name="model_offers.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_offers",
            width="stretch",
        )
    with col_info:
        st.caption(
            "Câmpuri obligatorii: `id intern ofertă`, `nume`, `categorie`, `eroare ofertă`  \n"
            "Opțional: `descriere`, `Offer ch. N name`, `Offer ch. N val.`"
        )

    offers_file = st.file_uploader(
        "Fișier oferte (.xlsx)",
        type=["xlsx", "xls"],
        key="offers_upload",
        help="Fișierul export din platforma marketplace-ului cu toate ofertele și erorile.",
    )

    if offers_file:
        file_bytes = offers_file.read()
        buf = io.BytesIO(file_bytes)
        buf.name = offers_file.name

        with st.spinner("Se citește fișierul..."):
            products, char_pairs = extract_products(buf)

        st.session_state["offers_products"] = products
        st.session_state["offers_pairs"]    = char_pairs
        st.session_state["offers_file_buf"] = file_bytes
        st.session_state["offers_file_name"]= offers_file.name

        err_counts = {}
        for p in products:
            code = get_error_code(str(p.get("error") or ""))
            if code:
                err_counts[code] = err_counts.get(code, 0) + 1

        processable = get_all_processable_codes(selected_mp)
        codes_str = ", ".join(sorted(processable)) or "—"
        proc_count = sum(err_counts.get(c, 0) for c in processable)
        other_count = sum(v for k, v in err_counts.items() if k not in processable)

        from pages.ui_helpers import kpi_row as _kpi_row_upload
        _kpi_row_upload([
            {"value": len(products), "label": "Total produse",                        "color": "#f1f5f9"},
            {"value": proc_count,    "label": f"De procesat (cod: {codes_str})",       "color": "#22c55e"},
            {"value": other_count,   "label": "Alte erori / fără eroare",              "color": "#6b7280"},
        ])

    # ── Step 3: Process ────────────────────────────────────────────────────────
    section_header("3️⃣ Procesare")

    products = st.session_state.get("offers_products", [])

    if not products:
        st.info("Încarcă un fișier de oferte pentru a continua.")
        return

    # ── Audit categorii ───────────────────────────────────────────────────────
    audit = _audit_products(products, mp)
    if audit["invalid"] > 0:
        with st.expander(
            f"⚠️ {audit['invalid']} produse au categorii invalide pentru **{selected_mp}**"
            f" ({audit['valid']} valide)",
            expanded=True,
        ):
            st.caption(
                "Categoriile de mai jos nu există în indexul acestui marketplace. "
                "Vor fi ignorate și sistemul va încerca să le determine automat prin AI."
            )
            rows = [{"Categorie invalidă": cat, "Nr. produse": n}
                    for cat, n in audit["invalid_cats"].items()]
            st.dataframe(rows, width="stretch", hide_index=True)
    else:
        st.success(f"Toate cele {audit['valid']} produse au categorii valide pentru {selected_mp}.")

    processable = get_all_processable_codes(selected_mp)
    to_process = [p for p in products
                  if get_error_code(str(p.get("error") or "")) in processable]

    if not to_process and products:
        error_codes_in_file = set(
            get_error_code(str(p.get("error") or "")) for p in products
        ) - {None}
        if not error_codes_in_file:
            st.warning(
                "⚠️ **Niciun produs nu are codul de eroare completat.** "
                "Asigură-te că ai descărcat coloana **Eroare ofertă** din eMAG înainte de export. "
                "Fără această coloană, sistemul nu știe ce produse trebuie procesate."
            )
        else:
            st.warning(
                f"⚠️ **Niciun produs nu corespunde codurilor de eroare configurate** pentru {selected_mp}. "
                f"Coduri găsite în fișier: `{', '.join(sorted(error_codes_in_file))}` — "
                f"coduri procesabile configurate: `{', '.join(sorted(processable))}`. "
                "Verifică configurarea codurilor în ⚙️ Setup."
            )
    else:
        st.info(f"**{len(to_process)}** produse cu erori vor fi procesate din totalul de **{len(products)}**.")

    # AI toggle
    from core.ai_enricher import is_configured as ai_configured
    ai_ready = ai_configured()

    col_ai1, col_ai2 = st.columns([3, 1])
    with col_ai1:
        if ai_ready:
            use_ai = st.toggle(
                "🤖 Activează îmbogățire AI (Claude API)",
                value=True,
                help="Claude API completează caracteristicile pe care regulile de bază nu le-au detectat."
            )
            st.caption("✅ API key configurată și activă.")
        else:
            use_ai = False
            st.toggle("🤖 Activează îmbogățire AI (Claude API)", value=False, disabled=True,
                      help="Configurează ANTHROPIC_API_KEY în fișierul .env pentru a activa.")
            st.caption("⚠️ API key neconfigurata. Mergi la **⚙️ Setup → Configurare API** pentru a o adăuga.")

    # ── AI Cost Estimate ──────────────────────────────────────────────────────
    from core.llm_router import get_router as _get_router
    _is_anthropic = False
    try:
        _is_anthropic = _get_router().provider_name == "anthropic"
    except Exception:
        pass

    if use_ai and to_process and _is_anthropic:
        est = _estimate_ai_cost(to_process, mp)
        with st.expander("💰 Cost estimativ API Claude", expanded=False):
            from pages.ui_helpers import kpi_row as _kpi_row_cost
            _kpi_row_cost([
                {"value": est["n_cat_ai"],           "label": "Produse → categorie AI"},
                {"value": est["n_batches"],           "label": "Batch-uri categorii"},
                {"value": est["n_char_ai"],           "label": "Produse → caractere AI"},
                {"value": f"~${est['cost_usd']:.4f}", "label": "Cost estimativ", "color": "#22c55e"},
            ])
            st.caption(
                f"Tokeni: **{est['total_input']:,}** input · **{est['total_output']:,}** output"
                f"  ·  $0.80/MTok input · $4.00/MTok output"
            )
            if est["n_cat_ai"] == 0:
                st.success("✅ Toate categoriile rezolvabile fără AI — cost categorizare: $0.00")
            if est["cost_usd"] < 0.001:
                st.info("💡 Cost sub $0.001 — practic gratuit pentru acest număr de oferte.")

    # ── Image analysis options (collapsed by default — optional feature) ─────────
    enable_color = False
    enable_yolo  = False
    enable_product_hint = False
    enable_clip  = False

    with st.expander("🖼️ Analiză imagine (opțional)", expanded=False):
        st.caption("Activează pentru a detecta culoarea, tipul produsului sau a valida categoria din imagini.")
        col_img1, col_img2 = st.columns(2)
        with col_img1:
            enable_color = st.checkbox(
                "Detectează culoarea din imagine",
                value=False,
                help="Analizează imaginea produsului și completează automat caracteristica de culoare dacă lipsește.",
            )
            enable_yolo = st.checkbox(
                "Detectare obiect YOLO",
                value=False,
                help="Folosește YOLO pentru a detecta și decupa obiectul principal din imagine înainte de analiză.",
            )
            if enable_yolo:
                try:
                    from core.vision.detection_yolo import is_available as _yolo_available
                    if not _yolo_available():
                        st.warning(
                            "YOLO dezactivat — pachetul `ultralytics` nu este instalat. "
                            "Rulează: `pip install ultralytics`"
                        )
                except Exception:
                    st.warning("YOLO dezactivat — eroare la verificarea ultralytics.")
        with col_img2:
            enable_product_hint = st.checkbox(
                "Folosește imaginea pentru îmbunătățirea categoriei",
                value=False,
                help="Folosește un model vision (Ollama llava-phi3) pentru a sugera tipul de produs din imagine.",
            )
            enable_clip = st.checkbox(
                "Validare semantică CLIP",
                value=False,
                help="Folosește CLIP pentru a valida semantic categoria detectată față de imaginea produsului.",
            )

    _any_image = enable_color or enable_product_hint or enable_yolo or enable_clip
    image_options = None
    if _any_image:
        vision_provider = None
        if enable_product_hint:
            import os
            _has_openai = bool(os.getenv("OPENAI_API_KEY", "").strip()
                               and not os.getenv("OPENAI_API_KEY", "").startswith("sk-your"))
            _vision_options = ["openai (gpt-4o-mini)", "ollama (local)"] if _has_openai \
                              else ["ollama (local)", "openai (gpt-4o-mini — cheie lipsă)"]
            _vision_sel = st.selectbox(
                "Provider vision",
                _vision_options,
                index=0,
                key="vision_provider_sel",
                help="OpenAI gpt-4o-mini: mai precis, cost mic per imagine. "
                     "Ollama: gratuit, necesită instalare locală.",
            )
            _use_openai_vision = "openai" in _vision_sel and _has_openai
            try:
                from core.vision.visual_provider import build_vision_provider, MockVisionProvider
                vision_provider = build_vision_provider(
                    "openai" if _use_openai_vision else "ollama"
                )
                if isinstance(vision_provider, MockVisionProvider):
                    st.warning(
                        f"Îmbunătățirea categoriei din imagine **dezactivată** — "
                        f"{vision_provider.fallback_reason}"
                    )
            except Exception:
                vision_provider = None

        # Advanced options (shown only when at least one image option is active)
        with st.expander("⚙️ Setări avansate imagine", expanded=False):
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                yolo_model = st.selectbox(
                    "Model YOLO",
                    ["yolov8n.pt", "yolov8s.pt", "yolov8m.pt"],
                    index=0,
                    disabled=not enable_yolo,
                    help="Dimensiunea modelului YOLO: n=nano (rapid), s=small, m=medium.",
                )
                yolo_conf = st.slider(
                    "Prag confidență YOLO",
                    min_value=0.10, max_value=0.90, value=0.35, step=0.05,
                    disabled=not enable_yolo,
                    help="Detecțiile sub acest prag sunt ignorate.",
                )
            with col_a2:
                clip_model = st.selectbox(
                    "Model CLIP",
                    ["ViT-B-32", "ViT-L-14"],
                    index=0,
                    disabled=not enable_clip,
                    help="ViT-B-32 = rapid; ViT-L-14 = mai precis dar mai lent.",
                )
                clip_conf = st.slider(
                    "Prag confidență CLIP",
                    min_value=0.10, max_value=0.90, value=0.25, step=0.05,
                    disabled=not enable_clip,
                    help="Scorul CLIP sub acest prag nu este luat în considerare.",
                )
            col_b1, col_b2 = st.columns(2)
            with col_b1:
                image_strategy = st.selectbox(
                    "Strategie imagini",
                    ["first_only", "best_confidence", "aggregate_vote"],
                    index=0,
                    help="first_only=prima imagine; best_confidence=cea cu scorul YOLO mai mare; aggregate_vote=vot majoritar.",
                )
                suggestion_only = st.checkbox(
                    "Doar sugestii (nu completează automat)",
                    value=False,
                    help="Când activ, rezultatele din imagine sunt vizibile dar nu suprascriu caracteristicile.",
                )
            with col_b2:
                save_debug = st.checkbox(
                    "Salvează crop/overlay debug",
                    value=False,
                    help="Salvează imaginile decupate și overlay-urile YOLO pentru inspecție manuală.",
                )
        image_options = {
            "enable_color": enable_color,
            "enable_product_hint": enable_product_hint,
            "enable_yolo": enable_yolo,
            "enable_clip": enable_clip,
            "vision_provider": vision_provider,
            "yolo_model": yolo_model if enable_yolo else "yolov8n.pt",
            "clip_model": clip_model if enable_clip else "ViT-B-32",
            "yolo_conf": yolo_conf if enable_yolo else 0.35,
            "clip_conf": clip_conf if enable_clip else 0.25,
            "image_strategy": image_strategy,
            "suggestion_only": suggestion_only,
            "save_debug": save_debug,
        }

    # ── Checkpoint resume UI ───────────────────────────────────────────────────
    from core.logger import load_checkpoint
    _file_name_ui = st.session_state.get("offers_file_name", "")
    _checkpoint = load_checkpoint(selected_mp, _file_name_ui) if _file_name_ui else None
    _resume_results, _resume_ids = None, None

    if _checkpoint:
        _done = _checkpoint.get("processed_cnt", 0)
        _tot  = _checkpoint.get("total", 0)
        st.warning(
            f"🔄 Progres salvat găsit: **{_done}/{_tot} produse** procesate "
            f"(salvat la {_checkpoint.get('saved_at', '?')}). "
            f"Apasă **Continuă** pentru a relua sau **Începe de la zero** pentru o rulare nouă."
        )
        col_resume, col_restart = st.columns(2)
        if col_resume.button("▶️ Continuă de unde am rămas", type="primary"):
            _resume_results = _checkpoint.get("results", [])
            _resume_ids     = set(_checkpoint.get("processed_ids", []))
        if col_restart.button("🔁 Începe de la zero"):
            from core.logger import clear_checkpoint
            clear_checkpoint(selected_mp, _file_name_ui)
            _checkpoint = None
            st.rerun()

    st.markdown('<div style="margin-top:1.5rem"></div>', unsafe_allow_html=True)
    if st.button(f"🚀 Pornește procesarea pentru {selected_mp}", type="primary", width="stretch"):
        progress = st.progress(0)
        status   = st.empty()
        start    = time.time()

        results = _process_all(
            products, mp, progress, status,
            use_ai=use_ai, marketplace=selected_mp, image_options=image_options,
            resume_results=_resume_results, resume_ids=_resume_ids,
            checkpoint_filename=_file_name_ui,
        )

        elapsed = time.time() - start
        st.session_state["process_results"] = results
        st.session_state["process_results_mp"] = selected_mp  # marketplace folosit la procesare

        from core.state import save_dashboard_stats
        save_dashboard_stats(results)

        try:
            from core.reference_store_duckdb import save_process_run
            save_process_run(results, selected_mp)
        except Exception:
            pass

        from core.logger import write_log, write_resolver_log
        file_name = st.session_state.get("offers_file_name", "necunoscut.xlsx")
        _log_path = write_log(selected_mp, file_name, results, elapsed, use_ai)
        write_resolver_log(selected_mp, file_name, results, base_log_path=_log_path)

        # Summary
        n_cat_assigned  = sum(1 for r in results if r["action"] == "cat_assigned")
        n_cat_corrected = sum(1 for r in results if r["action"] == "cat_corrected")
        n_chars_added   = sum(len(r["new_chars"]) for r in results)
        n_cleared       = sum(len(r["cleared"]) for r in results)
        n_manual        = sum(1 for r in results if r.get("needs_manual"))

        status.empty()
        progress.empty()

        st.success(f"✅ Procesare completă în {elapsed:.1f}s!")

        from pages.ui_helpers import kpi_row as _kpi_row
        _kpi_row([
            {"value": n_cat_assigned,  "label": "Categorii atribuite",    "color": "#6366f1"},
            {"value": n_cat_corrected, "label": "Categorii corectate",    "color": "#a5b4fc"},
            {"value": n_chars_added,   "label": "Caracteristici adăugate","color": "#22c55e"},
            {"value": n_cleared,       "label": "Valori invalide șterse", "color": "#f59e0b"},
            {"value": n_manual,        "label": "Necesită review manual", "color": "#ef4444"},
        ])

        # ── Needs Review section ──────────────────────────────────────────────
        review_products = [
            r for r in results
            if r.get("new_chars", {}).get("_review_flags")
        ]
        if review_products:
            with st.expander(
                f"⚠️ {len(review_products)} produse cu câmpuri ce necesită verificare",
                expanded=True,
            ):
                st.caption(
                    "Aceste valori au fost completate cu încredere scăzută (rescue/repair). "
                    "Verifică și corectează dacă este necesar."
                )
                for prod in review_products[:20]:  # max 20 in UI
                    flags = prod["new_chars"].get("_review_flags", {})
                    if not flags:
                        continue
                    st.markdown(f"**{str(prod.get('title', prod.get('id', '?')))[:80]}**")
                    for char_name, meta in flags.items():
                        val = meta.get("value")
                        top_k = meta.get("top_k", [])
                        method = meta.get("method", "?")
                        score = meta.get("score", 0)
                        sugestii = ", ".join(
                            f"`{v}` ({s:.2f})" for v, s in top_k[:3]
                        )
                        if val:
                            st.markdown(
                                f"  - **{char_name}**: completat cu `{val}` "
                                f"(method={method}, score={score:.2f}) | "
                                f"Sugestii: {sugestii}"
                            )
                        else:
                            st.markdown(
                                f"  - **{char_name}**: ❌ necompletat | "
                                f"Sugestii: {sugestii}"
                            )
                    st.divider()

        # ── Auto-save export la finalizare ────────────────────────────────────
        _file_bytes = st.session_state.get("offers_file_buf")
        _products   = st.session_state.get("offers_products", [])
        _file_name  = st.session_state.get("offers_file_name", "offers.xlsx")
        _base_name  = _file_name.rsplit(".", 1)[0]

        try:
            import time as _time
            from datetime import datetime as _dt
            from pathlib import Path as _Path
            from core.exporter import export_model_format

            _exports_dir = _Path(__file__).parent.parent / "data" / "exports"
            _exports_dir.mkdir(parents=True, exist_ok=True)

            # Curăță fișierele mai vechi de 24h
            _cutoff = _time.time() - 24 * 3600
            for _f in _exports_dir.glob("*.xlsx"):
                try:
                    if _f.stat().st_mtime < _cutoff:
                        _f.unlink()
                except Exception:
                    pass

            _auto_bytes = export_model_format(
                io.BytesIO(_file_bytes) if _file_bytes else None,
                results,
                _products,
            )
            _ts = _dt.now().strftime("%Y-%m-%d_%H-%M-%S")
            _mp_slug = selected_mp.replace(" ", "_") if selected_mp else "export"
            _auto_fname = f"{_base_name}_model.xlsx"
            _save_path = _exports_dir / f"{_ts}_{_mp_slug}_{_auto_fname}"
            _save_path.write_bytes(_auto_bytes)

            from pages.ui_helpers import section_header as _sh
            _sh("⬇️ Export rapid", "Fișierul a fost generat automat", color="#22c55e")
            st.download_button(
                f"⬇️ Descarcă {_auto_fname}",
                data=_auto_bytes,
                file_name=_auto_fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                width="stretch",
                type="primary",
            )
            st.caption(f"💾 Salvat automat local: `{_save_path}`")
        except Exception as _e:
            st.warning(f"⚠️ Auto-save eșuat: {_e}")
            st.info("Mergi la **📊 Results** pentru a descărca fișierul.")
