"""
PlayStyles (and the legacy FIFA traits that still sit beside them), decoded from the save.

The game stores them as one 41-bit mask split across two `players` columns:

    trait1  [30 bits]  bits 0..29        PlayStyles the player has
    trait2  [11 bits]  bits 30..40

    icontrait1 / icontrait2             the same 41-bit layout again, marking which of
                                        those are PlayStyle+ (the gold ones)

Bits 0..33 are the 34 PlayStyles, laid out in category order -- scoring, passing, defending,
ball control, physical, goalkeeper. Bits 36..40 are four of the old FIFA traits, which FC25
still carries: they are not PlayStyles and the game does not show them as such, so they are
kept separate here rather than padding out a player's PlayStyle list with things a user won't
recognise.

## How the bit -> name mapping was established

Not guessed. It was *solved* against a public reference dataset that lists each player's
PlayStyles by name, by asking, for each of the 41 bits, which name it predicts:

  - Against the shipped `fifa_ng_db.db` (launch data, same vintage as the reference):
    **mean F1 = 0.9968 across the 38 bits with any evidence**, with a mean margin of 0.79 over
    the runner-up name. At that separation the assignment is not ambiguous.
  - Against a career save two seasons in: 0.88 -- the gap is players gaining and losing
    PlayStyles as they develop, not decode error, which is exactly why the launch DB is the
    right thing to validate against.
  - `icontrait1/2` was solved *independently*, against the reference's PlayStyle+ column, and
    produced the same name for all 29 bits where it had evidence. Two separate correlations
    agreeing bit-for-bit is what makes this a decode rather than a correlation.
  - Bits 0..33 come to exactly 34 PlayStyles, which is exactly how many FC25 has. The three
    remaining bits (34, 35, 38) are set *only* on career-generated players -- 84/84 and 139/139
    of them are regens, never a launch player -- so no reference data can name them. They are
    deliberately left unnamed rather than guessed at; see UNNAMED_BITS.

## A note on wording

The names are the reference dataset's. They match what FC25 displays for all but two, where
the game's own label is believed to differ: `Whipped Cross` is shown in game as "Whipped Pass",
and `Close Down` as "Rush Out". Rather than assert a name the evidence didn't establish, both
keep their derived label and gain the in-game wording as a search alias, so looking either one
up works. Reading the shipped localisation database (`loc/eng_us.db`, a related but distinct
container format) would settle the wording properly -- see HANDOFF.md.
"""
from typing import Dict, FrozenSet, List, Optional, Tuple

# Where trait2 picks up from trait1. Both are declared in fifa_ng_db-meta.xml; this is their
# width, not a choice.
TRAIT1_BITS = 30
TOTAL_BITS = 41

# bit -> (PlayStyle name, category), for the 34 PlayStyles.
PLAYSTYLES: Dict[int, Tuple[str, str]] = {
    0: ("Finesse Shot", "Scoring"),
    1: ("Chip Shot", "Scoring"),
    2: ("Power Shot", "Scoring"),
    3: ("Dead Ball", "Scoring"),
    4: ("Power Header", "Scoring"),
    5: ("Incisive Pass", "Passing"),
    6: ("Pinged Pass", "Passing"),
    7: ("Long Ball Pass", "Passing"),
    8: ("Tiki Taka", "Passing"),
    9: ("Whipped Cross", "Passing"),
    10: ("Jockey", "Defending"),
    11: ("Block", "Defending"),
    12: ("Intercept", "Defending"),
    13: ("Anticipate", "Defending"),
    14: ("Slide Tackle", "Defending"),
    15: ("Bruiser", "Defending"),
    16: ("Technical", "Ball control"),
    17: ("Rapid", "Ball control"),
    18: ("Flair", "Ball control"),
    19: ("First Touch", "Ball control"),
    20: ("Trickster", "Ball control"),
    21: ("Press Proven", "Ball control"),
    22: ("Quick Step", "Physical"),
    23: ("Relentless", "Physical"),
    24: ("Trivela", "Physical"),
    25: ("Acrobatic", "Physical"),
    26: ("Long Throw", "Physical"),
    27: ("Aerial", "Physical"),
    28: ("Far Throw", "Goalkeeper"),
    29: ("Footwork", "Goalkeeper"),
    30: ("Cross Claimer", "Goalkeeper"),
    31: ("Close Down", "Goalkeeper"),
    32: ("Far Reach", "Goalkeeper"),
    33: ("Deflector", "Goalkeeper"),
}

# The old FIFA traits, still stored in the same mask. Kept out of the PlayStyle list because
# the game doesn't present them as PlayStyles and a user comparing against their screen would
# not find them there.
TRAITS: Dict[int, str] = {
    36: "Solid Player",
    37: "Team Player",
    39: "Injury Prone",
    40: "Leadership",
}

# Set only on career-generated players (never on a launch player), so there is no reference
# data that could name them. Bits 34 and 35 are always set together, which looks more like one
# two-bit value than two independent flags. Left unnamed on purpose: inventing a label here
# would be indistinguishable, to a reader, from the 38 that are actually established.
UNNAMED_BITS = (34, 35, 38)

# In-game wording for the two names the reference dataset spells differently, so a search for
# either finds the same PlayStyle. Not used for display -- only the derived name is shown,
# because that is the one the evidence establishes.
ALIASES: Dict[str, str] = {
    "whipped pass": "Whipped Cross",
    "rush out": "Close Down",
}

CATEGORY_ORDER = ("Scoring", "Passing", "Defending", "Ball control", "Physical", "Goalkeeper")

ALL_PLAYSTYLES: Tuple[str, ...] = tuple(name for name, _ in PLAYSTYLES.values())
ALL_TRAITS: Tuple[str, ...] = tuple(TRAITS.values())

_BY_KEY: Dict[str, str] = {name.lower(): name for name in ALL_PLAYSTYLES}
_BY_KEY.update(ALIASES)


def resolve(text: str) -> Optional[str]:
    """Look up a PlayStyle by name, case-insensitively, accepting the in-game wording for the
    two that differ. Returns the canonical name, or None if it isn't one."""
    return _BY_KEY.get(" ".join((text or "").split()).lower())


def mask(trait1: Optional[int], trait2: Optional[int]) -> int:
    """Join the two stored columns into the single 41-bit mask they represent."""
    return ((trait1 or 0) & ((1 << TRAIT1_BITS) - 1)) | ((trait2 or 0) << TRAIT1_BITS)


def _names_in(bits: int, table) -> List[str]:
    return [table[bit] for bit in sorted(table) if bits >> bit & 1]


def decode(trait1: Optional[int], trait2: Optional[int],
            icontrait1: Optional[int] = None, icontrait2: Optional[int] = None
            ) -> Tuple[List[str], List[str], List[str]]:
    """(playstyles, playstyles_plus, traits) for one decoded `players` row.

    A PlayStyle+ is included in `playstyles` as well as `playstyles_plus`: it is still a
    PlayStyle the player has, and a search for "Acrobatic" should find a player whose
    Acrobatic is gold. The two masks are OR'd for that reason -- measured on real data, a
    PlayStyle+ is occasionally recorded only in the icon mask, so taking `trait` alone would
    drop it entirely."""
    plain = mask(trait1, trait2)
    plus = mask(icontrait1, icontrait2)
    style_names = {bit: name for bit, (name, _) in PLAYSTYLES.items()}
    return (
        _names_in(plain | plus, style_names),
        _names_in(plus, style_names),
        _names_in(plain | plus, TRAITS),
    )


def category_of(name: str) -> Optional[str]:
    for style, category in PLAYSTYLES.values():
        if style == name:
            return category
    return None


def by_category(names: FrozenSet[str]) -> List[Tuple[str, List[str]]]:
    """Group a player's PlayStyles the way the game's own UI does, for display."""
    grouped: Dict[str, List[str]] = {}
    for bit in sorted(PLAYSTYLES):
        style, category = PLAYSTYLES[bit]
        if style in names:
            grouped.setdefault(category, []).append(style)
    return [(c, grouped[c]) for c in CATEGORY_ORDER if c in grouped]
