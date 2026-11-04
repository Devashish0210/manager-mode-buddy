"""
Composable filter engine (spec Section 21).

Every filter is an independent object with `matches(player)` and a human-readable
`describe()`; `apply(players, filters)` just ANDs them together (spec Section 8: AND
semantics by default). Adding a filter type never means touching a growing `if position and
overall and ...` function, and the CLI can print exactly which criteria produced a result set.

One deliberate rule runs through all of them: **a player whose value for a filtered field is
unknown does not match.** The save doesn't expose every field for every player (see
HANDOFF.md), and a filter asking for "potential >= 85" should return players confirmed to
meet it, not players who might. This is spec Section 30 principle #2 (don't fabricate) applied
to filtering: unknown is not a pass. `mmb search` reports how many players were skipped for
missing data so this is visible rather than silent.
"""
import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Protocol, Sequence, Tuple

from .attributes import label_for, resolve_field
from .models import Player
from .parser.positions import BROAD_GROUP


class FilterError(ValueError):
    """A filter the user asked for couldn't be understood (bad syntax, unknown attribute)."""


class PlayerFilter(Protocol):
    def matches(self, player: Player) -> bool: ...
    def describe(self) -> str: ...


def _broad(position: Optional[str]) -> Optional[str]:
    """Collapse a specific slot to its general position. The save's position codes include
    side-specific variants (RCM/LCM/RCB...) that nobody scouts by, so `--pos CM` should match
    an RCM. Unrecognized values pass through unchanged rather than being dropped."""
    if not position:
        return None
    upper = position.upper()
    return BROAD_GROUP.get(upper, upper)


@dataclass(frozen=True)
class PositionFilter:
    """Matches a player's primary position by default.

    Why not secondary positions too: the primary position decodes correctly for 89% of the
    screened players that also appear in a public reference dataset (measured on the real test
    save), while `preferredposition2..4` can't be validated the same way -- that reference
    lists only one position per player. What can be checked is internal consistency, and it is
    imperfect: 150 decoded goalkeepers carry an outfield secondary position, which the game
    does not do. That's rare in absolute terms but concentrated exactly where it hurts, since
    an unvalidated secondary hit is indistinguishable from a real one in the results table.
    So the trustworthy field is the default and `--include-secondary` opts into the wider,
    noisier net."""
    positions: Tuple[str, ...]
    include_secondary: bool = False

    def matches(self, player: Player) -> bool:
        wanted = {_broad(p) for p in self.positions}
        held = {_broad(player.position)} if player.position else set()
        if self.include_secondary:
            held |= {_broad(p) for p in player.other_positions}
        held.discard(None)
        return bool(wanted & held)

    def describe(self) -> str:
        where = "any position" if self.include_secondary else "primary position"
        return f"{where} in {'/'.join(p.upper() for p in self.positions)}"


@dataclass(frozen=True)
class _RangeFilter:
    """Shared min/max logic for the whole-number Player fields (overall, potential, ...)."""
    field: str
    label: str
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def value_for(self, player: Player) -> Optional[int]:
        return getattr(player, self.field, None)

    def matches(self, player: Player) -> bool:
        value = self.value_for(player)
        if value is None:
            return False
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True

    def describe(self) -> str:
        return f"{self.label} {_range_text(self.minimum, self.maximum)}"


def _range_text(minimum: Optional[int], maximum: Optional[int]) -> str:
    if minimum is not None and maximum is not None:
        return f"{minimum}-{maximum}"
    if minimum is not None:
        return f">= {minimum}"
    if maximum is not None:
        return f"<= {maximum}"
    return "any"


@dataclass(frozen=True)
class OverallFilter(_RangeFilter):
    field: str = "overall"
    label: str = "OVR"


@dataclass(frozen=True)
class PotentialFilter(_RangeFilter):
    field: str = "potential"
    label: str = "POT"


@dataclass(frozen=True)
class AgeFilter(_RangeFilter):
    """Age in completed years at the career's own current date -- not today's. That date is
    estimated (see parser/dates.py), so a player within a few weeks of a birthday may sit on
    the wrong side of a bound; everything else is solid, since the birth date itself decodes at
    99.3% agreement against a public reference."""
    field: str = "age"
    label: str = "age"


@dataclass(frozen=True)
class HeightFilter(_RangeFilter):
    """Height in cm. Validated at 93% exact agreement against a public reference dataset on the
    real save -- one of the more trustworthy fields, and the one that decides whether a striker
    is a target man."""
    field: str = "height_cm"
    label: str = "height (cm)"


@dataclass(frozen=True)
class WeightFilter(_RangeFilter):
    """Weight in kg. 92% exact against the same reference."""
    field: str = "weight_kg"
    label: str = "weight (kg)"


@dataclass(frozen=True)
class SkillMovesFilter(_RangeFilter):
    field: str = "skill_moves"
    label: str = "skill moves"


@dataclass(frozen=True)
class WeakFootFilter(_RangeFilter):
    field: str = "weak_foot"
    label: str = "weak foot"


@dataclass(frozen=True)
class GrowthFilter:
    """Potential minus current overall -- the headroom a scouted player still has. Requires
    both values, so a player missing either one doesn't match."""
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def matches(self, player: Player) -> bool:
        if player.potential is None or player.overall is None:
            return False
        growth = player.potential - player.overall
        if self.minimum is not None and growth < self.minimum:
            return False
        if self.maximum is not None and growth > self.maximum:
            return False
        return True

    def describe(self) -> str:
        return f"growth (POT-OVR) {_range_text(self.minimum, self.maximum)}"


@dataclass(frozen=True)
class AttributeFilter:
    """The spec's Section 8 attribute filter: an arbitrary numeric attribute field plus a min
    and/or max. Nothing here is hard-coded to a specific attribute -- whatever the parser
    produced, and `attributes.py` knows a name for, is filterable."""
    attribute: str
    minimum: Optional[int] = None
    maximum: Optional[int] = None

    def matches(self, player: Player) -> bool:
        value = player.attributes.get(self.attribute)
        if value is None:
            return False
        if self.minimum is not None and value < self.minimum:
            return False
        if self.maximum is not None and value > self.maximum:
            return False
        return True

    def describe(self) -> str:
        return f"{label_for(self.attribute)} {_range_text(self.minimum, self.maximum)}"


@dataclass(frozen=True)
class FootFilter:
    foot: str  # "Left" / "Right"

    def matches(self, player: Player) -> bool:
        return bool(player.preferred_foot) and player.preferred_foot.lower() == self.foot.lower()

    def describe(self) -> str:
        return f"{self.foot.capitalize()} footed"


@dataclass(frozen=True)
class FreeAgentFilter:
    def matches(self, player: Player) -> bool:
        return player.is_free_agent

    def describe(self) -> str:
        return "free agents only"


@dataclass(frozen=True)
class ClubFilter:
    club_id: int

    def matches(self, player: Player) -> bool:
        return player.club_id == self.club_id

    def describe(self) -> str:
        return f"club id {self.club_id}"


@dataclass(frozen=True)
class NameFilter:
    """Substring match on the display name. Names are decoded from the game's own tables (see
    names.py), so on a normal import every player has one. If the shipped fifa_ng_db.db wasn't
    available at import time, names fall back to the optional CSV join or are absent entirely,
    and this filter then matches nothing -- the unknown-is-not-a-pass rule again, which
    `mmb search` states explicitly rather than silently returning zero rows."""
    text: str

    def matches(self, player: Player) -> bool:
        return bool(player.name) and self.text.lower() in player.name.lower()

    def describe(self) -> str:
        return f"name contains {self.text!r}"


# A real FC-family player's overall never approaches this. The floor was established against
# the reference dataset back when the parser also produced tens of thousands of misdecoded
# rows; most of those are gone now (the reader no longer reads past a table's declared row
# count -- see parser/table_reader.read_rows), and what the floor catches today is much more
# clearly identifiable. On the real test save it screens out 137 of 21,950 rows, and now that
# names decode, those rows name themselves: `Alex Hunter` and `Gareth Walker` (the story-mode
# characters), `Generic Boy_4`, `FC22female Skillgame`, `girl young`. They are cutscene and
# skill-game placeholder records that genuinely live in the players table with overall 1, not
# decode artifacts and not scoutable players.
#
# Screening them is still worth doing: a search sorted by potential is otherwise led by rows
# reading "OVR 1 / POT 1" and by characters who cannot be signed. So this stays a screen at the
# *presentation* layer, on by default and switchable off with --include-implausible, exactly
# like `cli._pick_sample_player`'s floor: it never changes what's stored, and `mmb search` says
# how many rows it hid.
MIN_PLAUSIBLE_OVERALL = 40

# Attributes are hard-capped at 99 in every FC-family table, so a decoded 103 or 128 is a
# corrupted row, not a great player. This used to catch a further 988 rows on the real save;
# it now catches none, because the rows it was catching were slots past a table's declared row
# count that the reader no longer reads. Kept as cheap defence, not as a load-bearing screen.
#
# This is deliberately NOT the per-field range check HANDOFF.md records as tried and reverted.
# That one tested each field against its *declared* meta.xml range (often narrower than 0-99),
# which real rows violate routinely -- it rejected 95% of genuine data. This tests only the
# universal 0-99 cap, the same narrow, hard limit `extractor._valid_rating` already applies to
# overall and potential for exactly this reason.
MAX_PLAUSIBLE_ATTRIBUTE = 99


@dataclass(frozen=True)
class PlayStyleFilter:
    """Matches players who have every named PlayStyle (AND, like every other filter here).

    `plus_only` narrows it to PlayStyle+ -- the gold version -- which is a genuinely different
    scouting question: 155 of 21,777 players in a real save have any PlayStyle+ at all, so
    asking for one is asking for an elite specialist rather than a competent one.

    A PlayStyle+ still counts as having the PlayStyle, so `--playstyle Acrobatic` finds a
    player whose Acrobatic is gold (see playstyles.decode)."""
    styles: Tuple[str, ...]
    plus_only: bool = False

    def matches(self, player: Player) -> bool:
        held = set(player.playstyles_plus if self.plus_only else player.playstyles)
        return all(style in held for style in self.styles)

    def describe(self) -> str:
        kind = "PlayStyle+" if self.plus_only else "PlayStyle"
        return f"{kind}: {', '.join(self.styles)}"


@dataclass(frozen=True)
class TraitFilter:
    """The legacy FIFA traits (Leadership, Injury Prone, ...), which FC25 still stores but no
    longer shows as PlayStyles. Separate filter for the same reason they're a separate field:
    a user searching for a PlayStyle should not get these back."""
    traits: Tuple[str, ...]

    def matches(self, player: Player) -> bool:
        held = set(player.traits)
        return all(trait in held for trait in self.traits)

    def describe(self) -> str:
        return f"trait: {', '.join(self.traits)}"


@dataclass(frozen=True)
class PlausiblePlayerFilter:
    """Screens out rows that don't look like real players (see the two constants above)."""
    minimum_overall: int = MIN_PLAUSIBLE_OVERALL
    maximum_attribute: int = MAX_PLAUSIBLE_ATTRIBUTE

    def matches(self, player: Player) -> bool:
        if player.overall is None or player.overall < self.minimum_overall:
            return False
        return all(value <= self.maximum_attribute for value in player.attributes.values())

    def describe(self) -> str:
        return (
            f"plausible players only (OVR >= {self.minimum_overall}, no attribute above "
            f"{self.maximum_attribute})"
        )


def apply(players: Iterable[Player], filters: Sequence[PlayerFilter]) -> List[Player]:
    """AND every filter together. Order doesn't change the result, only the work done, so the
    caller is free to list filters in whatever order reads best."""
    return [p for p in players if all(f.matches(p) for f in filters)]


# --- attribute expression parsing --------------------------------------------------------

_NAME = r"[A-Za-z][A-Za-z0-9 _.'-]*?"
_EXPR_RE = re.compile(rf"^\s*(?P<name>{_NAME})\s*(?P<op>>=|<=|==|=|>|<)\s*(?P<value>-?\d+)\s*$")
_RANGE_RE = re.compile(rf"^\s*(?P<name>{_NAME})\s*[=:]\s*(?P<low>\d+)\s*-\s*(?P<high>\d+)\s*$")


def parse_attribute_filter(expression: str) -> AttributeFilter:
    """Turn a CLI expression into an AttributeFilter.

    Accepted forms (the attribute may be named by raw field or friendly label, any casing):
        vision>=80     "Short Passing >= 78"     dribbling<90     finishing=75-85

    `>` and `<` are translated into the equivalent inclusive bound, since attributes are whole
    numbers -- keeping only min/max internally means one comparison path instead of four.
    """
    range_match = _RANGE_RE.match(expression)
    if range_match:
        field = _resolve_or_raise(range_match.group("name"), expression)
        low, high = int(range_match.group("low")), int(range_match.group("high"))
        if low > high:
            raise FilterError(f"invalid range in {expression!r}: {low} is above {high}")
        return AttributeFilter(attribute=field, minimum=low, maximum=high)

    match = _EXPR_RE.match(expression)
    if not match:
        raise FilterError(
            f"could not understand attribute filter {expression!r}. "
            "Expected something like 'vision>=80', 'Short Passing >= 78' or 'finishing=75-85'."
        )
    field = _resolve_or_raise(match.group("name"), expression)
    op, value = match.group("op"), int(match.group("value"))
    if op == ">=":
        return AttributeFilter(attribute=field, minimum=value)
    if op == ">":
        return AttributeFilter(attribute=field, minimum=value + 1)
    if op == "<=":
        return AttributeFilter(attribute=field, maximum=value)
    if op == "<":
        return AttributeFilter(attribute=field, maximum=value - 1)
    return AttributeFilter(attribute=field, minimum=value, maximum=value)


def _resolve_or_raise(name: str, expression: str) -> str:
    field = resolve_field(name)
    if field is None:
        raise FilterError(
            f"unknown attribute {name.strip()!r} in {expression!r}. "
            "Run `mmb attributes` to see the attributes this career actually exposes."
        )
    return field


# --- sorting ------------------------------------------------------------------------------

def sort_key_for(key: str):
    """Resolve a sort-key name to a function over Player. Supports the headline fields plus
    any attribute (raw name or friendly label). Each key returns a (known, value) pair so
    players missing the value sort below those that have it, in either direction."""
    normalized = key.strip().lower()
    if normalized in ("ovr", "overall"):
        return lambda p: (p.overall is not None, p.overall or 0)
    if normalized in ("pot", "potential"):
        return lambda p: (p.potential is not None, p.potential or 0)
    if normalized == "growth":
        return lambda p: (
            p.potential is not None and p.overall is not None,
            (p.potential or 0) - (p.overall or 0),
        )
    if normalized == "id":
        return lambda p: (True, p.id)
    field = resolve_field(key)
    if field is None:
        raise FilterError(
            f"unknown sort key {key!r}. Use overall, potential, growth, id, or an attribute name."
        )
    return lambda p: (field in p.attributes, p.attributes.get(field, 0))


def sort_players(players: Sequence[Player], key: str = "potential",
                 descending: bool = True) -> List[Player]:
    return sorted(players, key=sort_key_for(key), reverse=descending)
