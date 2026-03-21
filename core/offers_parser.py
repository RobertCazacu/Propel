"""
Offers file parser.
Reads the export Excel and returns a list of product dicts.
Flexible: tolerates extra columns and different naming.
"""
import pandas as pd
import re
from typing import Optional


# Column aliases for the offers file
OFFER_COL_ALIASES = {
    "id":          ["id intern ofertă", "id intern oferta", "id", "offer_id", "id_oferta"],
    "name":        ["nume", "name", "title", "titlu", "product_name"],
    "status":      ["status"],
    "error":       ["eroare ofertă", "eroare oferta", "error", "eroare", "offer_errors", "offer_error"],
    "description": ["descriere", "description", "desc"],
    "category":    ["categorie", "category", "cat", "category_name"],
}


def _find_col(df: pd.DataFrame, aliases: list) -> Optional[str]:
    lower_map = {c.lower().strip(): c for c in df.columns}
    for alias in aliases:
        if alias.lower() in lower_map:
            return lower_map[alias.lower()]
    return None


def parse_offers_file(file) -> tuple[pd.DataFrame, list[str]]:
    """
    Parse an offers Excel file.
    Returns (df, char_col_pairs) where char_col_pairs is a list of
    (name_col, value_col) tuples for each characteristic slot.
    """
    # Find the correct sheet
    xl = pd.ExcelFile(file)
    sheet = next((s for s in xl.sheet_names if "export" in s.lower()), xl.sheet_names[0])
    df = pd.read_excel(file, sheet_name=sheet, dtype=str)
    df = df.where(pd.notnull(df), None)

    # Detect characteristic columns (pairs: "Offer ch. N name" / "Offer ch. N val.")
    char_pairs = []
    seen = set()
    for col in df.columns:
        m = re.match(r"offer\s+ch\.\s*(\d+)\s+name", col.lower().strip())
        if m:
            n = m.group(1)
            val_col = next(
                (c for c in df.columns if re.match(rf"offer\s+ch\.\s*{n}\s+val", c.lower().strip())),
                None,
            )
            if val_col and n not in seen:
                char_pairs.append((col, val_col))
                seen.add(n)

    return df, char_pairs


def extract_products(file) -> list[dict]:
    """
    Return list of product dicts with standardised keys.
    """
    df, char_pairs = parse_offers_file(file)

    mapping = {field: _find_col(df, aliases) for field, aliases in OFFER_COL_ALIASES.items()}

    products = []
    for _, row in df.iterrows():
        # Base fields
        prod = {
            "id":          row.get(mapping["id"])          if mapping["id"]          else None,
            "name":        row.get(mapping["name"])        if mapping["name"]        else None,
            "status":      row.get(mapping["status"])      if mapping["status"]      else None,
            "error":       row.get(mapping["error"])       if mapping["error"]       else None,
            "description": row.get(mapping["description"]) if mapping["description"] else None,
            "category":    row.get(mapping["category"])    if mapping["category"]    else None,
        }

        # Existing characteristics
        existing = {}
        for name_col, val_col in char_pairs:
            ch_name = row.get(name_col)
            ch_val  = row.get(val_col)
            if ch_name and str(ch_name).strip() and str(ch_name).strip() != "nan":
                existing[str(ch_name).strip()] = ch_val if (ch_val and str(ch_val).strip() != "nan") else None

        prod["existing_chars"] = existing
        prod["_char_pairs"]    = char_pairs  # keep for export
        products.append(prod)

    return products, char_pairs


def get_error_code(error: str) -> Optional[str]:
    if not error:
        return None
    m = re.match(r"^(\d+)", str(error).strip())
    return m.group(1) if m else None
