"""
Marketplace data loader.
Handles Categories, Characteristics and Values Excel files
with flexible column detection (tolerant of extra/missing columns).
"""
import pandas as pd
import json
from pathlib import Path
from collections import defaultdict
from typing import Optional
from core.app_logger import get_logger

log = get_logger("marketplace.loader")


# ── Column aliases ─────────────────────────────────────────────────────────────
# Each list = accepted column names (case-insensitive) for that field
CAT_COL_ALIASES = {
    "id":        ["id", "category_id", "cat_id", "id_categorie"],
    "emag_id":   ["emag_id", "marketplace_id", "external_id", "id_extern"],
    "name":      ["name", "name_ro", "nume", "denumire", "category_name"],
    "parent_id": ["parent_id", "parent", "id_parinte"],
}

CHAR_COL_ALIASES = {
    "id":             ["id", "char_id", "characteristic_id", "id_caracteristica"],
    "category_id":    ["category_id", "cat_id", "id_categorie"],
    "name":           ["name", "name_ro", "nume", "characteristic_name", "denumire"],
    "mandatory":      ["mandatory", "obligatoriu", "required", "is_mandatory"],
}

VAL_COL_ALIASES = {
    "category_id":        ["category_id", "cat_id"],
    "characteristic_id":  ["characteristic_id", "char_id", "emag_characteristic_id"],
    "characteristic_name":["characteristic_name", "name", "char_name", "name_ro", "denumire"],
    "value":              ["value", "value_ro", "valoare", "val", "val_ro"],
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


def load_categories(file) -> pd.DataFrame:
    """Load categories file. Returns DataFrame with standardised columns."""
    df = pd.read_excel(file, sheet_name=0)
    mapping = _map_cols(df, CAT_COL_ALIASES)
    result = pd.DataFrame()
    result["id"]        = df[mapping["id"]]        if mapping["id"]        else None
    result["emag_id"]   = df[mapping["emag_id"]]   if mapping["emag_id"]   else result["id"]
    result["name"]      = df[mapping["name"]]      if mapping["name"]      else None
    result["parent_id"] = df[mapping["parent_id"]] if mapping["parent_id"] else None
    return result.dropna(subset=["id", "name"])


def load_characteristics(file) -> pd.DataFrame:
    """Load characteristics file."""
    df = pd.read_excel(file, sheet_name=0)
    mapping = _map_cols(df, CHAR_COL_ALIASES)
    result = pd.DataFrame()
    result["id"]          = df[mapping["id"]]          if mapping["id"]          else range(len(df))
    result["category_id"] = df[mapping["category_id"]] if mapping["category_id"] else None
    result["name"]        = df[mapping["name"]]        if mapping["name"]        else None
    result["mandatory"]   = df[mapping["mandatory"]]   if mapping["mandatory"]   else 0
    return result.dropna(subset=["category_id", "name"])


def load_values(file) -> pd.DataFrame:
    """Load characteristic values file."""
    df = pd.read_excel(file, sheet_name=0)
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
        self._cat_name_to_id: dict = {}       # "Tricouri copii" -> 3216
        self._cat_id_to_name: dict = {}       # 3216 -> "Tricouri copii"
        self._mandatory: dict = {}            # cat_id -> [char_name, ...]
        self._valid_values: dict = {}         # cat_id -> {char_name -> set(values)}
        self._cat_chars: dict = {}            # cat_id -> set(all char names for that category)

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
        self._cat_name_to_id = dict(zip(cats["name"], join_ids))
        self._cat_id_to_name = dict(zip(join_ids, cats["name"]))

        # mandatory characteristics (vectorized)
        chars = self.characteristics.copy()
        chars["name"] = chars["name"].astype(str).str.strip()
        mandatory_mask = chars["mandatory"].astype(str).isin(["1", "True", "true", "yes", "1.0"])
        valid_name_mask = chars["name"].notna() & (chars["name"] != "") & (chars["name"] != "nan")
        for cat_id, grp in chars[mandatory_mask & valid_name_mask].groupby("category_id"):
            self._mandatory[cat_id] = grp["name"].tolist()

        # all char names per category (to detect which fields belong to this marketplace)
        for cat_id, grp in chars[valid_name_mask].groupby("category_id"):
            self._cat_chars[cat_id] = set(grp["name"])

        # valid values (vectorized)
        vals = self.values.copy()
        vals = vals[vals["characteristic_name"].notna() & vals["value"].notna()]
        vals["characteristic_name"] = vals["characteristic_name"].astype(str).str.strip()
        vals["value"] = vals["value"].astype(str).str.strip()
        vals = vals[vals["value"] != "nan"]
        for (cat_id, char_name), grp in vals.groupby(["category_id", "characteristic_name"]):
            self._valid_values.setdefault(cat_id, {})[char_name] = set(grp["value"])

    # ── Lookup helpers ─────────────────────────────────────────────────────────
    def category_id(self, name: str) -> Optional[int]:
        return self._cat_name_to_id.get(name)

    def category_name(self, cat_id) -> Optional[str]:
        return self._cat_id_to_name.get(cat_id)

    def mandatory_chars(self, cat_id) -> list:
        return self._mandatory.get(cat_id, [])

    def valid_values(self, cat_id, char_name: str) -> set:
        return self._valid_values.get(cat_id, {}).get(char_name, set())

    def all_char_names(self, cat_id) -> list:
        return list(self._valid_values.get(cat_id, {}).keys())

    def has_char(self, cat_id, char_name: str) -> bool:
        """Returns True if char_name is a known characteristic for this category in this marketplace."""
        return char_name in self._cat_chars.get(cat_id, set())

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
        folder.mkdir(parents=True, exist_ok=True)
        self.categories.to_parquet(folder / "categories.parquet")
        self.characteristics.to_parquet(folder / "characteristics.parquet")
        self.values.to_parquet(folder / "values.parquet")

    def load_from_disk(self, folder: Path) -> bool:
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
