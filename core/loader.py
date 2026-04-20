"""
Marketplace data loader.
Handles Categories, Characteristics and Values Excel/CSV files
with flexible column detection (tolerant of extra/missing columns).
Accepts both Streamlit file-like objects and local file paths (str/Path).
"""
import re
import unicodedata
import difflib
import pandas as pd
import json
from pathlib import Path
from collections import defaultdict
from typing import Optional, Union
from core.app_logger import get_logger

log = get_logger("marketplace.loader")


def _normalize_str(s: str) -> str:
    """Strip diacritics and lowercase for fuzzy matching (ș→s, ő→o, etc.)."""
    nfkd = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def _normalize_char_name(s: str) -> str:
    """Canonical lookup key for characteristic names.

    Maps 'Culoare:' and 'Culoare' and '  culoare  ' all to the same key 'culoare'.
    Used only for matching; output always uses the display name from the
    characteristics table.
    Strips diacritics so 'Mărime:' matches 'Marime:'.
    """
    s = re.sub(r'\s+', ' ', str(s).strip())
    s = s.rstrip(':').strip().casefold()
    return _normalize_str(s)


def _read_tabular(file_or_path: Union[str, Path, object]) -> pd.DataFrame:
    """
    Read a tabular file (Excel or CSV) from either:
    - a local path (str or Path) — no size limit
    - a Streamlit file-like object (UploadedFile)

    CSV detection is based on file extension (.csv, .tsv).
    TSV files are read with tab separator.

    For Excel files with multiple sheets: automatically concatenates all sheets
    that share the same column schema as the first data sheet (skips SQL/query
    sheets where the first column header looks like a SQL statement).
    """
    if isinstance(file_or_path, (str, Path)):
        p = Path(file_or_path)
        if not p.exists():
            raise FileNotFoundError(f"Fișierul nu există: {p}")
        ext = p.suffix.lower()
        if ext == ".csv":
            return pd.read_csv(p, dtype=str, encoding_errors="replace")
        if ext == ".tsv":
            return pd.read_csv(p, sep="\t", dtype=str, encoding_errors="replace")
        return _read_excel_all_sheets(p)
    else:
        # Streamlit UploadedFile or any file-like object
        name = getattr(file_or_path, "name", "") or ""
        ext = Path(name).suffix.lower()
        if ext == ".csv":
            return pd.read_csv(file_or_path, dtype=str, encoding_errors="replace")
        if ext == ".tsv":
            return pd.read_csv(file_or_path, sep="\t", dtype=str, encoding_errors="replace")
        return _read_excel_all_sheets(file_or_path)


def _read_excel_all_sheets(source) -> pd.DataFrame:
    """
    Read an Excel file and concatenate all sheets that share the same column
    schema as the first data sheet.  Skips sheets whose first column header
    contains SQL keywords (export artefacts like 'SELECT …').
    """
    try:
        import python_calamine  # noqa: F401
        xl = pd.ExcelFile(source, engine="calamine")
    except ImportError:
        xl = pd.ExcelFile(source)
    if len(xl.sheet_names) == 1:
        return xl.parse(xl.sheet_names[0]).dropna(how="all")

    dfs: list[pd.DataFrame] = []
    ref_cols: Optional[list] = None

    for sheet in xl.sheet_names:
        try:
            df = xl.parse(sheet).dropna(how="all")
        except Exception:
            continue
        if df.empty:
            continue
        # Skip SQL/query sheets (first column header looks like a SQL statement)
        first_col = str(df.columns[0]).strip().upper()
        if first_col.startswith("SELECT") or first_col.startswith("--") or first_col.startswith("/*"):
            continue
        cols = list(df.columns)
        if ref_cols is None:
            ref_cols = cols
            dfs.append(df)
        elif cols == ref_cols:
            dfs.append(df)
        else:
            log.warning("Sheet '%s' sărit: schema diferită de primul sheet (coloane: %s)", sheet, cols)

    if not dfs:
        return xl.parse(xl.sheet_names[0])
    if len(dfs) == 1:
        return dfs[0]

    result = pd.concat(dfs, ignore_index=True)
    log.info("Excel multi-sheet concat: %d sheets → %d rânduri total", len(dfs), len(result))
    return result


# ── Column aliases ─────────────────────────────────────────────────────────────
# Each list = accepted column names (case-insensitive) for that field
CAT_COL_ALIASES = {
    "id":        ["id", "category_id", "cat_id", "id_categorie"],
    "emag_id":   ["emag_id", "marketplace_id", "external_id", "id_extern"],
    "name":      ["name", "name_ro", "nume", "denumire", "category_name"],
    "parent_id": ["parent_id", "parent", "id_parinte"],
}

CHAR_COL_ALIASES = {
    "id":                ["id", "char_id", "id_caracteristica"],
    "characteristic_id": ["characteristic_id", "emag_characteristic_id", "char_emag_id"],
    "category_id":       ["category_id", "cat_id", "id_categorie"],
    "name":              ["name", "name_ro", "nume", "characteristic_name", "denumire"],
    "mandatory":         ["mandatory", "obligatoriu", "required", "is_mandatory"],
    "restrictive":       ["restrictive", "is_restrictive", "restrictiv"],
}

VAL_COL_ALIASES = {
    "category_id":        ["category_id", "cat_id"],
    "characteristic_id":  ["characteristic_id", "char_id", "emag_characteristic_id"],
    "characteristic_name":["characteristic_name", "name", "char_name", "name_ro", "denumire"],
    "value":              ["label", "value", "value_ro", "valoare", "val", "val_ro"],
}


def _find_col(df: pd.DataFrame, aliases: list[str]) -> Optional[str]:
    """Find the first matching column name (case-insensitive)."""
    lower_map = {c.lower(): c for c in df.columns}
    for alias in aliases:
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def _map_cols(df: pd.DataFrame, alias_dict: dict) -> dict:
    """Return {field: actual_col} for each field in alias_dict."""
    return {field: _find_col(df, aliases) for field, aliases in alias_dict.items()}


def _autodetect_external_id_col(df: pd.DataFrame, already_mapped: dict) -> Optional[str]:
    """Auto-detect the marketplace external ID column.

    Looks for any column whose name ends with '_id' (case-insensitive) that has
    not already been mapped to 'id' or 'parent_id'. Works for any marketplace:
    trendyol_id, emag_id, allegro_id, trendyolbg_id, etc.
    """
    reserved = {v.lower() for v in already_mapped.values() if v}
    lower_map = {c.lower(): c for c in df.columns}
    for col_lower, col_orig in lower_map.items():
        if col_lower.endswith("_id") and col_lower not in reserved:
            log.info(
                "load_categories: coloana emag_id auto-detectată: '%s'", col_orig
            )
            return col_orig
    return None


def load_categories(file) -> pd.DataFrame:
    """Load categories file. Returns DataFrame with standardised columns."""
    df = _read_tabular(file)
    mapping = _map_cols(df, CAT_COL_ALIASES)
    for col in ("id", "name"):
        if not mapping[col]:
            log.warning(
                "load_categories: coloana critică '%s' nu a fost găsită. Aliasuri încercate: %s",
                col, CAT_COL_ALIASES[col],
            )
    # If no explicit emag_id alias matched, auto-detect from any remaining *_id column.
    # This handles trendyol_id, allegro_id, trendyolbg_id, etc. without hardcoding.
    if not mapping["emag_id"]:
        mapping["emag_id"] = _autodetect_external_id_col(df, {
            "id": mapping["id"], "parent_id": mapping["parent_id"]
        })
    result = pd.DataFrame()
    result["id"]        = df[mapping["id"]]        if mapping["id"]        else None
    result["emag_id"]   = df[mapping["emag_id"]]   if mapping["emag_id"]   else result["id"]
    result["name"]      = df[mapping["name"]]      if mapping["name"]      else None
    result["parent_id"] = df[mapping["parent_id"]] if mapping["parent_id"] else None
    total_before = len(result)
    result = result.dropna(subset=["id", "name"])
    eliminated = total_before - len(result)
    if eliminated > 0:
        log.warning(
            "load_categories: %d rânduri eliminate din %d total (lipsă id sau name). Rămân: %d",
            eliminated, total_before, len(result),
        )
    return result


def load_characteristics(file) -> pd.DataFrame:
    """Load characteristics file."""
    df = _read_tabular(file)
    mapping = _map_cols(df, CHAR_COL_ALIASES)
    for col in ("category_id", "name"):
        if not mapping[col]:
            log.warning(
                "load_characteristics: coloana critică '%s' nu a fost găsită. Aliasuri încercate: %s",
                col, CHAR_COL_ALIASES[col],
            )
    result = pd.DataFrame()
    result["id"]                = df[mapping["id"]]                if mapping["id"]                else range(len(df))
    result["characteristic_id"] = df[mapping["characteristic_id"]] if mapping["characteristic_id"] else result["id"]
    result["category_id"]       = df[mapping["category_id"]]       if mapping["category_id"]       else None
    result["name"]              = df[mapping["name"]]              if mapping["name"]              else None
    result["mandatory"]         = df[mapping["mandatory"]]         if mapping["mandatory"]         else 0
    result["restrictive"]       = df[mapping["restrictive"]]       if mapping["restrictive"]       else 1
    total_before = len(result)
    result = result.dropna(subset=["category_id", "name"])
    eliminated = total_before - len(result)
    if eliminated > 0:
        log.warning(
            "load_characteristics: %d rânduri eliminate din %d total (lipsă category_id sau name). Rămân: %d",
            eliminated, total_before, len(result),
        )
    return result


def load_values(file) -> pd.DataFrame:
    """Load characteristic values file."""
    df = _read_tabular(file)
    mapping = _map_cols(df, VAL_COL_ALIASES)
    result = pd.DataFrame()
    result["category_id"]        = df[mapping["category_id"]]        if mapping["category_id"]        else None
    result["characteristic_id"]  = df[mapping["characteristic_id"]]  if mapping["characteristic_id"]  else None
    result["characteristic_name"]= df[mapping["characteristic_name"]] if mapping["characteristic_name"] else None
    result["value"]               = df[mapping["value"]]               if mapping["value"]               else None
    # Drop only on value — category_id may be filled later via join with characteristics
    return result.dropna(subset=["value"])


def _enrich_values_with_chars(vals: pd.DataFrame, chars: pd.DataFrame) -> pd.DataFrame:
    """
    When values file has no category_id, join with characteristics on characteristic_id
    to obtain category_id and characteristic_name.
    """
    lookup = chars[["id", "category_id", "name"]].copy()
    lookup = lookup.rename(columns={"id": "_char_id", "name": "_char_name"})
    lookup["_char_id"] = pd.to_numeric(lookup["_char_id"], errors="coerce")

    vals = vals.copy()
    vals["characteristic_id"] = pd.to_numeric(vals["characteristic_id"], errors="coerce")

    merged = vals.merge(lookup, left_on="characteristic_id", right_on="_char_id", how="left")

    # Fill category_id from join
    merged["category_id"] = merged["category_id_y"] if "category_id_y" in merged.columns else merged["category_id"]

    # Fill characteristic_name from join only where it's missing
    if "characteristic_name" not in merged.columns or merged["characteristic_name"].isna().all():
        merged["characteristic_name"] = merged["_char_name"]
    else:
        merged["characteristic_name"] = merged["characteristic_name"].where(
            merged["characteristic_name"].notna(),
            merged["_char_name"]
        )

    merged = merged.dropna(subset=["category_id", "value"])
    return merged[["category_id", "characteristic_id", "characteristic_name", "value"]]


class MarketplaceData:
    """
    Holds all reference data for one marketplace.
    Provides fast lookups for categories, characteristics and valid values.
    """

    def __init__(self, name: str):
        self.name = name
        self.categories: pd.DataFrame = pd.DataFrame()
        self.characteristics: pd.DataFrame = pd.DataFrame()
        self.values: pd.DataFrame = pd.DataFrame()

        # Fast lookup indexes (built after loading)
        self._cat_name_to_id: dict = {}           # "Tricouri copii" -> 3216
        self._cat_name_normalized: dict = {}      # "tricouri copii" -> 3216 (diacritics-free)
        self._cat_id_to_name: dict = {}           # 3216 -> "Tricouri copii"
        self._mandatory: dict = {}                # cat_id -> [char_name, ...]
        self._valid_values: dict = {}             # cat_id -> {display_name -> set(values)}
        self._valid_values_normalized: dict = {}  # cat_id -> {display_name -> {norm_val -> orig_val}}
        self._cat_chars: dict = {}                # cat_id -> set(all char display names)
        self._char_name_map: dict = {}            # cat_id -> {norm_key -> display_name from characteristics}
        self._marketplace_values: dict = {}       # norm_char_name -> set(values) across all cats
        self._valid_values_by_char_id: dict = {}  # emag_char_id -> set(values)
        self._char_name_to_emag_id: dict = {}     # cat_id -> {norm_name -> emag_char_id}
        self._char_emag_id_to_display: dict = {}  # emag_char_id -> display_name
        self._char_restrictive: set = set()       # set of emag_char_ids that are restrictive
        self._char_restrictive_by_name: dict = {} # cat_id -> {norm_name -> bool}

    # ── Loaders ────────────────────────────────────────────────────────────────
    def load_from_files(self, cat_file, char_file, val_file):
        self.categories      = load_categories(cat_file)
        self.characteristics = load_characteristics(char_file)
        self.values          = load_values(val_file)
        # If category_id is missing but characteristic_id exists, auto-join with characteristics
        if (not self.characteristics.empty
                and not self.values.empty
                and self.values["category_id"].isna().all()
                and self.values["characteristic_id"].notna().any()):
            self.values = _enrich_values_with_chars(self.values, self.characteristics)
        self._build_indexes()

    def load_from_dataframes(self, cats: pd.DataFrame, chars: pd.DataFrame, vals: pd.DataFrame):
        self.categories      = cats
        self.characteristics = chars
        self.values          = vals
        self._build_indexes()

    # ── Index builder ──────────────────────────────────────────────────────────
    def _build_indexes(self):
        # category name <-> id (vectorized)
        cats = self.categories.copy()
        cats["name"] = cats["name"].astype(str).str.strip()
        if "emag_id" in cats.columns:
            has_emag = (
                cats["emag_id"].notna() &
                (cats["emag_id"].astype(str) != "nan") &
                (cats["emag_id"] != cats["id"])
            )
            join_ids = cats["emag_id"].where(has_emag, cats["id"])
        else:
            join_ids = cats["id"]
        # For Trendyol marketplaces: when two categories share the same name,
        # keep only the one with the most characteristics (more complete data).
        if "trendyol" in self.name.lower():
            chars_tmp = self.characteristics.copy() if not self.characteristics.empty else pd.DataFrame(columns=["category_id"])
            char_count = chars_tmp.groupby("category_id").size().to_dict() if not chars_tmp.empty else {}
            cats["_join_id"] = join_ids
            cats["_char_count"] = cats["id"].map(lambda x: char_count.get(x, 0))
            cats_sorted = cats.sort_values("_char_count", ascending=False)
            # drop_duplicates on name keeps first occurrence = highest char count
            cats_dedup = cats_sorted.drop_duplicates(subset="name", keep="first")
            duplicates = len(cats) - len(cats_dedup)
            if duplicates > 0:
                log.warning(
                    "[%s] _build_indexes: %d categorii cu nume duplicate — se pastreaza cea cu mai multe caracteristici",
                    self.name, duplicates,
                )
            _name_to_join = dict(zip(cats_dedup["name"], cats_dedup["_join_id"]))
            _join_to_name = dict(zip(cats_dedup["_join_id"], cats_dedup["name"]))
        else:
            _name_to_join = dict(zip(cats["name"], join_ids))
            _join_to_name = dict(zip(join_ids, cats["name"]))

        self._cat_name_to_id = _name_to_join
        self._cat_id_to_name = _join_to_name
        # Map internal category_id → join_id so char-based indexes use the same key
        _internal_to_join = dict(zip(cats["id"], join_ids))

        # normalized category lookup (diacritics-insensitive, case-insensitive)
        self._cat_name_normalized = {}
        for name, cat_id in self._cat_name_to_id.items():
            nk = _normalize_str(name)
            if nk not in self._cat_name_normalized:
                self._cat_name_normalized[nk] = cat_id

        # mandatory characteristics (vectorized)
        chars = self.characteristics.copy()
        chars["name"] = chars["name"].astype(str).str.strip()
        # Remap internal category_id → join_id (emag_id when available) so all
        # char-based indexes use the same key scheme as _cat_name_to_id.
        chars["_jcat"] = chars["category_id"].map(lambda x: _internal_to_join.get(x, x))
        mandatory_mask = chars["mandatory"].astype(str).isin(["1", "True", "true", "yes", "1.0"])
        valid_name_mask = chars["name"].notna() & (chars["name"] != "") & (chars["name"] != "nan")
        for cat_id, grp in chars[mandatory_mask & valid_name_mask].groupby("_jcat"):
            self._mandatory[cat_id] = grp["name"].tolist()

        # all char names per category (to detect which fields belong to this marketplace)
        for cat_id, grp in chars[valid_name_mask].groupby("_jcat"):
            self._cat_chars[cat_id] = set(grp["name"])

        # char name canonical map — built from characteristics display names
        # key: _normalize_char_name(display_name)  →  display_name (the authoritative form)
        # Allows tolerant lookup: "Culoare:" and "Culoare" both resolve to the same entry.
        self._char_name_map = {}
        for cat_id, grp in chars[valid_name_mask].groupby("_jcat"):
            self._char_name_map[cat_id] = {
                _normalize_char_name(name): name
                for name in grp["name"]
            }

        # characteristic_id (eMAG external ID) indexes + restrictive flag
        self._valid_values_by_char_id = {}
        self._char_name_to_emag_id = {}
        self._char_emag_id_to_display = {}
        self._char_restrictive = set()
        self._char_restrictive_by_name = {}

        _restr_vals = {"1", "True", "true", "yes", "1.0"}
        if "characteristic_id" in chars.columns:
            char_id_data = chars[valid_name_mask & chars["characteristic_id"].notna()].copy()
            char_id_data = char_id_data[
                char_id_data["characteristic_id"].astype(str).str.strip().isin(["", "nan"]) == False
            ]
            for _, row in char_id_data.iterrows():
                emag_id = row["characteristic_id"]
                cat_id_row = row["_jcat"]
                name = row["name"]
                norm_name = _normalize_char_name(name)
                self._char_name_to_emag_id.setdefault(cat_id_row, {})[norm_name] = emag_id
                self._char_emag_id_to_display[emag_id] = name
                if str(row.get("restrictive", "1")).strip() in _restr_vals:
                    self._char_restrictive.add(emag_id)

        if "restrictive" in chars.columns:
            for cat_id_row, grp in chars[valid_name_mask].groupby("_jcat"):
                self._char_restrictive_by_name[cat_id_row] = {
                    _normalize_char_name(name): str(restr).strip() in _restr_vals
                    for name, restr in zip(grp["name"], grp["restrictive"])
                }

        # valid values — keyed by canonical display name from characteristics (via _char_name_map)
        vals = self.values.copy()
        vals = vals[vals["characteristic_name"].notna() & vals["value"].notna()]
        vals["characteristic_name"] = vals["characteristic_name"].astype(str).str.strip()
        vals["value"] = vals["value"].astype(str).str.strip()
        vals = vals[vals["value"] != "nan"]
        vals["_jcat"] = vals["category_id"].map(lambda x: _internal_to_join.get(x, x))
        for (cat_id, char_name), grp in vals.groupby(["_jcat", "characteristic_name"]):
            # Resolve to canonical display name (from characteristics) if possible
            canonical = self._char_name_map.get(cat_id, {}).get(
                _normalize_char_name(char_name), char_name
            )
            self._valid_values.setdefault(cat_id, {})[canonical] = set(grp["value"])

        # valid values by eMAG characteristic_id (stable across label renames)
        if "characteristic_id" in vals.columns:
            cid_vals = vals[vals["characteristic_id"].notna()].copy()
            cid_vals = cid_vals[
                cid_vals["characteristic_id"].astype(str).str.strip().isin(["", "nan"]) == False
            ]
            for char_id, grp in cid_vals.groupby("characteristic_id"):
                self._valid_values_by_char_id[char_id] = set(grp["value"])

        # normalized valid values (diacritics-insensitive) — built from _valid_values
        self._valid_values_normalized = {}
        for cat_id, char_dict in self._valid_values.items():
            self._valid_values_normalized[cat_id] = {}
            for char_name, val_set in char_dict.items():
                norm_map: dict = {}
                for v in val_set:
                    nv = _normalize_str(v)
                    if nv not in norm_map:
                        norm_map[nv] = v
                self._valid_values_normalized[cat_id][char_name] = norm_map

        # marketplace-wide fallback values — aggregated across all categories
        # Used when a char has values in other categories but not the current one.
        self._marketplace_values = {}
        for char_dict in self._valid_values.values():
            for char_name, val_set in char_dict.items():
                self._marketplace_values.setdefault(
                    _normalize_char_name(char_name), set()
                ).update(val_set)

    # ── Lookup helpers ─────────────────────────────────────────────────────────
    def category_id(self, name: str) -> Optional[int]:
        result = self._cat_name_to_id.get(name)
        if result is None:
            result = self._cat_name_normalized.get(_normalize_str(name))
        if result is None and self._cat_name_to_id:
            # Fuzzy fallback: last resort for minor typos / abbreviations
            matches = difflib.get_close_matches(
                name, self._cat_name_to_id.keys(), n=1, cutoff=0.90
            )
            if matches:
                result = self._cat_name_to_id[matches[0]]
        return result

    def category_name(self, cat_id) -> Optional[str]:
        return self._cat_id_to_name.get(cat_id)

    def mandatory_chars(self, cat_id) -> list:
        return self._mandatory.get(cat_id, [])

    def canonical_char_name(self, cat_id, char_name: str) -> Optional[str]:
        """Return the display name from the characteristics table for this category.

        Returns None if char_name cannot be resolved to any known characteristic.
        Tolerant of trailing ':', whitespace, and case differences.
        """
        # Fast path: already the canonical form
        if char_name in self._cat_chars.get(cat_id, set()):
            return char_name
        # Normalised lookup
        return self._char_name_map.get(cat_id, {}).get(_normalize_char_name(char_name))

    def _get_char_emag_id(self, cat_id, char_name: str):
        """Return the eMAG characteristic_id for this char in the given category, or None."""
        norm = _normalize_char_name(char_name)
        return self._char_name_to_emag_id.get(cat_id, {}).get(norm)

    def is_restrictive(self, cat_id, char_name: str) -> bool:
        """Return True if this char only accepts values from the table (restrictive=1).

        Defaults to True when the flag is unknown (safe default — avoid invalid values).
        """
        emag_id = self._get_char_emag_id(cat_id, char_name)
        if emag_id is not None:
            return emag_id in self._char_restrictive
        # Fallback: name-based lookup
        norm = _normalize_char_name(char_name)
        cat_restr = self._char_restrictive_by_name.get(cat_id, {})
        if norm in cat_restr:
            return cat_restr[norm]
        return True  # safe default

    def valid_values(self, cat_id, char_name: str) -> set:
        cat_dict = self._valid_values.get(cat_id, {})
        result = cat_dict.get(char_name)
        if result is not None:
            return result
        # Normalised fallback: "Culoare" finds values stored under "Culoare:"
        canonical = self.canonical_char_name(cat_id, char_name)
        if canonical and canonical != char_name:
            return cat_dict.get(canonical, set())
        return set()

    def _find_in_set(self, value: str, val_set: set) -> Optional[str]:
        """Normalised lookup in an arbitrary value set (for marketplace fallback)."""
        s = str(value).strip()
        if s in val_set:
            return s
        norm_s = _normalize_str(s)
        for v in val_set:
            if _normalize_str(v) == norm_s:
                return v
        return None

    def marketplace_fallback_values(self, char_name: str) -> set:
        """Values aggregated across all categories for this char name.

        Used as a last-resort fallback when the category-specific values list is empty.
        """
        return self._marketplace_values.get(_normalize_char_name(char_name), set())

    def all_char_names(self, cat_id) -> list:
        return list(self._valid_values.get(cat_id, {}).keys())

    def has_char(self, cat_id, char_name: str) -> bool:
        """Returns True if char_name is a known characteristic for this category in this marketplace."""
        if char_name in self._cat_chars.get(cat_id, set()):
            return True
        return self.canonical_char_name(cat_id, char_name) is not None

    def find_valid(self, value: str, cat_id, char_name: str) -> Optional[str]:
        """Try to match value to a valid option (with common transformations)."""
        vs  = self.valid_values(cat_id, char_name)
        if not vs:
            return None
        s = str(value).strip()
        candidates = [
            s,
            s.upper(),
            f"{s} INTL",
            f"{s.upper()} INTL",
            f"{s} EU",
            f"{s.replace(',', '.')} EU",
        ]
        for c in candidates:
            if c in vs:
                return c
        # Numeric shoe size
        try:
            num = float(s.replace(",", "."))
            for fmt in [f"{num:g} EU", f"{s} EU", f"{s.replace(',', '.')} EU"]:
                if fmt in vs:
                    return fmt
        except ValueError:
            pass
        # Normalized fallback — diacritics-insensitive (e.g. "Negru" matches "Negru" in BG/HU)
        norm_s = _normalize_str(s)
        # Use canonical name so the normalized map key matches
        canonical = self.canonical_char_name(cat_id, char_name) or char_name
        norm_map = self._valid_values_normalized.get(cat_id, {}).get(canonical, {})
        if norm_s in norm_map:
            return norm_map[norm_s]
        # Fuzzy fallback — difflib closest match (handles minor typos from AI output)
        if norm_map:
            matches = difflib.get_close_matches(norm_s, norm_map.keys(), n=1, cutoff=0.80)
            if matches:
                return norm_map[matches[0]]
        return None

    def category_list(self) -> list:
        return self.categories["name"].dropna().tolist()

    def is_loaded(self) -> bool:
        return not self.categories.empty

    def stats(self) -> dict:
        return {
            "categories":      len(self.categories),
            "characteristics": len(self.characteristics),
            "values":          sum(
                len(vals)
                for chars in self._valid_values.values()
                for vals in chars.values()
            ),
        }

    # ── Persistence ────────────────────────────────────────────────────────────
    def save_to_disk(self, folder: Path):
        """Save marketplace data as Parquet files.

        .. deprecated::
            Use DuckDB backend via ``core.reference_store_duckdb.import_marketplace`` instead.
            This method is preserved for ``REFERENCE_BACKEND=parquet`` and ``dual`` modes.
            Will be removed once all marketplaces are fully migrated to DuckDB.
        """
        import warnings
        warnings.warn(
            "save_to_disk is deprecated; use DuckDB backend (REFERENCE_BACKEND=duckdb).",
            DeprecationWarning,
            stacklevel=2,
        )
        folder.mkdir(parents=True, exist_ok=True)
        self.categories.to_parquet(folder / "categories.parquet")
        self.characteristics.to_parquet(folder / "characteristics.parquet")
        self.values.to_parquet(folder / "values.parquet")

    def load_from_disk(self, folder: Path) -> bool:
        """Load marketplace data from Parquet files.

        .. deprecated::
            Use DuckDB backend via ``core.reference_store_duckdb.load_marketplace_data`` instead.
            This method is preserved for ``REFERENCE_BACKEND=parquet`` and ``dual`` modes.
        """
        import warnings
        warnings.warn(
            "load_from_disk is deprecated; use DuckDB backend (REFERENCE_BACKEND=duckdb).",
            DeprecationWarning,
            stacklevel=2,
        )
        try:
            self.categories      = pd.read_parquet(folder / "categories.parquet")
            self.characteristics = pd.read_parquet(folder / "characteristics.parquet")
            self.values          = pd.read_parquet(folder / "values.parquet")
            self._build_indexes()
            s = self.stats()
            log.info(
                "Loaded %s: %d categorii, %d caracteristici, %d valori",
                folder.name, s["categories"], s["characteristics"], s["values"],
            )
            # Warn about categories that have mandatory chars but no values at all
            cats_with_mandatory = set(self._mandatory.keys())
            cats_with_values = set(self._valid_values.keys())
            no_values = cats_with_mandatory - cats_with_values
            if no_values:
                log.warning(
                    "%s: %d categorii cu caracteristici obligatorii dar FARA valori in values.parquet "
                    "(toate campurile vor fi freeform): %s",
                    folder.name, len(no_values),
                    sorted(list(no_values))[:20],
                )
            return True
        except Exception as exc:
            log.error("Eroare la incarcarea %s: %s", folder, exc, exc_info=True)
            return False
