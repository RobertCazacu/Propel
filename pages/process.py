import streamlit as st
import time
import re
import math
from core.state import all_marketplace_names, get_marketplace, get_error_codes, get_all_processable_codes
from core.offers_parser import extract_products, get_error_code
from core.processor import process_product, validate_existing, explain_missing_chars

# Prețuri claude-haiku-4-5-20251001 (per token)
_AI_PRICE_INPUT  = 0.80 / 1_000_000   # $0.80 per million input tokens
_AI_PRICE_OUTPUT = 4.00 / 1_000_000   # $4.00 per million output tokens
_AI_BATCH_SIZE   = 60


# ── Category mapping helpers (for 1007 missing-category errors) ───────────────
# These are per-marketplace and stored in session state after user confirms.

TITLE_CATEGORY_RULES_KEY = "title_cat_rules"  # prefix — se foloseste per-marketplace: f"{KEY}_{mp_name}"


def _default_title_rules() -> list[dict]:
    """
    Reguli bazate pe cuvinte cheie prezente ORIUNDE in titlu (AND logic).
    Camp 'keywords': cuvinte separate prin virgula, TOATE trebuie sa apara in titlu.
    Camp 'exclude': cuvinte care NU trebuie sa apara (optional).
    Reguli cu mai multe keywords = mai specifice = verificate primele.
    """
    return [
        # Treninguri / Kituri
        {"keywords": "kit, copii",              "category": "Treninguri lifestyle copii"},
        {"keywords": "trening, copii",          "category": "Treninguri lifestyle copii"},
        {"keywords": "trening, barbati",        "category": "Treninguri lifestyle barbati"},
        {"keywords": "trening, femei",          "category": "Treninguri lifestyle femei"},
        # Tricouri
        {"keywords": "tricou, copii",           "category": "Tricouri sport copii"},
        {"keywords": "tricou, baieti",          "category": "Tricouri sport copii"},
        {"keywords": "tricou, fete",            "category": "Tricouri sport copii"},
        {"keywords": "tricou, barbati",         "category": "Tricouri sport barbati"},
        {"keywords": "tricou, femei",           "category": "Tricouri sport femei"},
        {"keywords": "tricou, dama",            "category": "Tricouri sport femei"},
        # Hanorace
        {"keywords": "hanorac, copii",          "category": "Hanorace sport copii"},
        {"keywords": "hanorac, barbati",        "category": "Hanorace sport barbati"},
        {"keywords": "hanorac, femei",          "category": "Hanorace sport femei"},
        # Pantaloni / Sorturi
        {"keywords": "pantalon, copii",         "category": "Pantaloni sport copii"},
        {"keywords": "pantalon, barbati",       "category": "Pantaloni sport barbati"},
        {"keywords": "pantalon, femei",         "category": "Pantaloni sport femei"},
        {"keywords": "sort, barbati",           "category": "Pantaloni sport barbati"},
        {"keywords": "sort, copii",             "category": "Pantaloni sport copii"},
        # Genti / Rucsacuri
        {"keywords": "ghiozdan",               "category": "Ghiozdane și rucsacuri școală"},
        {"keywords": "rucsac",                  "category": "Rucsacuri"},
        {"keywords": "geanta, sport",           "category": "Rucsacuri"},
        {"keywords": "borseta",                 "category": "Rucsacuri"},
        # Accesorii
        {"keywords": "sapca",                   "category": "Sepci si bandane sport barbati"},
        {"keywords": "caciula, barbati",        "category": "Caciuli sport barbati"},
        {"keywords": "caciula, copii",          "category": "Caciuli sport copii"},
        {"keywords": "caciula",                 "category": "Caciuli sport barbati"},
        {"keywords": "sosete, barbati",         "category": "Sosete sport barbati"},
        {"keywords": "sosete, femei",           "category": "Sosete sport femei"},
        {"keywords": "sosete, copii",           "category": "Sosete sport copii"},
        {"keywords": "sosete",                  "category": "Sosete sport barbati"},
        {"keywords": "manusi",                  "category": "Manusi sport"},
        # Mingi
        {"keywords": "minge",                   "category": "Mingi"},
    ]


def _rule_keywords(rule: dict) -> list[str]:
    """Extrage lista de cuvinte cheie dintr-o regula (suporta format nou si vechi)."""
    raw = rule.get("keywords", rule.get("prefix", ""))
    return [k.strip().lower() for k in re.split(r"[,\s]+", raw) if k.strip()]


def _resolve_category(title: str, current_cat: str, mp, rules: list) -> tuple[str, str]:
    """
    Returns (final_category, action) where action is one of:
    'ok', 'assigned', 'corrected', 'unknown'

    Matching logic: ALL keywords trebuie sa apara oriunde in titlu (case-insensitive).
    Regulile cu mai multe keywords sunt verificate primele (mai specifice).
    """
    # Pasul 1 — categoria curenta exista in marketplace-ul activ
    if current_cat and mp.category_id(current_cat):
        return current_cat, "ok"

    # Pasul 2 — categoria curenta NU exista in MP-ul activ (e din alt marketplace)
    # o ignoram complet si determinam din titlu cu regulile MP-ului curent
    title_lower = title.lower()

    # Sorteaza: mai multe keywords = mai specific = prioritate mai mare
    sorted_rules = sorted(rules, key=lambda r: -len(_rule_keywords(r)))

    for rule in sorted_rules:
        cat      = rule.get("category", "")
        keywords = _rule_keywords(rule)
        exclude  = [k.strip().lower() for k in rule.get("exclude", "").split(",") if k.strip()]

        if not keywords or not cat:
            continue
        # Verifica ca si categoria din regula exista in MP-ul curent
        if not mp.category_id(cat):
            continue
        if all(kw in title_lower for kw in keywords):
            if not any(ex in title_lower for ex in exclude):
                return cat, "assigned"

    return "", "unknown"


def _estimate_ai_cost(to_process: list, mp, rules: list) -> dict:
    """
    Estimează tokenii și costul API înainte de procesare.
    - Category AI: produse fără categorie valabilă → batch calls
    - Char AI: produse cu categorii valabile dar cu caracteristici obligatorii lipsă → per-product calls
    """
    # ── Category AI ──────────────────────────────────────────────────────────
    needs_cat_ai = [
        p for p in to_process
        if _resolve_category(str(p.get("name") or ""), str(p.get("category") or ""), mp, rules)[1] == "unknown"
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
        _, action = _resolve_category(title, cat, mp, rules)
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


def _process_all(products, mp, rules, progress_bar, status_text, use_ai=False, marketplace=""):
    results = []
    total = len(products)
    processable_codes = get_all_processable_codes(marketplace)

    # ── Pre-procesare batch categorie (1 apel API pentru toate produsele fara categorie) ─
    if use_ai:
        try:
            from core.ai_enricher import suggest_categories_batch, get_learned_rules, is_configured
            if is_configured():
                # Adauga regulile invatate automat din rulari anterioare
                learned = get_learned_rules()
                existing_kws = {r.get("keywords", r.get("prefix", "")) for r in rules}
                for r in learned:
                    kw = r.get("keywords", r.get("prefix", ""))
                    if kw and kw not in existing_kws:
                        rules = rules + [r]

                # Colecteaza produsele care au nevoie de AI pentru categorie
                needs_ai_cat = []
                for prod in products:
                    err_code = get_error_code(str(prod.get("error") or ""))
                    if err_code not in processable_codes:
                        continue
                    cat = str(prod.get("category") or "")
                    title = str(prod.get("name") or "")
                    # Verifica daca categoria e deja rezolvabila fara AI
                    _, cat_action = _resolve_category(title, cat, mp, rules)
                    if cat_action == "unknown":
                        needs_ai_cat.append({
                            "id": prod.get("id") or title,
                            "title": title,
                            "description": str(prod.get("description") or ""),
                        })

                if needs_ai_cat:
                    ai_cat_map = suggest_categories_batch(
                        needs_ai_cat,
                        mp.category_list(),
                        marketplace=marketplace,
                        status_callback=lambda msg: status_text.text(msg),
                    )
                    # Injecteaza categoriile in produse
                    for prod in products:
                        pid = prod.get("id") or str(prod.get("name") or "")
                        if pid in ai_cat_map and ai_cat_map[pid]:
                            prod["_ai_category"] = ai_cat_map[pid]
        except Exception as e:
            status_text.text(f"Avertisment batch AI: {e}")

    # Bad values patterns to clear
    SIZE_INTL = {"S INTL", "M INTL", "L INTL", "XL INTL", "XXL INTL",
                 "XS INTL", "2XL INTL", "10XL INTL", "3XL INTL"}

    for i, prod in enumerate(products):
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
            "new_category": cat,
            "cleared":      [],
            "new_chars":    {},
            "needs_manual": False,
            "mapping_log":  {},
        }

        if err_code not in processable_codes:
            results.append(result)
            continue

        # ── Fix category ──────────────────────────────────────────────────────
        final_cat, cat_action = _resolve_category(title, cat, mp, rules)

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
                    f"Nicio regulă din {len(rules)} nu s-a potrivit cu titlul (AI dezactivat)"
                )

            if not final_cat or not mp.category_id(final_cat):
                result["needs_manual"] = True
                results.append(result)
                continue

        cat_id = mp.category_id(final_cat)

        # ── Clear invalid existing values ─────────────────────────────────────
        invalid = validate_existing(existing, final_cat, mp)
        cleared = []
        for char_name, bad_val in invalid.items():
            str_val = str(bad_val).strip()
            # Always clear: size value in Culoare field
            if char_name == "Culoare:" and str_val in SIZE_INTL:
                cleared.append(char_name)
                existing[char_name] = None
            # Clear invalid Marime convertita / Stil
            elif char_name in ("Marime convertita", "Stil:", "Marime:"):
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
        new_chars = process_product(title, desc, final_cat, existing, mp, use_ai=use_ai, marketplace=marketplace, offer_id=str(prod.get("id", "")))
        result["new_chars"] = new_chars

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

        results.append(result)

        # Update progress
        pct = int((i + 1) / total * 100)
        progress_bar.progress(pct)
        status_text.text(f"Procesare: {i+1}/{total} produse...")

    return results


# ── Page render ────────────────────────────────────────────────────────────────

def render():
    st.title("📁 Procesare Oferte")
    st.markdown("Încarcă fișierul de oferte, selectează marketplace-ul și pornește procesarea automată.")
    st.markdown("---")

    # ── Step 1: Select marketplace ────────────────────────────────────────────
    mp_names = all_marketplace_names()
    loaded   = [n for n in mp_names if get_marketplace(n) and get_marketplace(n).is_loaded()]

    if not loaded:
        st.warning("⚠️ Niciun marketplace configurat. Mergi la **⚙️ Setup Marketplace** mai întâi.")
        return

    st.subheader("1️⃣ Selectează marketplace")
    selected_mp = st.selectbox("Marketplace", loaded, key="proc_mp")
    mp = get_marketplace(selected_mp)
    _rules_key = f"{TITLE_CATEGORY_RULES_KEY}_{selected_mp}"  # reguli separate per marketplace

    stats = mp.stats()
    st.markdown(
        f"<div style='background:#1e3a5f;border-left:5px solid #4da6ff;padding:10px 16px;"
        f"border-radius:4px;margin:4px 0 12px 0'>"
        f"<span style='color:#4da6ff;font-size:13px;font-weight:600;letter-spacing:0.5px'>"
        f"MARKETPLACE ACTIV</span><br>"
        f"<span style='color:#ffffff;font-size:20px;font-weight:700'>{selected_mp}</span>"
        f"<span style='color:#aaaaaa;font-size:12px;margin-left:12px'>"
        f"{stats['categories']} categorii · {stats['characteristics']} caracteristici · {stats['values']} valori</span>"
        f"</div>",
        unsafe_allow_html=True,
    )

    # ── Step 2: Upload offers file ─────────────────────────────────────────────
    st.subheader("2️⃣ Încarcă fișierul de oferte")

    from core.templates import offers_template
    col_dl, col_info = st.columns([1, 3])
    with col_dl:
        st.download_button(
            "⬇️ Descarcă model oferte",
            data=offers_template(),
            file_name="model_offers.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="dl_offers",
            use_container_width=True,
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
        import io
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

        col1, col2, col3 = st.columns(3)
        col1.metric("Total produse", len(products))
        col2.metric(f"De procesat (cod: {codes_str})", proc_count)
        col3.metric("Alte erori / fără eroare", other_count)

    # ── Step 3: Category mapping rules ────────────────────────────────────────
    with st.expander("⚙️ Reguli mapare categorii (pentru erori 1007)", expanded=False):
        st.markdown(
            "Definește reguli bazate pe **cuvinte cheie** prezente oriunde în titlu.\n\n"
            "- **Cuvinte cheie**: separate prin virgulă — **TOATE** trebuie să apară în titlu\n"
            "- **Excludere** *(opțional)*: cuvinte care **NU** trebuie să apară\n"
            "- Regulile cu mai multe cuvinte cheie au prioritate (mai specifice)\n\n"
            "**Exemple:** `tricou, barbati` prinde orice titlu cu ambele cuvinte, indiferent de ordine sau brand."
        )

        if _rules_key not in st.session_state:
            # Regulile implicite cu categorii romanesti se aplica DOAR pentru eMAG Romania
            st.session_state[_rules_key] = _default_title_rules() if "Romania" in selected_mp else []

        rules = st.session_state[_rules_key]

        cat_list = mp.category_list() if mp else []
        updated_rules = []
        for i, rule in enumerate(rules):
            c1, c2, c3, c4 = st.columns([3, 2, 3, 1])
            # Suport format vechi (prefix) si nou (keywords)
            kw_val = rule.get("keywords", rule.get("prefix", ""))
            keywords = c1.text_input(
                f"Cuvinte cheie #{i+1}", value=kw_val, key=f"rule_kw_{i}",
                help="Ex: tricou, barbati — TOATE trebuie în titlu"
            )
            exclude = c2.text_input(
                f"Excludere #{i+1}", value=rule.get("exclude", ""), key=f"rule_ex_{i}",
                help="Ex: copii — dacă apare, regula nu se aplică"
            )
            category = c3.selectbox(
                f"Categorie #{i+1}", options=[""] + cat_list,
                index=(cat_list.index(rule["category"]) + 1) if rule.get("category") in cat_list else 0,
                key=f"rule_cat_{i}"
            )
            keep = not c4.button("🗑", key=f"rule_del_{i}")
            if keep:
                updated_rules.append({"keywords": keywords, "exclude": exclude, "category": category})

        st.session_state[_rules_key] = updated_rules

        c1, c2 = st.columns([3, 1])
        if c2.button("➕ Adaugă regulă"):
            st.session_state[_rules_key].append({"keywords": "", "exclude": "", "category": ""})
            st.rerun()

    # ── Step 4: Process ────────────────────────────────────────────────────────
    st.subheader("3️⃣ Procesare")

    products = st.session_state.get("offers_products", [])
    rules    = st.session_state.get(_rules_key, _default_title_rules() if "Romania" in selected_mp else [])

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
                "Vor fi ignorate și sistemul va încerca să le determine din titlu folosind regulile de mai sus."
            )
            rows = [{"Categorie invalidă": cat, "Nr. produse": n}
                    for cat, n in audit["invalid_cats"].items()]
            st.dataframe(rows, use_container_width=True, hide_index=True)
    else:
        st.success(f"Toate cele {audit['valid']} produse au categorii valide pentru {selected_mp}.")

    processable = get_all_processable_codes(selected_mp)
    to_process = [p for p in products
                  if get_error_code(str(p.get("error") or "")) in processable]
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
    if use_ai and to_process:
        est = _estimate_ai_cost(to_process, mp, rules)
        with st.expander("💰 Cost estimativ API Claude", expanded=True):
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Produse → categorie AI", est["n_cat_ai"],
                      help=f"{est['n_batches']} batch-uri × max {_AI_BATCH_SIZE} produse/batch")
            c2.metric("Batches categorii", est["n_batches"])
            c3.metric("Produse → caractere AI", est["n_char_ai"],
                      help="Produse cu categorii valabile și câmpuri obligatorii lipsă")
            c4.metric("Cost estimativ", f"~${est['cost_usd']:.4f}",
                      help="Estimare pe baza prețurilor claude-haiku-4-5-20251001")

            st.caption(
                f"Tokeni estimați: **{est['total_input']:,}** input"
                f" ({est['cat_input']:,} categ. + {est['char_input']:,} caractere)"
                f" · **{est['total_output']:,}** output"
                f" ({est['cat_output']:,} categ. + {est['char_output']:,} caractere)"
                f"  ·  Preț model: $0.80/MTok input · $4.00/MTok output"
            )
            if est["n_cat_ai"] == 0:
                st.success("✅ Toate categoriile sunt rezolvabile fără AI — cost categorizare: $0.00")
            if est["cost_usd"] < 0.001:
                st.info("💡 Cost sub $0.001 — practic gratuit pentru acest număr de oferte.")

    if st.button(f"🚀 Pornește procesarea pentru {selected_mp}", type="primary", use_container_width=True):
        progress = st.progress(0)
        status   = st.empty()
        start    = time.time()

        results = _process_all(products, mp, rules, progress, status, use_ai=use_ai, marketplace=selected_mp)

        elapsed = time.time() - start
        st.session_state["process_results"] = results
        st.session_state["process_results_mp"] = selected_mp  # marketplace folosit la procesare

        from core.state import save_dashboard_stats
        save_dashboard_stats(results)

        from core.logger import write_log
        file_name = st.session_state.get("offers_file_name", "necunoscut.xlsx")
        write_log(selected_mp, file_name, results, elapsed, use_ai)

        # Summary
        n_cat_assigned  = sum(1 for r in results if r["action"] == "cat_assigned")
        n_cat_corrected = sum(1 for r in results if r["action"] == "cat_corrected")
        n_chars_added   = sum(len(r["new_chars"]) for r in results)
        n_cleared       = sum(len(r["cleared"]) for r in results)
        n_manual        = sum(1 for r in results if r.get("needs_manual"))

        status.empty()
        progress.empty()

        st.success(f"✅ Procesare completă în {elapsed:.1f}s!")

        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Categorii atribuite",   n_cat_assigned)
        col2.metric("Categorii corectate",   n_cat_corrected)
        col3.metric("Caracteristici adăugate", n_chars_added)
        col4.metric("Valori invalide șterse", n_cleared)
        col5.metric("Necesită review manual", n_manual)

        st.info("Mergi la **📊 Results** pentru a revizui și descărca fișierul.")
