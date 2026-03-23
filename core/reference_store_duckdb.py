"""
DuckDB reference store — pilot pentru eMAG HU.

Gestionează schema, importul, validarea și citirea datelor de referință
(categories, characteristics, values) din DuckDB local.

Folosit EXCLUSIV pentru eMAG HU. Celelalte marketplace-uri continuă cu Parquet.
"""
import uuid
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import duckdb
import pandas as pd

from core.app_logger import get_logger

log = get_logger("marketplace.duckdb")

# ── Constante ──────────────────────────────────────────────────────────────────
EMAG_HU_ID   = "emag_hu"
EMAG_HU_NAME = "eMAG HU"

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
        marketplace_id      VARCHAR NOT NULL,
        characteristic_id   VARCHAR NOT NULL,
        category_id         VARCHAR NOT NULL,
        characteristic_name VARCHAR NOT NULL,
        mandatory           BOOLEAN NOT NULL DEFAULT FALSE,
        import_run_id       VARCHAR,
        created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    înregistrarea eMAG HU în tabela marketplaces (upsert idempotent).
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(db_path)) as con:
        for ddl in _DDL_STATEMENTS:
            con.execute(ddl)
        con.execute(_UPSERT_MARKETPLACE, [EMAG_HU_ID, EMAG_HU_NAME])
    log.info("DuckDB inițializat: %s", db_path)


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
        with duckdb.connect(str(db_path), read_only=True) as con:
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
    cat_ids = set(cats["id"].astype(str).dropna()) if not cats.empty else set()

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

    # 1. Orphan characteristics
    if not chars.empty:
        orphan_chars = chars[~chars["category_id"].astype(str).isin(cat_ids)]
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

    # 4. Mandatory characteristics fără valori
    if not chars.empty:
        mandatory_truthy = {"1", "true", "True", "yes", "1.0"}
        mandatory_mask = chars["mandatory"].astype(str).isin(mandatory_truthy)
        for _, row in chars[mandatory_mask].iterrows():
            cat_id  = str(row.get("category_id", ""))
            char_nm = str(row.get("name", ""))
            has_val = (
                not vals.empty
                and not vals[
                    (vals["category_id"].astype(str) == cat_id) &
                    (vals["characteristic_name"].astype(str) == char_nm)
                ].empty
            )
            if not has_val:
                issues.append(_issue("warning", "mandatory_no_values", "characteristic",
                                     str(row.get("id", "")),
                                     f"Caracteristica obligatorie '{char_nm}' (cat '{cat_id}') "
                                     f"nu are nicio valoare permisă."))

    # 5. Values cu characteristic_name null
    if not vals.empty:
        null_char = vals[vals["characteristic_name"].isna()]
        for idx, row in null_char.iterrows():
            issues.append(_issue("warning", "null_characteristic_name", "value", str(idx),
                                 f"Valoarea '{row.get('value', '')}' nu are characteristic_name "
                                 f"(va fi ignorată la indexare)."))

    # 6. Values goale
    if not vals.empty:
        empty_mask = vals["value"].isna() | (vals["value"].astype(str).str.strip() == "")
        for idx, row in vals[empty_mask].iterrows():
            issues.append(_issue("error", "empty_value", "value", str(idx),
                                 f"Valoare goală sau null la rândul {idx}."))

    # 7. Orphan values
    if not vals.empty and not chars.empty:
        known_char_names = set(chars["name"].astype(str).dropna())
        orphan_vals = vals[
            vals["characteristic_name"].notna() &
            ~vals["characteristic_name"].astype(str).isin(known_char_names)
        ]
        seen = set()
        for idx, row in orphan_vals.iterrows():
            key = str(row.get("characteristic_name", ""))
            if key not in seen:
                seen.add(key)
                issues.append(_issue("warning", "orphan_value", "value", str(idx),
                                     f"Valoarea '{row.get('value', '')}' are characteristic_name "
                                     f"'{key}' care nu există în characteristics."))

    return issues


# ── Import ─────────────────────────────────────────────────────────────────────

def import_emag_hu(
    cats_df: pd.DataFrame,
    chars_df: pd.DataFrame,
    vals_df: pd.DataFrame,
    source_type: str,
    sources: dict,
    db_path: Path = DB_PATH,
) -> str:
    """
    Importă date pentru eMAG HU în DuckDB.

    Pași:
    1. Creează import_run (status=started)
    2. Enrich values per-rând
    3. Validare → colectare issues
    4. BEGIN TRANSACTION: delete old data + insert new + insert issues
    5. Update import_run → completed
    Pe excepție: update import_run → failed, re-raise.

    Returns: import_run_id
    """
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
                import_run_id, EMAG_HU_ID, source_type,
                sources.get("categories"), sources.get("characteristics"), sources.get("values"),
                now,
            ],
        )

        # 2. Enrich values
        vals_enriched = _enrich_values_robust(vals_df, chars_df)

        # 3. Validate (before touching existing data)
        issues = _validate_and_create_issues(
            import_run_id, EMAG_HU_ID, cats_df, chars_df, vals_enriched
        )
        log.info(
            "Import eMAG HU: %d categorii, %d caracteristici, %d valori, %d issues",
            len(cats_df), len(chars_df), len(vals_enriched), len(issues),
        )

        # 4. Transaction: delete old + insert new
        con.execute("BEGIN")
        try:
            for table in ("categories", "characteristics", "characteristic_values"):
                con.execute(f"DELETE FROM {table} WHERE marketplace_id=?", [EMAG_HU_ID])

            for _, row in cats_df.iterrows():
                con.execute(
                    """
                    INSERT INTO categories
                      (marketplace_id, category_id, emag_id, category_name,
                       parent_category_id, import_run_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        EMAG_HU_ID,
                        str(row.get("id", "") or ""),
                        str(row.get("emag_id", "") or ""),
                        str(row.get("name", "") or ""),
                        str(row.get("parent_id", "") or "") or None,
                        import_run_id,
                    ],
                )

            for _, row in chars_df.iterrows():
                mandatory = str(row.get("mandatory", "0")) in mandatory_truthy
                con.execute(
                    """
                    INSERT INTO characteristics
                      (marketplace_id, characteristic_id, category_id,
                       characteristic_name, mandatory, import_run_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        EMAG_HU_ID,
                        str(row.get("id", "") or ""),
                        str(row.get("category_id", "") or ""),
                        str(row.get("name", "") or ""),
                        mandatory,
                        import_run_id,
                    ],
                )

            for _, row in vals_enriched.iterrows():
                value = str(row.get("value", "") or "").strip()
                if not value:
                    continue
                con.execute(
                    """
                    INSERT INTO characteristic_values
                      (marketplace_id, category_id, characteristic_id,
                       characteristic_name, value, import_run_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    [
                        EMAG_HU_ID,
                        str(row.get("category_id", "") or "") or None,
                        str(row.get("characteristic_id", "") or "") or None,
                        str(row.get("characteristic_name", "") or "") or None,
                        value,
                        import_run_id,
                    ],
                )

            for iss in issues:
                con.execute(
                    """
                    INSERT INTO import_issues
                      (issue_id, import_run_id, marketplace_id, severity,
                       issue_type, entity_type, entity_id, message, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        iss["issue_id"], iss["import_run_id"], iss["marketplace_id"],
                        iss["severity"], iss["issue_type"], iss["entity_type"],
                        iss["entity_id"], iss["message"], iss["created_at"],
                    ],
                )

            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise

        # 5. Mark completed
        con.execute(
            "UPDATE import_runs SET status='completed', completed_at=? WHERE import_run_id=?",
            [datetime.now(timezone.utc), import_run_id],
        )
        log.info("Import eMAG HU completat. run_id=%s", import_run_id)
        return import_run_id

    except Exception as exc:
        try:
            con.execute(
                "UPDATE import_runs SET status='failed', notes=? WHERE import_run_id=?",
                [str(exc), import_run_id],
            )
        except Exception:
            pass
        log.error("Import eMAG HU eșuat: %s", exc, exc_info=True)
        raise
    finally:
        con.close()


# ── Read API ───────────────────────────────────────────────────────────────────

def get_import_summary(import_run_id: str, db_path: Path = DB_PATH) -> dict:
    """Returnează statistici pentru un import_run."""
    with duckdb.connect(str(db_path), read_only=True) as con:
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
    with duckdb.connect(str(db_path), read_only=True) as con:
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
    with duckdb.connect(str(db_path), read_only=True) as con:
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
                characteristic_id   AS id,
                category_id,
                characteristic_name AS name,
                mandatory
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


def get_db_status(db_path: Path = DB_PATH) -> dict:
    """
    Returnează statusul DB pentru afișare în UI.
    Folosit de panoul de diagnosticare din setup.py.
    """
    if not db_path.exists():
        return {"available": False, "reason": "Fișierul DB nu există încă."}
    try:
        with duckdb.connect(str(db_path), read_only=True) as con:
            run = con.execute(
                """
                SELECT import_run_id, created_at, completed_at, status
                FROM import_runs
                WHERE marketplace_id=? AND status='completed'
                ORDER BY completed_at DESC LIMIT 1
                """,
                [EMAG_HU_ID],
            ).fetchone()
            if not run:
                return {"available": False, "reason": "Niciun import completat în DB."}
            cats  = con.execute(
                "SELECT COUNT(*) FROM categories WHERE marketplace_id=?", [EMAG_HU_ID]
            ).fetchone()[0]
            chars = con.execute(
                "SELECT COUNT(*) FROM characteristics WHERE marketplace_id=?", [EMAG_HU_ID]
            ).fetchone()[0]
            vals  = con.execute(
                "SELECT COUNT(*) FROM characteristic_values WHERE marketplace_id=?", [EMAG_HU_ID]
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
