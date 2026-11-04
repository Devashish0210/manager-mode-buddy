"""
Attribute registry: maps the raw field names the parser exposes to friendly display names,
grouped the way FC25 groups them in its own UI (spec Section 22).

This is a fixed, hand-maintained list (not derived from meta.xml) because meta.xml doesn't
carry category groupings or friendly names -- only raw field names. Only attributes actually
produced by `parser.extractor` should be looked up here; a field with no entry is displayed
using its raw name instead of failing.
"""
import re
from typing import Dict, List, NamedTuple, Optional


class AttributeInfo(NamedTuple):
    field: str
    label: str
    category: str


_REGISTRY: List[AttributeInfo] = [
    AttributeInfo("acceleration", "Acceleration", "PACE"),
    AttributeInfo("sprintspeed", "Sprint Speed", "PACE"),

    AttributeInfo("positioning", "Positioning", "SHOOTING"),
    AttributeInfo("finishing", "Finishing", "SHOOTING"),
    AttributeInfo("shotpower", "Shot Power", "SHOOTING"),
    AttributeInfo("longshots", "Long Shots", "SHOOTING"),
    AttributeInfo("volleys", "Volleys", "SHOOTING"),
    AttributeInfo("penalties", "Penalties", "SHOOTING"),

    AttributeInfo("vision", "Vision", "PASSING"),
    AttributeInfo("crossing", "Crossing", "PASSING"),
    AttributeInfo("freekickaccuracy", "Free Kick Accuracy", "PASSING"),
    AttributeInfo("shortpassing", "Short Passing", "PASSING"),
    AttributeInfo("longpassing", "Long Passing", "PASSING"),
    AttributeInfo("curve", "Curve", "PASSING"),

    AttributeInfo("agility", "Agility", "DRIBBLING"),
    AttributeInfo("balance", "Balance", "DRIBBLING"),
    AttributeInfo("reactions", "Reactions", "DRIBBLING"),
    AttributeInfo("ballcontrol", "Ball Control", "DRIBBLING"),
    AttributeInfo("dribbling", "Dribbling", "DRIBBLING"),
    AttributeInfo("composure", "Composure", "DRIBBLING"),

    AttributeInfo("interceptions", "Interceptions", "DEFENDING"),
    AttributeInfo("headingaccuracy", "Heading Accuracy", "DEFENDING"),
    AttributeInfo("defensiveawareness", "Defensive Awareness", "DEFENDING"),
    AttributeInfo("standingtackle", "Standing Tackle", "DEFENDING"),
    AttributeInfo("slidingtackle", "Sliding Tackle", "DEFENDING"),

    AttributeInfo("jumping", "Jumping", "PHYSICAL"),
    AttributeInfo("stamina", "Stamina", "PHYSICAL"),
    AttributeInfo("strength", "Strength", "PHYSICAL"),
    AttributeInfo("aggression", "Aggression", "PHYSICAL"),

    AttributeInfo("gkdiving", "GK Diving", "GOALKEEPING"),
    AttributeInfo("gkhandling", "GK Handling", "GOALKEEPING"),
    AttributeInfo("gkkicking", "GK Kicking", "GOALKEEPING"),
    AttributeInfo("gkpositioning", "GK Positioning", "GOALKEEPING"),
    AttributeInfo("gkreflexes", "GK Reflexes", "GOALKEEPING"),
]

BY_FIELD: Dict[str, AttributeInfo] = {a.field: a for a in _REGISTRY}

CATEGORY_ORDER = ["PACE", "SHOOTING", "PASSING", "DRIBBLING", "DEFENDING", "PHYSICAL", "GOALKEEPING"]


def known_fields() -> List[str]:
    """All raw field names this registry has a friendly label for."""
    return list(BY_FIELD.keys())


def label_for(field_name: str) -> str:
    """Friendly display name for a raw field, falling back to the raw name if unregistered."""
    info = BY_FIELD.get(field_name)
    return info.label if info else field_name


def discover(available_fields) -> Dict[str, List[AttributeInfo]]:
    """Given the set of field names actually present in parsed data, return only the
    registered attributes that exist, grouped by category in display order. This is the
    'attribute discovery' mechanism from spec Section 22 -- the CLI should never advertise a
    filter for an attribute the parser didn't actually produce."""
    available = set(available_fields)
    grouped: Dict[str, List[AttributeInfo]] = {}
    for cat in CATEGORY_ORDER:
        items = [a for a in _REGISTRY if a.category == cat and a.field in available]
        if items:
            grouped[cat] = items
    return grouped


def _normalize(name: str) -> str:
    """Reduce a user-typed attribute name to a comparison key: case, spaces, underscores and
    punctuation all ignored, so "Short Passing", "short_passing" and "shortpassing" are one
    thing."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


_BY_NORMALIZED: Dict[str, str] = {}
for _info in _REGISTRY:
    _BY_NORMALIZED[_normalize(_info.field)] = _info.field
    _BY_NORMALIZED[_normalize(_info.label)] = _info.field


def resolve_field(name: str) -> Optional[str]:
    """Map whatever the user typed (raw field name or friendly label, any casing/spacing) to
    the raw field name the parser produces, or None if it isn't a registered attribute."""
    return _BY_NORMALIZED.get(_normalize(name))
