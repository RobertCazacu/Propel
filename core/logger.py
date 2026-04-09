"""
Processing logger.
Scrie un fisier JSON per rulare in data/logs/.
Log-urile mai vechi de 7 zile se sterg automat.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

LOGS_DIR = Path(__file__).parent.parent / "data" / "logs"
MAX_AGE_DAYS = 7


# ── Cleanup ────────────────────────────────────────────────────────────────────

def cleanup_old_logs():
    """Sterge log-urile mai vechi de MAX_AGE_DAYS zile."""
    if not LOGS_DIR.exists():
        return
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    for f in LOGS_DIR.glob("*.json"):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                f.unlink()
        except Exception:
            pass


# ── Write ──────────────────────────────────────────────────────────────────────

def _reason(result: dict) -> str:
    """Construieste un mesaj de motiv pentru statusul produsului."""
    action   = result.get("action", "skip")
    err_code = result.get("error_code")

    if action == "skip" and err_code not in ("1007", "1009", "1010"):
        return f"Fara eroare (cod: {err_code or 'N/A'}) — omis"

    if result.get("needs_manual"):
        missing = result.get("missing_mandatory", [])
        if not result.get("new_category") or result.get("new_category") == result.get("original_cat", ""):
            return f"Categorie negasita (eroare {err_code})"
        if missing:
            return f"Caracteristici obligatorii lipsa: {', '.join(missing)}"
        return "Necesita verificare manuala"

    parts = []
    if action == "cat_assigned":
        parts.append(f"Categorie atribuita: {result.get('new_category', '')}")
    elif action == "cat_corrected":
        parts.append(f"Categorie corectata: {result.get('new_category', '')}")

    nc = result.get("new_chars", {})
    if nc:
        parts.append(f"{len(nc)} caracteristici completate automat")

    cl = result.get("cleared", [])
    if cl:
        parts.append(f"{len(cl)} valori invalide sterse")

    return " | ".join(parts) if parts else "Procesat fara modificari"


def write_log(
    marketplace: str,
    filename: str,
    results: list[dict],
    elapsed_s: float,
    use_ai: bool,
):
    """Scrie log-ul unei procesari in data/logs/<timestamp>.json."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_logs()

    ts      = datetime.now()
    ts_str  = ts.strftime("%Y-%m-%d_%H-%M-%S")
    ts_disp = ts.strftime("%d.%m.%Y %H:%M:%S")

    # ── Sumar ──────────────────────────────────────────────────────────────────
    total      = len(results)
    skipped    = sum(1 for r in results if r.get("action") == "skip"
                     and r.get("error_code") not in ("1007", "1009", "1010"))
    processed  = total - skipped
    cats_done  = sum(1 for r in results if r.get("action") in ("cat_assigned", "cat_corrected"))
    chars_done = sum(len(r.get("new_chars", {})) for r in results)
    cleared    = sum(len(r.get("cleared", [])) for r in results)
    manual     = sum(1 for r in results if r.get("needs_manual"))
    completed  = processed - manual

    summary = {
        "total_produse":        total,
        "cu_erori_procesate":   processed,
        "fara_erori_omise":     skipped,
        "categorii_fixate":     cats_done,
        "caracteristici_adaugate": chars_done,
        "valori_invalide_sterse":  cleared,
        "completate_automat":   completed,
        "necesita_manual":      manual,
    }

    # ── Detalii per produs ─────────────────────────────────────────────────────
    mapped   = []
    unmapped = []

    for r in results:
        action   = r.get("action", "skip")
        err_code = r.get("error_code")

        # Omite produsele fara erori (nu au fost procesate)
        if action == "skip" and err_code not in ("1007", "1009", "1010"):
            continue

        mlog = r.get("mapping_log", {})
        entry = {
            "id":            r.get("id", ""),
            "titlu":         str(r.get("title", ""))[:120],
            "eroare":        err_code,
            "actiune":       action,
            "categorie_noua": r.get("new_category", "") if action != "skip" else "",
            "caracteristici_adaugate": {k: v for k, v in r.get("new_chars", {}).items()},
            "valori_sterse": r.get("cleared", []),
            "motiv":         _reason(r),
        }

        if mlog.get("category_reason"):
            entry["motiv_categorie_nemapata"] = mlog["category_reason"]
        if mlog.get("chars_reasons"):
            entry["motiv_caracteristici_nemapate"] = mlog["chars_reasons"]

        if r.get("needs_manual"):
            entry["caracteristici_obligatorii_lipsa"] = r.get("missing_mandatory", [])
            unmapped.append(entry)
        else:
            mapped.append(entry)

    # ── Diagnostic agregat ─────────────────────────────────────────────────────
    cat_reasons: dict = {}
    char_reasons: dict = {}
    for r in results:
        mlog = r.get("mapping_log", {})
        if mlog.get("category_reason"):
            reason = mlog["category_reason"]
            cat_reasons[reason] = cat_reasons.get(reason, 0) + 1
        for char_name, reason in mlog.get("chars_reasons", {}).items():
            if char_name not in char_reasons:
                char_reasons[char_name] = {}
            char_reasons[char_name][reason] = char_reasons[char_name].get(reason, 0) + 1

    log = {
        "timestamp":    ts_disp,
        "marketplace":  marketplace,
        "fisier":       filename,
        "durata_s":     round(elapsed_s, 1),
        "ai_activat":   use_ai,
        "sumar":        summary,
        "diagnostic": {
            "motive_categorie_nemapata": cat_reasons,
            "motive_caracteristici_nemapate": char_reasons,
        },
        "mapate":       mapped,
        "nemapate":     unmapped,
    }

    out_path = LOGS_DIR / f"{ts_str}_{marketplace.replace(' ', '_')}.json"
    out_path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ── Resolver log ───────────────────────────────────────────────────────────────

def write_resolver_log(
    marketplace: str,
    filename: str,
    results: list[dict],
    base_log_path=None,  # Path returned by write_log — used to match timestamp
) -> "Path | None":
    """
    Scrie un fișier JSON dedicat resolver-ului, cu toate evenimentele de rezoluție
    din această rulare. Salvat în data/logs/ cu același timestamp ca log-ul principal.

    Returnează path-ul fișierului sau None dacă nu există evenimente resolver.
    """
    events = []
    method_counts: dict = {}

    for r in results:
        flags = r.get("new_chars", {}).get("_review_flags", {})
        if not flags:
            continue
        for char_name, meta in flags.items():
            method = meta.get("method", "none")
            method_counts[method] = method_counts.get(method, 0) + 1
            events.append({
                "product_id":    str(r.get("id", "")),
                "title":         str(r.get("title", ""))[:120],
                "char":          char_name,
                "mandatory":     char_name in r.get("missing_mandatory", []) or meta.get("value") is not None,
                "llm_value":     meta.get("llm_value", ""),
                "resolved_value": meta.get("value"),
                "method":        method,
                "score":         meta.get("score", 0.0),
                "top_k":         meta.get("top_k", []),
                "reason":        meta.get("reason", ""),
                "needs_review":  True,
            })

    if not events:
        return None

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # Reuse timestamp from base log path if available, else generate new one
    if base_log_path is not None:
        stem = Path(base_log_path).stem  # e.g. "2026-04-09_04-05-09_eMAG_BG"
        out_path = LOGS_DIR / f"{stem}_ollama_resolver.json"
    else:
        ts_str = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        out_path = LOGS_DIR / f"{ts_str}_{marketplace.replace(' ', '_')}_ollama_resolver.json"

    total_resolved   = sum(1 for e in events if e["resolved_value"] is not None)
    total_unresolved = sum(1 for e in events if e["resolved_value"] is None)

    payload = {
        "timestamp":  datetime.now().strftime("%d.%m.%Y %H:%M:%S"),
        "marketplace": marketplace,
        "fisier":     filename,
        "sumar": {
            "total_events":       len(events),
            "rezolvate":          total_resolved,
            "nerezolvate":        total_unresolved,
            "metode":             method_counts,
        },
        "events": events,
    }

    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


# ── Read ───────────────────────────────────────────────────────────────────────

def list_logs() -> list[dict]:
    """
    Returneaza lista log-urilor disponibile, sortate descrescator dupa data.
    Fiecare element: {"path": Path, "timestamp": str, "marketplace": str, "sumar": dict}
    """
    if not LOGS_DIR.exists():
        return []

    logs = []
    for f in sorted(LOGS_DIR.glob("*.json"), reverse=True):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            logs.append({
                "path":        f,
                "timestamp":   data.get("timestamp", f.stem),
                "marketplace": data.get("marketplace", ""),
                "fisier":      data.get("fisier", ""),
                "durata_s":    data.get("durata_s", 0),
                "ai_activat":  data.get("ai_activat", False),
                "sumar":       data.get("sumar", {}),
            })
        except Exception:
            pass
    return logs


def read_log(path: Path) -> dict:
    """Citeste un log complet dupa path."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
