"""Tests for SchemaBuilder — mandatory + top-N selection."""
import pytest
from core.schema_builder import SchemaBuilder, build_json_schema


@pytest.fixture
def chars():
    """Simulează lista de caracteristici dintr-o categorie."""
    return [
        {"name": "Culoare",      "is_mandatory": True,  "values": ["Negru", "Alb", "Roșu", "Albastru"]},
        {"name": "Material",     "is_mandatory": True,  "values": ["Bumbac", "Poliester", "Lână"]},
        {"name": "Mărime",       "is_mandatory": True,  "values": ["XS", "S", "M", "L", "XL", "XXL"]},
        {"name": "Stil",         "is_mandatory": False, "values": ["Casual", "Sport", "Elegant"]},
        {"name": "Sezon",        "is_mandatory": False, "values": ["Vară", "Iarnă", "Primăvară", "Toamnă"]},
        {"name": "Brand",        "is_mandatory": False, "values": []},
        {"name": "Descriere",    "is_mandatory": False, "values": []},
        {"name": "Greutate",     "is_mandatory": False, "values": []},
        {"name": "Origine",      "is_mandatory": False, "values": ["România", "China", "Turcia"]},
        {"name": "Certificări",  "is_mandatory": False, "values": ["CE", "ISO", "OEKO-TEX"]},
    ]


def test_mandatory_always_included(chars):
    builder = SchemaBuilder(max_total=6)
    selected = builder.select(chars, description="tricou negru bumbac", known_attrs={})
    names = [c["name"] for c in selected]
    assert "Culoare" in names
    assert "Material" in names
    assert "Mărime" in names


def test_max_total_respected(chars):
    builder = SchemaBuilder(max_total=5)
    selected = builder.select(chars, description="", known_attrs={})
    assert len(selected) <= 5


def test_known_attrs_boost_optional(chars):
    """Caracteristicile prezente în knowledge store au prioritate la opționale."""
    builder = SchemaBuilder(max_total=6)
    selected = builder.select(
        chars,
        description="tricou",
        known_attrs={"Sezon": "Vară"},  # Sezon cunoscut → prioritate
    )
    names = [c["name"] for c in selected]
    assert "Sezon" in names


def test_description_keyword_boost(chars):
    """Keyword match în descriere ridică scorul caracteristicii."""
    builder = SchemaBuilder(max_total=5)
    selected = builder.select(
        chars,
        description="produs de origine română, sezon vară",
        known_attrs={},
    )
    names = [c["name"] for c in selected]
    # "origine" și "sezon" apar în descriere — ar trebui să fie incluse dacă nu depășim limita
    # (3 mandatory + 2 opționale = 5)
    assert len(selected) <= 5


def test_build_json_schema(chars):
    builder = SchemaBuilder(max_total=20)
    selected = builder.select(chars, description="", known_attrs={})
    schema = build_json_schema(selected)
    assert schema["type"] == "object"
    assert "Culoare" in schema["properties"]
    assert schema["properties"]["Culoare"]["enum"] == ["Negru", "Alb", "Roșu", "Albastru"]
    assert "Culoare" in schema["required"]
    assert schema["properties"]["Brand"]["type"] == "string"
    assert "enum" not in schema["properties"]["Brand"]


def test_empty_characteristics(chars):
    builder = SchemaBuilder(max_total=20)
    selected = builder.select([], description="", known_attrs={})
    assert selected == []
