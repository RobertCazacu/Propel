"""
DuckDB reference store — backend unificat pentru marketplace-urile pilot.

Gestionează schema, importul, validarea și citirea datelor de referință
(categories, characteristics, values) din DuckDB local.

Marketplace-uri active: eMAG HU, Allegro.
"""
import json
import re
import time
import uuid
import threading
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import duckdb
import pandas as pd

from core.app_logger import get_logger

# Lock global — serializes all write connections on Windows (single-writer constraint)
_WRITE_LOCK = threading.Lock()

log = get_logger("marketplace.duckdb")

# ── Constante ──────────────────────────────────────────────────────────────────
EMAG_HU_ID   = "emag_hu"
EMAG_HU_NAME = "eMAG HU"
ALLEGRO_ID   = "allegro"
ALLEGRO_NAME = "Allegro"

# Map display name → marketplace_id (folosit în setup.py și state.py)
DUCKDB_ID_MAP: dict[str, str] = {
    EMAG_HU_NAME: EMAG_HU_ID,
    ALLEGRO_NAME: ALLEGRO_ID,
}

# Path absolut anchored la modul — nu relativ la cwd
DB_PATH = Path(__file__).parent.parent / "data" / "reference_data.duckdb"

# ── DDL ────────────────────────────────────────────────────────────────────────
_DDL_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS marketplaces (
        marketplace_id   VARCHAR PRIMARY KEY,
        marketplace_name VARCHAR NOT NULL,
        storage_backend  VARCHAR NOT NULL,
        is_active        BOOLEAN NOT NULL DEFAULT TRUE,
        created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS categories (
        marketplace_id     VARCHAR NOT NULL,
        category_id        VARCHAR NOT NULL,
        emag_id            VARCHAR,
        category_name      VARCHAR NOT NULL,
        parent_category_id VARCHAR,
        import_run_id      VARCHAR,
        created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS characteristics (
        marketplace_id         VARCHAR NOT NULL,
        characteristic_id      VARCHAR NOT NULL,
        emag_characteristic_id VARCHAR,
        category_id            VARCHAR NOT NULL,
        characteristic_name    VARCHAR NOT NULL,
        mandatory              BOOLEAN NOT NULL DEFAULT FALSE,
        restrictive            BOOLEAN NOT NULL DEFAULT TRUE,
        import_run_id          VARCHAR,
        created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS characteristic_values (
        marketplace_id      VARCHAR NOT NULL,
        category_id         VARCHAR,
        characteristic_id   VARCHAR,
        characteristic_name VARCHAR,
        value               VARCHAR NOT NULL,
        import_run_id       VARCHAR,
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_runs (
        import_run_id          VARCHAR PRIMARY KEY,
        marketplace_id         VARCHAR NOT NULL,
        source_type            VARCHAR NOT NULL,
        categories_source      VARCHAR,
        characteristics_source VARCHAR,
        values_source          VARCHAR,
        status                 VARCHAR NOT NULL,
        created_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        completed_at           TIMESTAMP,
        notes                  VARCHAR
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS import_issues (
        issue_id       VARCHAR PRIMARY KEY,
        import_run_id  VARCHAR NOT NULL,
        marketplace_id VARCHAR NOT NULL,
        severity       VARCHAR NOT NULL,
        issue_type     VARCHAR NOT NULL,
        entity_type    VARCHAR,
        entity_id      VARCHAR,
        message        VARCHAR NOT NULL,
        created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS product_knowledge (
        ean               VARCHAR,
        brand             VARCHAR,
        normalized_title  VARCHAR NOT NULL,
        marketplace_id    VARCHAR NOT NULL,
        offer_id          VARCHAR NOT NULL,
        category          VARCHAR NOT NULL,
        final_attributes  VARCHAR NOT NULL,
        confidence        DOUBLE NOT NULL DEFAULT 0.0,
        run_id            VARCHAR,
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS ai_run_log (
        run_id            VARCHAR NOT NULL,
        ean               VARCHAR,
        offer_id          VARCHAR,
        marketplace       VARCHAR NOT NULL,
        model_used        VARCHAR NOT NULL,
        tokens_input      INTEGER NOT NULL DEFAULT 0,
        tokens_output     INTEGER NOT NULL DEFAULT 0,
        cost_usd          DOUBLE NOT NULL DEFAULT 0.0,
        fields_requested  INTEGER NOT NULL DEFAULT 0,
        fields_accepted   INTEGER NOT NULL DEFAULT 0,
        fields_rejected   INTEGER NOT NULL DEFAULT 0,
        retry_count       INTEGER NOT NULL DEFAULT 0,
        fallback_used     BOOLEAN NOT NULL DEFAULT FALSE,
        duration_ms       INTEGER NOT NULL DEFAULT 0,
        created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS process_runs (
        run_id      VARCHAR NOT NULL,
        marketplace VARCHAR NOT NULL,
        run_ts      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        results     JSON NOT NULL
    )
    """,
]

# ── Schema migrations (backward-compatible, safe to run on existing DBs) ───────
_MIGRATIONS = [
    "ALTER TABLE characteristics ADD COLUMN IF NOT EXISTS emag_characteristic_id VARCHAR",
    "ALTER TABLE characteristics ADD COLUMN IF NOT EXISTS restrictive BOOLEAN DEFAULT TRUE",
    # Structured output telemetry columns (added in structured-output rollout)
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS structured_mode VARCHAR DEFAULT 'off'",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS structured_attempted BOOLEAN DEFAULT FALSE",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS structured_success BOOLEAN DEFAULT FALSE",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS structured_fallback_used BOOLEAN DEFAULT FALSE",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS structured_latency_ms INTEGER DEFAULT 0",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS structured_model_used VARCHAR DEFAULT ''",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS schema_fields_count INTEGER DEFAULT 0",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS shadow_diff JSON DEFAULT NULL",
    # product_knowledge: migrate marketplace column → marketplace_id + compound unique key
    "DROP INDEX IF EXISTS idx_pk_ean",
    "DROP INDEX IF EXISTS idx_pk_brand_title",
    "ALTER TABLE product_knowledge RENAME COLUMN marketplace TO marketplace_id",
    # Normalise existing records: convert display names to slugs
    "UPDATE product_knowledge SET marketplace_id = 'emag_hu' WHERE marketplace_id = 'eMAG HU'",
    "UPDATE product_knowledge SET marketplace_id = 'allegro' WHERE marketplace_id = 'Allegro'",
    "UPDATE product_knowledge SET marketplace_id = 'emag_romania' WHERE marketplace_id = 'eMAG Romania'",
    "UPDATE product_knowledge SET marketplace_id = 'trendyol' WHERE marketplace_id = 'Trendyol'",
    "UPDATE product_knowledge SET marketplace_id = 'fashiondays' WHERE marketplace_id = 'FashionDays'",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_pk_ean_mp ON product_knowledge(ean, marketplace_id)",
    "CREATE INDEX IF NOT EXISTS idx_pk_brand_title_mp ON product_knowledge(brand, normalized_title, marketplace_id)",
    # Vision fusion telemetry (added in prompt-pipeline-v2)
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS vision_signal VARCHAR DEFAULT NULL",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS vision_confidence DOUBLE DEFAULT NULL",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS fusion_action VARCHAR DEFAULT NULL",
    "ALTER TABLE ai_run_log ADD COLUMN IF NOT EXISTS conflict_flag BOOLEAN DEFAULT FALSE",
    # P11: indexes for dedup prevention on re-import and fast lookup
    "CREATE INDEX IF NOT EXISTS idx_cats_mp_cat ON categories(marketplace_id, category_id)",
    "CREATE INDEX IF NOT EXISTS idx_chars_mp_char_cat ON characteristics(marketplace_id, characteristic_id, category_id)",
    # P21: fast lookup for find_valid() queries
    "CREATE INDEX IF NOT EXISTS idx_cv_mp_charname ON characteristic_values(marketplace_id, characteristic_name)",
]

_UPSERT_MARKETPLACE = """
    INSERT INTO marketplaces (marketplace_id, marketplace_name, storage_backend, is_active)
    VALUES (?, ?, 'duckdb', TRUE)
    ON CONFLICT (marketplace_id) DO UPDATE SET
        marketplace_name = excluded.marketplace_name,
        storage_backend  = excluded.storage_backend,
        is_active        = excluded.is_active
"""


# ── Init ───────────────────────────────────────────────────────────────────────

def init_db(db_path: Path = DB_PATH) -> None:
    """
    Inițializează fișierul DB: creează directorul, tabelele și
    înregistrările tuturor marketplace-urilor DuckDB (upsert idempotent).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
        for ddl in _DDL_STATEMENTS:
            con.execute(ddl)
        _run_migrations(con)  # P28: idempotent migrations with RENAME COLUMN guard
        # Register known pilot marketplaces (backward compat).
        # New marketplaces are registered on-demand via ensure_marketplace().
        for mp_id, mp_name in [(EMAG_HU_ID, EMAG_HU_NAME), (ALLEGRO_ID, ALLEGRO_NAME)]:
            con.execute(_UPSERT_MARKETPLACE, [mp_id, mp_name])
    log.info("DuckDB initializat: %s", db_path)


def is_available(marketplace_id: str = EMAG_HU_ID, db_path: Path = DB_PATH) -> bool:
    """
    Returnează True dacă:
    1. Fișierul DB există
    2. Există cel puțin un import_run completed pentru marketplace
    3. Există cel puțin un rând în categories pentru marketplace
    """
    if not db_path.exists():
        return False
    try:
        with _WRITE_LOCK:
            with duckdb.connect(str(db_path)) as con:
                run_count = con.execute(
                    "SELECT COUNT(*) FROM import_runs WHERE marketplace_id=? AND status='completed'",
                    [marketplace_id],
                ).fetchone()[0]
                if run_count == 0:
                    return False
                cat_count = con.execute(
                    "SELECT COUNT(*) FROM categories WHERE marketplace_id=?",
                    [marketplace_id],
                ).fetchone()[0]
                return cat_count > 0
    except Exception as exc:
        log.warning("is_available check failed for %s: %s", marketplace_id, exc)
        return False


# ── Helpers ────────────────────────────────────────────────────────────────────

def _norm_id(x) -> str:
    """Normalize '2819.0' → '2819', 2819 → '2819', '2819' → '2819'."""
    if pd.isna(x) or str(x).strip() in ("", "nan", "None"):
        return ""
    try:
        return str(int(float(x)))
    except (ValueError, TypeError):
        return str(x).strip()


def marketplace_id_slug(name: str) -> str:
    """Generate a deterministic, safe VARCHAR marketplace_id from a display name.

    'eMAG HU'      → 'emag_hu'   (matches EMAG_HU_ID — no data migration needed)
    'Allegro'      → 'allegro'   (matches ALLEGRO_ID)
    'eMAG Romania' → 'emag_romania'
    'My Custom MP' → 'my_custom_mp'
    """
    slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return slug or "marketplace"


def ensure_marketplace(db_path: Path, marketplace_id: str, marketplace_name: str) -> str:
    """Upsert marketplace metadata into the DB.  Returns marketplace_id.

    Safe to call multiple times (idempotent).
    Requires the DB to be initialised first (call init_db once).
    """
    with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
        con.execute(_UPSERT_MARKETPLACE, [marketplace_id, marketplace_name])
    return marketplace_id


# ── Enrich ─────────────────────────────────────────────────────────────────────

def _enrich_values_robust(
    vals: pd.DataFrame,
    chars: pd.DataFrame,
) -> pd.DataFrame:
    """
    Completează category_id și characteristic_name per-rând în vals,
    pentru rândurile unde category_id lipsește dar characteristic_id există.

    Fix față de _enrich_values_with_chars din loader.py care se activează
    doar când TOATĂ coloana category_id este goală.
    """
    vals = vals.copy()

    needs_enrich = vals["category_id"].isna() & vals["characteristic_id"].notna()
    if not needs_enrich.any():
        return vals

    lookup = chars[["id", "category_id", "name"]].copy()
    lookup = lookup.rename(columns={"id": "_char_id", "name": "_char_name"})
    lookup["_char_id"] = lookup["_char_id"].astype(str)
    vals["characteristic_id"] = vals["characteristic_id"].astype(str)

    # Fill category_id
    to_enrich = vals[needs_enrich].merge(
        lookup, left_on="characteristic_id", right_on="_char_id", how="left"
    )
    vals.loc[needs_enrich, "category_id"] = to_enrich["category_id_y"].values

    # Fill characteristic_name unde lipsește
    char_name_missing = needs_enrich & vals["characteristic_name"].isna()
    if char_name_missing.any():
        to_fill = vals[char_name_missing].merge(
            lookup, left_on="characteristic_id", right_on="_char_id", how="left"
        )
        vals.loc[char_name_missing, "characteristic_name"] = to_fill["_char_name"].values

    return vals


# ── Validate ───────────────────────────────────────────────────────────────────

def _validate_and_create_issues(
    import_run_id: str,
    marketplace_id: str,
    cats: pd.DataFrame,
    chars: pd.DataFrame,
    vals: pd.DataFrame,
) -> list[dict]:
    """
    Validează datele importate și returnează lista de issues.
    Nu scrie în DB — insert-ul se face în import_emag_hu().
    """
    issues: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    cat_ids = set(cats["id"].apply(_norm_id).replace("", pd.NA).dropna()) if not cats.empty else set()

    # Also accept emag_id as a valid category reference (chars may use emag_id as category_id)
    cat_emag_ids = set(cats["emag_id"].apply(_norm_id).replace("", pd.NA).dropna()) if not cats.empty and "emag_id" in cats.columns else set()
    cat_ids_all = cat_ids | cat_emag_ids

    def _issue(severity, issue_type, entity_type=None, entity_id=None, message=""):
        return {
            "issue_id":       str(uuid.uuid4()),
            "import_run_id":  import_run_id,
            "marketplace_id": marketplace_id,
            "severity":       severity,
            "issue_type":     issue_type,
            "entity_type":    entity_type,
            "entity_id":      entity_id,
            "message":        message,
            "created_at":     now,
        }

    # 1. Orphan characteristics (accept both sequential id and emag_id)
    if not chars.empty:
        orphan_chars = chars[~chars["category_id"].apply(_norm_id).isin(cat_ids_all)]
        seen = set()
        for _, row in orphan_chars.iterrows():
            key = str(row.get("id", ""))
            if key not in seen:
                seen.add(key)
                issues.append(_issue(
                    "warning", "orphan_characteristic", "characteristic", key,
                    f"Characteristic '{row.get('name', '')}' are category_id="
                    f"'{row.get('category_id', '')}' care nu există în categories."
                ))

    # 2. Duplicate categories
    if not cats.empty:
        for col, label in [("id", "id"), ("name", "name")]:
            dup = cats[cats[col].duplicated(keep=False)]
            seen = set()
            for _, row in dup.iterrows():
                key = str(row[col])
                if key not in seen:
                    seen.add(key)
                    issues.append(_issue("warning", "duplicate_category", "category", key,
                                         f"Category {label} '{key}' duplicat."))

    # 3. Duplicate characteristics
    if not chars.empty:
        dup_mask = chars[["category_id", "name"]].astype(str).duplicated(keep=False)
        seen = set()
        for _, row in chars[dup_mask].iterrows():
            key = f"{row.get('category_id', '')}::{row.get('name', '')}"
            if key not in seen:
                seen.add(key)
                issues.append(_issue("warning", "duplicate_characteristic", "characteristic",
                                     str(row.get("id", "")),
                                     f"Characteristic '{row.get('name', '')}' duplicat în "
                                     f"categoria '{row.get('category_id', '')}'."))

    # 4. Mandatory characteristics fără valori (vectorizat)
    if not chars.empty:
        mandatory_truthy = {"1", "true", "True", "yes", "1.0"}
        mandatory_mask = chars["mandatory"].astype(str).isin(mandatory_truthy)
        mandatory_chars = chars[mandatory_mask].copy()
        if not mandatory_chars.empty:
            # Build set of (category_id, char_name) care au cel puțin o valoare
            if not vals.empty:
                vals_pairs = set(
                    zip(vals["category_id"].apply(_norm_id), vals["characteristic_name"].astype(str))
                )
            else:
                vals_pairs = set()
            mandatory_chars["_pair"] = list(zip(
                mandatory_chars["category_id"].apply(_norm_id),
                mandatory_chars["name"].astype(str),
            ))
            no_vals = mandatory_chars[~mandatory_chars["_pair"].isin(vals_pairs)]
            for _, row in no_vals.iterrows():
                issues.append(_issue("warning", "mandatory_no_values", "characteristic",
                                     str(row.get("id", "")),
                                     f"Caracteristica obligatorie '{row.get('name', '')}' "
                                     f"(cat '{row.get('category_id', '')}') nu are valori."))

    # 5. Values cu characteristic_name null (raportăm doar numărul total, nu per-rând)
    if not vals.empty:
        null_count = vals["characteristic_name"].isna().sum()
        if null_count > 0:
            issues.append(_issue("warning", "null_characteristic_name", "value", None,
                                 f"{null_count} rânduri fără characteristic_name "
                                 f"(vor fi ignorate la indexare)."))

    # 6. Values goale (raportăm total, nu per-rând)
    if not vals.empty:
        empty_count = (
            vals["value"].isna() | (vals["value"].astype(str).str.strip() == "")
        ).sum()
        if empty_count > 0:
            issues.append(_issue("error", "empty_value", "value", None,
                                 f"{empty_count} valori goale sau null (vor fi excluse la import)."))

    # 7. Orphan values — raportăm characteristic_name-urile unice care nu există în chars
    if not vals.empty and not chars.empty:
        known_char_names = set(chars["name"].astype(str).dropna())
        orphan_names = (
            vals.loc[vals["characteristic_name"].notna(), "characteristic_name"]
            .astype(str)
            .loc[lambda s: ~s.isin(known_char_names)]
            .unique()
        )
        for name in orphan_names:
            issues.append(_issue("warning", "orphan_value", "value", None,
                                 f"characteristic_name '{name}' nu există în characteristics."))

    return issues


# ── Import ─────────────────────────────────────────────────────────────────────

def import_marketplace(
    marketplace_id: str,
    cats_df: pd.DataFrame,
    chars_df: pd.DataFrame,
    vals_df: pd.DataFrame,
    source_type: str,
    sources: dict,
    db_path: Path = DB_PATH,
) -> str:
    """
    Importă date pentru orice marketplace DuckDB.

    Pași:
    1. Creează import_run (status=started)
    2. Enrich values per-rând
    3. Validare → colectare issues
    4. BEGIN TRANSACTION: delete old data + insert new + insert issues
    5. Update import_run → completed
    Pe excepție: update import_run → failed, re-raise.

    Returns: import_run_id
    """
    with _WRITE_LOCK:   # P06: single-writer constraint DuckDB pe Windows
        return _import_marketplace_locked(
            marketplace_id, cats_df, chars_df, vals_df,
            source_type, sources, db_path,
        )


def _import_marketplace_locked(
    marketplace_id: str,
    cats_df: pd.DataFrame,
    chars_df: pd.DataFrame,
    vals_df: pd.DataFrame,
    source_type: str,
    sources: dict,
    db_path: Path,
) -> str:
    """Implementarea internă a import_marketplace — apelată sub _WRITE_LOCK."""
    import_run_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    mandatory_truthy = {"1", "true", "True", "yes", "1.0"}

    con = duckdb.connect(str(db_path))
    try:
        # 1. Create import_run
        con.execute(
            """
            INSERT INTO import_runs
              (import_run_id, marketplace_id, source_type,
               categories_source, characteristics_source, values_source,
               status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 'started', ?)
            """,
            [
                import_run_id, marketplace_id, source_type,
                sources.get("categories"), sources.get("characteristics"), sources.get("values"),
                now,
            ],
        )

        # 2. Enrich values
        vals_enriched = _enrich_values_robust(vals_df, chars_df)

        # 3. Validate (before touching existing data)
        issues = _validate_and_create_issues(
            import_run_id, marketplace_id, cats_df, chars_df, vals_enriched
        )
        log.info(
            "Import [%s]: %d categorii, %d caracteristici, %d valori, %d issues",
            marketplace_id, len(cats_df), len(chars_df), len(vals_enriched), len(issues),
        )

        # 4. Transaction: delete old + bulk insert new
        # Pregătire DataFrames pentru bulk insert (DuckDB citește direct din pandas)
        def _norm_id_series(s: pd.Series) -> pd.Series:
            """Normalize IDs for VARCHAR storage.

            Numeric-like: 2819.0 → '2819'
            Alphanumeric: 'cat-001' → 'cat-001' (preserved as-is)
            Empty/null  : → None (SQL NULL)
            """
            normed = s.apply(_norm_id)
            return normed.where(normed != "", None)

        cats_bulk = pd.DataFrame({
            "marketplace_id":     marketplace_id,
            "category_id":        _norm_id_series(cats_df["id"]),
            "emag_id":            _norm_id_series(cats_df["emag_id"]),
            "category_name":      cats_df["name"].astype(str),
            "parent_category_id": cats_df["parent_id"].where(cats_df["parent_id"].notna(), None),
            "import_run_id":      import_run_id,
        })

        # Build emag_id → sequential id lookup for characteristics remapping.
        # The characteristics file uses cats.emag_id as category_id (eMAG external IDs),
        # while the rest of the system (categories table, values) uses cats.id (sequential).
        # We remap chars category_id to the canonical sequential id so all tables are consistent.
        _emag_to_seq: dict = {}
        if "emag_id" in cats_df.columns:
            for seq_id, emag_id in zip(
                _norm_id_series(cats_df["id"]),
                cats_df["emag_id"].apply(_norm_id),
            ):
                if emag_id and emag_id != seq_id:
                    _emag_to_seq[emag_id] = seq_id

        def _remap_cat_id(x) -> str:
            norm = _norm_id(x)
            return _emag_to_seq.get(norm, norm)

        restrictive_truthy = {"1", "True", "true", "yes", "1.0"}
        _emag_char_ids = (
            chars_df["characteristic_id"].astype(str)
            if "characteristic_id" in chars_df.columns
            else pd.Series("", index=chars_df.index)
        )
        _restrictive = (
            chars_df["restrictive"].astype(str).isin(restrictive_truthy)
            if "restrictive" in chars_df.columns
            else pd.Series(True, index=chars_df.index)
        )
        chars_bulk = pd.DataFrame({
            "marketplace_id":         marketplace_id,
            "characteristic_id":      chars_df["id"].astype(str),
            "emag_characteristic_id": _emag_char_ids,
            "category_id":            chars_df["category_id"].apply(_remap_cat_id),
            "characteristic_name":    chars_df["name"].astype(str),
            "mandatory":              chars_df["mandatory"].astype(str).isin(mandatory_truthy),
            "restrictive":            _restrictive,
            "import_run_id":          import_run_id,
        })

        vals_clean = vals_enriched[
            vals_enriched["value"].notna() &
            (vals_enriched["value"].astype(str).str.strip() != "")
        ].copy()
        vals_bulk = pd.DataFrame({
            "marketplace_id":      marketplace_id,
            "category_id":         vals_clean["category_id"].apply(_remap_cat_id).replace("", None),
            "characteristic_id":   vals_clean["characteristic_id"].where(vals_clean["characteristic_id"].notna(), None),
            "characteristic_name": vals_clean["characteristic_name"].where(vals_clean["characteristic_name"].notna(), None),
            "value":               vals_clean["value"].astype(str).str.strip(),
            "import_run_id":       import_run_id,
        })

        issues_bulk = pd.DataFrame(issues) if issues else pd.DataFrame(columns=[
            "issue_id", "import_run_id", "marketplace_id", "severity",
            "issue_type", "entity_type", "entity_id", "message", "created_at",
        ])

        con.execute("BEGIN")
        try:
            for table in ("categories", "characteristics", "characteristic_values"):
                con.execute(f"DELETE FROM {table} WHERE marketplace_id=?", [marketplace_id])

            con.register("_cats_bulk",   cats_bulk)
            con.register("_chars_bulk",  chars_bulk)
            con.register("_vals_bulk",   vals_bulk)
            con.execute("""
                INSERT INTO categories
                  (marketplace_id, category_id, emag_id, category_name,
                   parent_category_id, import_run_id)
                SELECT marketplace_id, category_id, emag_id, category_name,
                       parent_category_id, import_run_id
                FROM _cats_bulk
            """)
            con.execute("""
                INSERT INTO characteristics
                  (marketplace_id, characteristic_id, emag_characteristic_id,
                   category_id, characteristic_name, mandatory, restrictive, import_run_id)
                SELECT marketplace_id, characteristic_id, emag_characteristic_id,
                       category_id, characteristic_name, mandatory, restrictive, import_run_id
                FROM _chars_bulk
            """)
            con.execute("""
                INSERT INTO characteristic_values
                  (marketplace_id, category_id, characteristic_id,
                   characteristic_name, value, import_run_id)
                SELECT marketplace_id, category_id, characteristic_id,
                       characteristic_name, value, import_run_id
                FROM _vals_bulk
            """)
            con.unregister("_cats_bulk")
            con.unregister("_chars_bulk")
            con.unregister("_vals_bulk")

            if not issues_bulk.empty:
                con.register("_issues_bulk", issues_bulk)
                con.execute("""
                    INSERT INTO import_issues
                      (issue_id, import_run_id, marketplace_id, severity,
                       issue_type, entity_type, entity_id, message, created_at)
                    SELECT issue_id, import_run_id, marketplace_id, severity,
                           issue_type, entity_type, entity_id, message, created_at
                    FROM _issues_bulk
                """)
                con.unregister("_issues_bulk")

            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        # 5. Mark completed
        con.execute(
            "UPDATE import_runs SET status='completed', completed_at=? WHERE import_run_id=?",
            [datetime.now(timezone.utc), import_run_id],
        )
        log.info("Import [%s] completat. run_id=%s", marketplace_id, import_run_id)
        return import_run_id

    except Exception as exc:
        try:
            con.execute(
                "UPDATE import_runs SET status='failed', notes=? WHERE import_run_id=?",
                [str(exc), import_run_id],
            )
        except Exception:
            pass
        log.error("Import [%s] eșuat: %s", marketplace_id, exc, exc_info=True)
        raise
    finally:
        con.close()


def import_emag_hu(
    cats_df: pd.DataFrame,
    chars_df: pd.DataFrame,
    vals_df: pd.DataFrame,
    source_type: str,
    sources: dict,
    db_path: Path = DB_PATH,
) -> str:
    """Wrapper backward-compatible — apelează import_marketplace cu EMAG_HU_ID."""
    return import_marketplace(EMAG_HU_ID, cats_df, chars_df, vals_df, source_type, sources, db_path)


# ── Read API ───────────────────────────────────────────────────────────────────

def get_import_summary(import_run_id: str, db_path: Path = DB_PATH) -> dict:
    """Returnează statistici pentru un import_run."""
    with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
        run = con.execute(
            "SELECT status, notes FROM import_runs WHERE import_run_id=?",
            [import_run_id],
        ).fetchone()
        cats  = con.execute(
            "SELECT COUNT(*) FROM categories WHERE import_run_id=?", [import_run_id]
        ).fetchone()[0]
        chars = con.execute(
            "SELECT COUNT(*) FROM characteristics WHERE import_run_id=?", [import_run_id]
        ).fetchone()[0]
        vals  = con.execute(
            "SELECT COUNT(*) FROM characteristic_values WHERE import_run_id=?", [import_run_id]
        ).fetchone()[0]
        warnings = con.execute(
            "SELECT COUNT(*) FROM import_issues WHERE import_run_id=? AND severity='warning'",
            [import_run_id],
        ).fetchone()[0]
        errors = con.execute(
            "SELECT COUNT(*) FROM import_issues WHERE import_run_id=? AND severity='error'",
            [import_run_id],
        ).fetchone()[0]
    return {
        "categories":      cats,
        "characteristics": chars,
        "values":          vals,
        "warnings":        warnings,
        "errors":          errors,
        "status":          run[0] if run else "unknown",
        "notes":           run[1] if run else None,
    }


def get_issues(import_run_id: str, db_path: Path = DB_PATH) -> list[dict]:
    """Returnează lista de issues pentru un import_run."""
    with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
        rows = con.execute(
            """
            SELECT issue_id, severity, issue_type, entity_type, entity_id, message
            FROM import_issues
            WHERE import_run_id=?
            ORDER BY severity DESC, issue_type
            """,
            [import_run_id],
        ).fetchall()
    return [
        {
            "issue_id":    r[0],
            "severity":    r[1],
            "issue_type":  r[2],
            "entity_type": r[3],
            "entity_id":   r[4],
            "message":     r[5],
        }
        for r in rows
    ]


def load_marketplace_data(
    marketplace_id: str = EMAG_HU_ID,
    db_path: Path = DB_PATH,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Citește datele din DuckDB și le returnează în formatul așteptat de
    MarketplaceData.load_from_dataframes() → _build_indexes().

    Aliasuri obligatorii (coloana DB → coloana DataFrame):
      categories:      category_id→id, category_name→name, parent_category_id→parent_id
      characteristics: characteristic_id→id, characteristic_name→name
      characteristic_values: coloane neschimbate
    """
    with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
        cats = con.execute(
            """
            SELECT
                category_id        AS id,
                emag_id,
                category_name      AS name,
                parent_category_id AS parent_id
            FROM categories
            WHERE marketplace_id = ?
            """,
            [marketplace_id],
        ).df()

        chars = con.execute(
            """
            SELECT
                characteristic_id      AS id,
                emag_characteristic_id AS characteristic_id,
                category_id,
                characteristic_name    AS name,
                mandatory,
                restrictive
            FROM characteristics
            WHERE marketplace_id = ?
            """,
            [marketplace_id],
        ).df()

        vals = con.execute(
            """
            SELECT
                category_id,
                characteristic_id,
                characteristic_name,
                value
            FROM characteristic_values
            WHERE marketplace_id = ?
            """,
            [marketplace_id],
        ).df()

    log.info(
        "Loaded from DuckDB [%s]: %d cats, %d chars, %d vals",
        marketplace_id, len(cats), len(chars), len(vals),
    )
    return cats, chars, vals


def clear_marketplace_data(marketplace_id: str, db_path: Path = DB_PATH) -> None:
    """Șterge toate datele pentru un marketplace din DuckDB (categories, characteristics, values, import_runs)."""
    if not db_path.exists():
        return
    with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
        con.execute("DELETE FROM characteristic_values WHERE marketplace_id=?", [marketplace_id])
        con.execute("DELETE FROM characteristics WHERE marketplace_id=?", [marketplace_id])
        con.execute("DELETE FROM categories WHERE marketplace_id=?", [marketplace_id])
        con.execute("DELETE FROM import_runs WHERE marketplace_id=?", [marketplace_id])
    log.info("DuckDB: date șterse pentru marketplace_id='%s'", marketplace_id)


def get_db_status(marketplace_id: str = EMAG_HU_ID, db_path: Path = DB_PATH) -> dict:
    """
    Returnează statusul DB pentru un marketplace, pentru afișare în UI.
    Folosit de panoul de diagnosticare din setup.py.
    """
    if not db_path.exists():
        return {"available": False, "reason": "Fișierul DB nu există încă."}
    try:
        with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
            run = con.execute(
                """
                SELECT import_run_id, created_at, completed_at, status
                FROM import_runs
                WHERE marketplace_id=? AND status='completed'
                ORDER BY completed_at DESC LIMIT 1
                """,
                [marketplace_id],
            ).fetchone()
            if not run:
                return {"available": False, "reason": "Niciun import completat în DB."}
            cats  = con.execute(
                "SELECT COUNT(*) FROM categories WHERE marketplace_id=?", [marketplace_id]
            ).fetchone()[0]
            chars = con.execute(
                "SELECT COUNT(*) FROM characteristics WHERE marketplace_id=?", [marketplace_id]
            ).fetchone()[0]
            vals  = con.execute(
                "SELECT COUNT(*) FROM characteristic_values WHERE marketplace_id=?", [marketplace_id]
            ).fetchone()[0]
        return {
            "available":      True,
            "last_import_id": run[0],
            "imported_at":    str(run[2] or run[1]),
            "status":         run[3],
            "categories":     cats,
            "characteristics": chars,
            "values":         vals,
            "db_path":        str(db_path),
        }
    except Exception as exc:
        return {"available": False, "reason": str(exc)}


def _run_migrations(con) -> None:
    """Rulează migrările idempotent. P28: RENAME COLUMN are guard explicit."""
    import re as _re
    _rename_pat = _re.compile(
        r"ALTER TABLE\s+(\w+)\s+RENAME COLUMN\s+(\w+)\s+TO\s+(\w+)", _re.IGNORECASE
    )
    for migration in _MIGRATIONS:
        m = _rename_pat.search(migration)
        if m:
            table, old_col, new_col = m.group(1), m.group(2), m.group(3)
            # P28: skip RENAME if target column already exists (fresh DB or already migrated)
            try:
                cols = {
                    row[0].lower()
                    for row in con.execute(f"PRAGMA table_info({table})").fetchall()
                }
                if new_col.lower() in cols or old_col.lower() not in cols:
                    continue  # guard: already renamed or source doesn't exist
            except Exception:
                pass
        try:
            con.execute(migration)
        except Exception:
            pass  # other benign conflicts (column already exists, index exists, etc.)


def ensure_schema(db_path: Path | None = None) -> None:
    """Asigură că schema DuckDB este la zi (tabele + indecși).

    Similar cu init_db() dar fără înregistrarea marketplace-urilor.
    Potrivit pentru contexte de test și inițializare lazy.
    """
    if db_path is None:
        db_path = DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
        for ddl in _DDL_STATEMENTS:
            con.execute(ddl)
        _run_migrations(con)


# ── product_knowledge CRUD ─────────────────────────────────────────────────────

def upsert_product_knowledge(
    *,
    ean: str | None,
    brand: str,
    normalized_title: str,
    marketplace_id: str,
    offer_id: str,
    category: str,
    final_attributes: dict,
    confidence: float,
    run_id: str,
) -> None:
    """Insert sau update în product_knowledge.

    Cheia de matching: (EAN, marketplace_id) dacă EAN există, altfel brand+normalized_title+marketplace_id.
    DOAR valorile validate (care au trecut char_validator) trebuie salvate.
    """
    attrs_json = json.dumps(final_attributes, ensure_ascii=False)
    key_mode = "ean" if ean else "brand+title"
    log.debug(
        "Knowledge store UPSERT [%s]: ean=%r brand=%r mp=%r offer=%r cat=%r conf=%.2f attrs=%d",
        key_mode, ean, brand, marketplace_id, offer_id, category, confidence, len(final_attributes),
    )
    with _WRITE_LOCK:
        con = duckdb.connect(str(DB_PATH))
        try:
            if ean:
                con.execute("""
                    INSERT INTO product_knowledge
                        (ean, brand, normalized_title, marketplace_id, offer_id,
                         category, final_attributes, confidence, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (ean, marketplace_id) DO UPDATE SET
                        brand             = excluded.brand,
                        normalized_title  = excluded.normalized_title,
                        offer_id          = excluded.offer_id,
                        category          = excluded.category,
                        final_attributes  = excluded.final_attributes,
                        confidence        = excluded.confidence,
                        run_id            = excluded.run_id,
                        updated_at        = now()
                """, [ean, brand, normalized_title, marketplace_id, offer_id,
                      category, attrs_json, confidence, run_id])
            else:
                con.execute("""
                    DELETE FROM product_knowledge
                    WHERE ean IS NULL
                      AND brand = ?
                      AND normalized_title = ?
                      AND marketplace_id = ?
                """, [brand, normalized_title, marketplace_id])
                con.execute("""
                    INSERT INTO product_knowledge
                        (ean, brand, normalized_title, marketplace_id, offer_id,
                         category, final_attributes, confidence, run_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, [None, brand, normalized_title, marketplace_id, offer_id,
                      category, attrs_json, confidence, run_id])
        finally:
            con.close()
    log.debug("Knowledge store UPSERT OK [%s]: ean=%r brand=%r mp=%r", key_mode, ean, brand, marketplace_id)


def get_product_knowledge(
    *,
    ean: str | None = None,
    brand: str | None = None,
    normalized_title: str | None = None,
    marketplace_id: str | None = None,
) -> dict | None:
    """Caută în knowledge store. Prioritate: (EAN, marketplace_id) > brand+title+marketplace_id.

    Returnează dict cu toate câmpurile sau None dacă nu există.
    final_attributes este deja decodat ca dict.
    """
    lookup_mode = "ean+mp" if (ean and marketplace_id) else "brand+title+mp" if (brand and normalized_title and marketplace_id) else "skip"
    log.debug(
        "Knowledge store lookup [%s]: ean=%r brand=%r title=%r mp=%r",
        lookup_mode, ean, brand, (normalized_title or "")[:50], marketplace_id,
    )
    if lookup_mode == "skip":
        log.debug("Knowledge store lookup skipped — date insuficiente (ean=%r, brand=%r, mp=%r)", ean, brand, marketplace_id)
        return None

    t0 = time.perf_counter()
    with _WRITE_LOCK:
        t_locked = time.perf_counter()
        lock_wait_ms = round((t_locked - t0) * 1000)
        if lock_wait_ms > 50:
            log.warning("Knowledge store _WRITE_LOCK wait: %d ms (concurenta ridicata)", lock_wait_ms)
        con = duckdb.connect(str(DB_PATH))
        try:
            if ean and marketplace_id:
                row = con.execute("""
                    SELECT ean, brand, normalized_title, marketplace_id, offer_id,
                           category, final_attributes, confidence, run_id, updated_at
                    FROM product_knowledge WHERE ean = ? AND marketplace_id = ? LIMIT 1
                """, [ean, marketplace_id]).fetchone()
            elif brand and normalized_title and marketplace_id:
                row = con.execute("""
                    SELECT ean, brand, normalized_title, marketplace_id, offer_id,
                           category, final_attributes, confidence, run_id, updated_at
                    FROM product_knowledge
                    WHERE ean IS NULL AND brand = ? AND normalized_title = ? AND marketplace_id = ?
                    LIMIT 1
                """, [brand, normalized_title, marketplace_id]).fetchone()
            else:
                return None

            duration_ms = round((time.perf_counter() - t_locked) * 1000)
            if row is None:
                log.debug(
                    "Knowledge store MISS [%s]: ean=%r brand=%r mp=%r — %d ms",
                    lookup_mode, ean, brand, marketplace_id, duration_ms,
                )
                return None

            cols = ["ean", "brand", "normalized_title", "marketplace_id", "offer_id",
                    "category", "final_attributes", "confidence", "run_id", "updated_at"]
            result = dict(zip(cols, row))
            result["final_attributes"] = json.loads(result["final_attributes"])
            n_attrs = len(result["final_attributes"])
            log.info(
                "Knowledge store HIT [%s]: ean=%r brand=%r mp=%r cat=%r conf=%.2f attrs=%d — %d ms",
                lookup_mode, result.get("ean"), result.get("brand"), result.get("marketplace_id"),
                result.get("category"), result.get("confidence", 0), n_attrs, duration_ms,
            )
            return result
        except Exception as exc:
            log.error("Knowledge store lookup eroare: %s (ean=%r, brand=%r, mp=%r)", exc, ean, brand, marketplace_id)
            return None
        finally:
            con.close()


# ── ai_run_log write ───────────────────────────────────────────────────────────

def write_ai_run_log(
    *,
    run_id: str,
    ean: str | None,
    offer_id: str | None,
    marketplace: str,
    model_used: str,
    tokens_input: int,
    tokens_output: int,
    cost_usd: float,
    fields_requested: int,
    fields_accepted: int,
    fields_rejected: int,
    retry_count: int,
    fallback_used: bool,
    duration_ms: int,
    # Structured output telemetry (optional, default off)
    structured_mode: str = "off",
    structured_attempted: bool = False,
    structured_success: bool = False,
    structured_fallback_used: bool = False,
    structured_latency_ms: int = 0,
    structured_model_used: str = "",
    schema_fields_count: int = 0,
    shadow_diff: dict | None = None,
) -> None:
    """Scrie o intrare de telemetry în ai_run_log."""
    log.debug(
        "AI run log: offer=%r mp=%r model=%r req=%d acc=%d rej=%d retry=%d cost=$%.5f dur=%dms",
        offer_id, marketplace, model_used,
        fields_requested, fields_accepted, fields_rejected,
        retry_count, cost_usd, duration_ms,
    )
    with _WRITE_LOCK:
        con = duckdb.connect(str(DB_PATH))
        try:
            con.execute("""
                INSERT INTO ai_run_log
                    (run_id, ean, offer_id, marketplace, model_used,
                     tokens_input, tokens_output, cost_usd,
                     fields_requested, fields_accepted, fields_rejected,
                     retry_count, fallback_used, duration_ms,
                     structured_mode, structured_attempted, structured_success,
                     structured_fallback_used, structured_latency_ms,
                     structured_model_used, schema_fields_count, shadow_diff)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [run_id, ean, offer_id, marketplace, model_used,
                  tokens_input, tokens_output, cost_usd,
                  fields_requested, fields_accepted, fields_rejected,
                  retry_count, fallback_used, duration_ms,
                  structured_mode, structured_attempted, structured_success,
                  structured_fallback_used, structured_latency_ms,
                  structured_model_used, schema_fields_count,
                  json.dumps(shadow_diff, default=str) if shadow_diff else None])
        finally:
            con.close()


# ── Process run persistence ─────────────────────────────────────────────────────

def save_process_run(results: list, marketplace: str, db_path: Path = DB_PATH) -> None:
    """Persistă rezultatele procesării în DuckDB pentru recuperare la reload."""
    run_id = str(uuid.uuid4())
    try:
        with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
            con.execute(
                "INSERT INTO process_runs (run_id, marketplace, results) VALUES (?, ?, ?)",
                [run_id, marketplace, json.dumps(results, ensure_ascii=False, default=str)]
            )
        log.debug("Saved process run %s (%d results) for %s", run_id, len(results), marketplace)
    except Exception as exc:
        log.warning("save_process_run failed: %s", exc)


def load_last_process_run(marketplace: str, db_path: Path = DB_PATH) -> list:
    """Încarcă cel mai recent run de procesare pentru un marketplace."""
    try:
        with _WRITE_LOCK, duckdb.connect(str(db_path)) as con:
            row = con.execute(
                "SELECT results FROM process_runs WHERE marketplace = ? ORDER BY run_ts DESC LIMIT 1",
                [marketplace]
            ).fetchone()
        if row:
            return json.loads(row[0])
    except Exception as exc:
        log.warning("load_last_process_run failed: %s", exc)
    return []
