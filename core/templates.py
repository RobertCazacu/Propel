"""
Generate downloadable Excel template files for marketplace data import.
"""
import io
import pandas as pd


def _to_bytes(df: pd.DataFrame) -> bytes:
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Sheet1")
    return buf.getvalue()


def categories_template() -> bytes:
    df = pd.DataFrame([
        {"id": 1,  "emag_id": 101, "name": "Tricouri sport barbati",  "parent_id": 0},
        {"id": 2,  "emag_id": 102, "name": "Tricouri sport femei",    "parent_id": 0},
        {"id": 3,  "emag_id": 103, "name": "Tricouri sport copii",    "parent_id": 0},
        {"id": 4,  "emag_id": 104, "name": "Hanorace sport barbati",  "parent_id": 0},
        {"id": 5,  "emag_id": 105, "name": "Pantaloni sport barbati", "parent_id": 0},
    ])
    return _to_bytes(df)


def characteristics_template() -> bytes:
    df = pd.DataFrame([
        {"id": 10, "category_id": 1, "name": "Culoare de baza",   "mandatory": 1},
        {"id": 11, "category_id": 1, "name": "Marime:",           "mandatory": 1},
        {"id": 12, "category_id": 1, "name": "Material:",         "mandatory": 0},
        {"id": 13, "category_id": 1, "name": "Pentru:",           "mandatory": 1},
        {"id": 14, "category_id": 2, "name": "Culoare de baza",   "mandatory": 1},
        {"id": 15, "category_id": 2, "name": "Marime:",           "mandatory": 1},
    ])
    return _to_bytes(df)


def values_template() -> bytes:
    df = pd.DataFrame([
        {"category_id": 1, "characteristic_id": 10, "characteristic_name": "Culoare de baza", "value": "Negru"},
        {"category_id": 1, "characteristic_id": 10, "characteristic_name": "Culoare de baza", "value": "Alb"},
        {"category_id": 1, "characteristic_id": 10, "characteristic_name": "Culoare de baza", "value": "Rosu"},
        {"category_id": 1, "characteristic_id": 11, "characteristic_name": "Marime:",         "value": "S"},
        {"category_id": 1, "characteristic_id": 11, "characteristic_name": "Marime:",         "value": "M"},
        {"category_id": 1, "characteristic_id": 11, "characteristic_name": "Marime:",         "value": "L"},
        {"category_id": 1, "characteristic_id": 11, "characteristic_name": "Marime:",         "value": "XL"},
        {"category_id": 1, "characteristic_id": 12, "characteristic_name": "Material:",       "value": "Bumbac"},
        {"category_id": 1, "characteristic_id": 12, "characteristic_name": "Material:",       "value": "Poliester"},
    ])
    return _to_bytes(df)


def offers_template() -> bytes:
    df = pd.DataFrame([
        {
            "id intern ofertă":  "12345",
            "nume":              "Tricou Nike Dri-FIT - Barbati",
            "categorie":         "Tricouri sport barbati",
            "eroare ofertă":     "1009 - Caracteristica obligatorie lipsa",
            "descriere":         "Tricou sport barbati din material Dri-FIT, culoare neagra, marime L.",
            "Offer ch. 1 name":  "Culoare de baza",
            "Offer ch. 1 val.":  "Negru",
            "Offer ch. 2 name":  "Marime:",
            "Offer ch. 2 val.":  "",
            "Offer ch. 3 name":  "Material:",
            "Offer ch. 3 val.":  "",
        },
        {
            "id intern ofertă":  "12346",
            "nume":              "Hanorac Adidas Essentials - Barbati",
            "categorie":         "",
            "eroare ofertă":     "1007 - Categoria nu a fost gasita",
            "descriere":         "Hanorac sport barbati cu gluga, material fleece, culoare gri.",
            "Offer ch. 1 name":  "",
            "Offer ch. 1 val.":  "",
            "Offer ch. 2 name":  "",
            "Offer ch. 2 val.":  "",
            "Offer ch. 3 name":  "",
            "Offer ch. 3 val.":  "",
        },
        {
            "id intern ofertă":  "12347",
            "nume":              "Pantaloni Puma Running - Barbati",
            "categorie":         "Pantaloni sport barbati",
            "eroare ofertă":     "1010 - Valoare caracteristica invalida",
            "descriere":         "Pantaloni sport pentru alergare, material poliester, culoare negru.",
            "Offer ch. 1 name":  "Culoare de baza",
            "Offer ch. 1 val.":  "NEGRU INTL",
            "Offer ch. 2 name":  "Marime:",
            "Offer ch. 2 val.":  "L INTL",
            "Offer ch. 3 name":  "",
            "Offer ch. 3 val.":  "",
        },
    ])
    return _to_bytes(df)
