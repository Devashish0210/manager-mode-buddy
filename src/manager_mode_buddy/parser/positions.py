"""
Maps FC25's numeric position codes (as stored in `preferredposition1..4` and similar fields)
to position abbreviations.

Nobody has published this table for FC25 specifically, so it was derived empirically during
Milestone 1: decoded `preferredposition1` for every player in the save that also appears in
`data/fc25_sofifa_players.csv` (a public, ID-compatible reference dataset), then tabulated
which sofifa position each raw code corresponds to. The 12 codes below each had hundreds to
thousands of samples and one dominant label at 85-98% agreement -- high confidence. The
remaining codes (rare wing-back/inside-channel/reserve variants) had only a handful of samples
each, not enough to confirm independently; their positions here are inferred from the standard
FIFA-series position enum ordering and should be treated as low-confidence until validated the
same way.
"""
from typing import Optional

# code -> abbreviation. Confidence noted per group.
POSITION_CODES = {
    0: "GK",
    # -- low-confidence (rare in preferredposition1, inferred from series-standard ordering) --
    1: "RWB",
    2: "SW",
    3: "RB",
    4: "RCB",
    5: "CB",
    6: "LCB",
    7: "LB",
    8: "LWB",
    9: "RDM",
    10: "CDM",
    11: "LDM",
    12: "RM",
    13: "RCM",
    14: "CM",
    15: "LCM",
    16: "LM",
    17: "RAM",
    18: "CAM",
    19: "LAM",
    20: "RF",
    21: "CF",
    22: "LF",
    23: "RW",
    24: "RS",
    25: "ST",
    26: "LS",
    27: "LW",
    28: "SUB",
    29: "RES",
}

# Empirically confirmed at scale (>=85% agreement against sofifa, hundreds+ of samples each).
CONFIRMED_CODES = {0, 3, 5, 7, 10, 12, 14, 16, 18, 23, 25, 27}

# Broad position group used for display/filtering when only the general area matters.
BROAD_GROUP = {
    "GK": "GK",
    "RWB": "RB", "RB": "RB", "RCB": "CB", "SW": "CB",
    "CB": "CB", "LCB": "CB", "LWB": "LB", "LB": "LB",
    "RDM": "CDM", "CDM": "CDM", "LDM": "CDM",
    "RM": "RM", "RCM": "CM", "CM": "CM", "LCM": "CM", "LM": "LM",
    "RAM": "CAM", "CAM": "CAM", "LAM": "CAM",
    "RF": "CF", "CF": "CF", "LF": "CF",
    "RW": "RW", "RS": "ST", "ST": "ST", "LS": "ST", "LW": "LW",
    "SUB": "SUB", "RES": "RES",
}


def position_name(code: Optional[int]) -> Optional[str]:
    """Map a raw position code to its abbreviation, or None if unknown/absent."""
    if code is None:
        return None
    return POSITION_CODES.get(code)


def is_confirmed(code: Optional[int]) -> bool:
    """Whether `code` is one of the empirically-validated (not inferred) mappings."""
    return code in CONFIRMED_CODES
