"""
Schema Builder — selectează mandatory + top-N characteristics pentru prompt AI.

Logică de scoring pentru opționale:
  +3  dacă atributul există în knowledge store (known_attrs)
  +2  dacă numele apare ca keyword în descriere
  +1  dacă enum-ul are sub 10 valori (ușor de procesat pentru model)

Limita implicită: max_total=20 caracteristici în schema finală.
"""
from __future__ import annotations


class SchemaBuilder:
    def __init__(self, max_total: int = 20):
        self.max_total = max_total

    def select(
        self,
        characteristics: list[dict],
        description: str,
        known_attrs: dict,
    ) -> list[dict]:
        """Selectează caracteristicile pentru prompt.

        Args:
            characteristics: lista completă din categorie.
                Fiecare element: {"name": str, "is_mandatory": bool, "values": list[str]}
            description: descrierea produsului (pentru keyword boost)
            known_attrs: dict de atribute deja cunoscute din knowledge store

        Returns:
            Sublistă ordonată: mandatory first, apoi opționale cu cel mai mare scor.
        """
        if not characteristics:
            return []

        mandatory = [c for c in characteristics if c.get("is_mandatory")]
        optional = [c for c in characteristics if not c.get("is_mandatory")]

        desc_lower = description.lower()
        known_lower = {k.lower() for k in known_attrs}

        scored_optional: list[tuple[int, dict]] = []
        for char in optional:
            score = 0
            name_lower = char["name"].lower()
            if name_lower in known_lower:
                score += 3
            if name_lower in desc_lower:
                score += 2
            values = char.get("values", [])
            if 0 < len(values) < 10:
                score += 1
            scored_optional.append((score, char))

        scored_optional.sort(key=lambda x: x[0], reverse=True)

        remaining_slots = max(0, self.max_total - len(mandatory))
        top_optional = [char for _, char in scored_optional[:remaining_slots]]

        return mandatory + top_optional


def build_json_schema(characteristics: list[dict]) -> dict:
    """Construiește JSON Schema din lista selectată de caracteristici.

    Caracteristici cu valori enum → "enum" constraint.
    Caracteristici freeform (values=[]) → plain "string".
    Toate sunt marcate ca "required".
    """
    if not characteristics:
        return {"type": "object", "properties": {}, "required": []}

    properties: dict = {}
    required: list[str] = []

    for char in characteristics:
        name = char["name"]
        values = char.get("values", [])
        required.append(name)

        if values:
            properties[name] = {"type": "string", "enum": values}
        else:
            properties[name] = {"type": "string"}

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
