"""
Normalized data model (spec Section 20). Nothing downstream of the extractor should touch raw
EA field names, table names, or shortname tags directly -- that keeps the UI/filter/scoring
layers independent of the parser's internals (spec Section 19).

Every field except `id` and `attributes` is nullable: the save may not reliably expose it (see
HANDOFF.md for what's confirmed vs. best-effort vs. unavailable), and the spec explicitly
forbids fabricating a value when the data isn't there (Section 30, principle #2).
"""
from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict


class Player(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: Optional[str] = None
    # "save" = decoded from the game's own name tables (see names.py); "sofifa" = joined from
    # the optional reference CSV. Display only either way -- never a scored field.
    name_source: Optional[str] = None

    nationality_id: Optional[int] = None
    preferred_foot: Optional[str] = None  # "Left" / "Right"

    height_cm: Optional[int] = None
    weight_kg: Optional[int] = None

    # ISO date decoded from the save (see parser/dates.py). `age` is completed years at the
    # career's own current date, not today's -- a save two seasons in has two-seasons-older
    # players -- so it is only set when that date could be established.
    birth_date: Optional[str] = None
    age: Optional[int] = None

    overall: Optional[int] = None
    overall_source: Optional[str] = None
    potential: Optional[int] = None

    position: Optional[str] = None
    position_confirmed: bool = False
    other_positions: List[str] = []

    skill_moves: Optional[int] = None
    weak_foot: Optional[int] = None

    # PlayStyles, decoded from the save's trait bitmask (see playstyles.py). `playstyles_plus`
    # is the subset that are PlayStyle+ and is always contained in `playstyles`. `traits` holds
    # the legacy FIFA traits the game still stores alongside them, kept separate because the
    # game doesn't present those as PlayStyles.
    playstyles: List[str] = []
    playstyles_plus: List[str] = []
    traits: List[str] = []

    club_id: Optional[int] = None
    is_free_agent: bool = False

    attributes: Dict[str, int] = {}


class Team(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: int
    name: Optional[str] = None


class ParsedCareer(BaseModel):
    """Result of parsing one save. Intentionally does not include the raw parser objects --
    only the normalized model, per the Section 19 layering."""
    model_config = ConfigDict(frozen=True)

    save_path: str
    players: List[Player]
    team_ids: List[int]
    # The career's current in-game date (ISO), estimated per parser/dates.py. Every `age` in
    # this career is relative to it, so it travels with the data rather than being recomputed.
    as_of_date: Optional[str] = None

    @property
    def player_count(self) -> int:
        return len(self.players)

    @property
    def team_count(self) -> int:
        return len(self.team_ids)
