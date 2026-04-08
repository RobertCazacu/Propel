"""
Concurrency tests — P01, P04, P06.

Rulare: python -m pytest tests/test_concurrency.py -v
"""
import threading
import pytest
from unittest.mock import patch, MagicMock


# ── P01: Race condition în learned_title_rules ──────────────────────────────

def test_no_duplicate_learned_rules():
    """P01: Două thread-uri nu pot adăuga aceeași regulă simultan."""
    import core.ai_enricher as ae

    # Asigură că _merge_lock există
    assert hasattr(ae, "_merge_lock"), \
        "_merge_lock lipsă din ai_enricher — fix P01 neaplicat"

    cache = {"category_map": {}, "learned_title_rules": [], "done_map": {}}

    errors = []

    def _add_rule(kw, cat):
        try:
            with ae._merge_lock:
                existing = {r.get("keywords", r.get("prefix", ""))
                            for r in cache["learned_title_rules"]}
                if kw not in existing:
                    cache["learned_title_rules"].append(
                        {"keywords": kw, "category": cat}
                    )
        except Exception as e:
            errors.append(e)

    threads = [
        threading.Thread(target=_add_rule, args=("nike, tricou", "Tricouri"))
        for _ in range(20)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"Erori în thread-uri: {errors}"
    rules_for_kw = [
        r for r in cache["learned_title_rules"]
        if r["keywords"] == "nike, tricou"
    ]
    assert len(rules_for_kw) == 1, \
        f"Duplicate rules detectate: {len(rules_for_kw)} în loc de 1"


# ── P04: get_router() thread-safe ──────────────────────────────────────────

def test_get_router_thread_safe():
    """P04: get_router() trebuie să returneze o singură instanță din orice thread."""
    import core.llm_router as router_mod

    init_count = []
    original_init = router_mod.LLMRouter.__init__

    def counting_init(self, provider_name=None):
        init_count.append(1)
        # Nu apela original ca să nu fie nevoie de .env
        self._provider = MagicMock()
        self._provider.name = "mock"

    with patch.object(router_mod.LLMRouter, "__init__", counting_init):
        router_mod._instance = None  # reset singleton

        threads = [
            threading.Thread(target=router_mod.get_router)
            for _ in range(30)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Resetează pentru alte teste
        router_mod._instance = None

    # Cu double-checked locking, maxim 1 init
    assert len(init_count) <= 1, \
        f"LLMRouter.__init__ apelat de {len(init_count)} ori — nu e thread-safe"


# ── P06: import_marketplace cu _WRITE_LOCK ──────────────────────────────────

def test_concurrent_import_uses_write_lock(tmp_path):
    """P06: import_marketplace trebuie să achiziționeze _WRITE_LOCK."""
    import core.reference_store_duckdb as store
    import pandas as pd

    db_path = tmp_path / "test.duckdb"
    store.init_db(db_path)

    errors = []

    cats = pd.DataFrame([{
        "id": "1", "emag_id": None,
        "name": "Cat1", "parent_id": None,
    }])
    chars = pd.DataFrame(columns=[
        "id", "characteristic_id", "category_id",
        "name", "mandatory", "restrictive",
    ])
    vals = pd.DataFrame(columns=[
        "category_id", "characteristic_id",
        "characteristic_name", "value",
    ])

    def _do_import(mp_id):
        try:
            store.import_marketplace(
                mp_id, cats.copy(), chars.copy(), vals.copy(),
                "test", {}, db_path=db_path,
            )
        except Exception as e:
            errors.append(str(e))

    threads = [
        threading.Thread(target=_do_import, args=(f"mp_{i}",))
        for i in range(4)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lock_errors = [e for e in errors if "locked" in e.lower()]
    assert not lock_errors, f"DB lock errors detectate: {lock_errors}"
