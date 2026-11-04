"""
High-level pipeline: FC25 save file -> normalized Player objects (spec Section 19/20).

Data sources and how much to trust each (see HANDOFF.md for the full narrative):
  - career_playergrowthuserseason: rigorously validated against known ground truth (exact
    match on overall + 36 attributes for multiple players). Covers only the players the save
    has calculated a growth tick for -- 35 rows in the real test save, not the ~1,872 an
    earlier reader reported, which was mostly stale slots past the table's declared end.
  - the players/temp_players/cmplayers/smplayers family: broader coverage (tens of thousands
    of players). These are four genuinely distinct tables, each read from its own declared
    place in the container, so merging them is a union of real data rather than the union of
    rival guesses it used to be -- an earlier reader located tables by signature scoring and
    could match several unrelated physical regions for one requested table.
  - teamplayerlinks/temp_teamplayerlinks: playerid -> teamid club assignment.
  - birthdate: decoded. It is a day count from 1582-10-14, not the packed year/month/day
    bitfield an earlier session tried; see parser/dates.py for the three checks that pin the
    epoch (99.3% birth-year agreement against a public reference, plus an exact match on the
    one player whose real date of birth was known). Age is derived from it against the
    career's own current date, which is *estimated* (parser/dates.py again) because the table
    holding it doesn't decode reliably -- so age is good to within a few weeks of a birthday,
    not exact, and is always reported "as of" that date.
  - PlayStyles: decoded from the `trait1`/`trait2` bitmask, with `icontrait1`/`icontrait2`
    marking the PlayStyle+ ones. The bit -> name mapping was solved against a reference
    dataset rather than guessed, and scores F1 0.9968 against the shipped launch database;
    see playstyles.py for the full derivation and its limits.
  - name: decoded from the game's own name tables (see names.py), which needs the shipped
    fifa_ng_db.db alongside the meta.xml MMB is already given. 100% coverage on a real save,
    including every career-generated player, and 99.1% exact agreement with a public reference
    dataset on the 13,874 players both know about (the residual is that dataset preferring a
    longer legal name over the one the game shows, not a decode failure). The sofifa CSV join
    remains as a fallback for when the shipped database isn't available. Display only --
    never used for scoring/filtering (spec Section 6/30).
"""
import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .attributes import BY_FIELD
from .models import Player
from .names import NameTables, load_name_tables, resolve_game_db
from .parser.dates import age_on, decode_date, estimate_current_date
from .parser.db_container import find_db_containers
from .parser.meta_schema import SchemaIndex
from .parser.positions import is_confirmed, position_name
from .parser.table_reader import read_table
from .playstyles import decode as decode_playstyles

IDENTITY_TABLES = ["players", "temp_players", "cmplayers", "smplayers"]
GROWTH_TABLE = "career_playergrowthuserseason"
CLUB_TABLES = ["teamplayerlinks", "temp_teamplayerlinks"]
FREE_AGENT_CLUB_ID = 111592

FOOT_NAMES = {1: "Right", 2: "Left"}


class SaveParseError(RuntimeError):
    pass


# playerid 1 used to decode as an obviously-corrupt row (overall=0 from the growth table;
# potential=121 alongside tattooback=1023 and birthdate=1048568 from the identity tables -- all
# "every bit set for this field's width" patterns), and was excluded here on that evidence.
# It is no longer produced at all: it was a slot past the tables' declared row count, which the
# reader used to walk into (see parser/table_reader.read_rows). The exclusion stays as cheap
# defence for saves this project hasn't seen -- FC-family games are known to reserve id 1 for a
# template record -- but it is now expected to match nothing. Keep it narrow: broader
# heuristics for this class of problem (per-field range checks, cross-row fingerprinting) were
# tried, rejected real data, and were reverted; don't reintroduce one here.
_RESERVED_TEMPLATE_PLAYER_IDS = {1}


def _is_reserved_template_id(pid: int) -> bool:
    return pid in _RESERVED_TEMPLATE_PLAYER_IDS


def _valid_rating(value: Optional[int]) -> Optional[int]:
    """overall and potential are hard-capped at 1-99 in every FC-family table, unlike the
    ~30 gameplay attribute fields, which turned out to have a surprisingly high (2-6%,
    verified empirically) legitimate rate of landing outside their nominally-declared range on
    real save data -- checking those broadly was tried and reverted after it rejected 95% of
    otherwise-real rows (see HANDOFF.md). Checking just these two specific, universally-capped
    fields is a much narrower, well-targeted check.

    It used to reject 2.5% of the growth table's rows and 4-6% of the identity pool's. On the
    same save it now rejects **none**: every rejected row was a slot past the table's declared
    row count that the reader shouldn't have been reading. The guard stays because it is
    nearly free and this project has only one save to measure against, but it is no longer
    load-bearing. A value outside 1-99 here still means a corrupted row rather than a real
    rating, so it's reported as unavailable rather than propagated as a fabricated 100+."""
    if value is None:
        return None
    return value if 1 <= value <= 99 else None


def _merge_family(blobs: List[bytes], schema: SchemaIndex, table_names: Iterable[str],
                   key_field: str = "playerid") -> Dict[int, dict]:
    """Read every named table (that exists in the schema) from every blob and merge rows by
    key, first-seen wins."""
    merged: Dict[int, dict] = {}
    for name in table_names:
        table_def = schema.by_name.get(name)
        if table_def is None:
            continue
        for blob in blobs:
            for row in read_table(blob, table_def, key_field_name=key_field):
                key = row.get(key_field)
                if key and key not in merged:
                    merged[key] = row
    return merged


def load_name_lookup(csv_path: Optional[str]) -> Dict[int, str]:
    """Fallback name join, used only when the shipped fifa_ng_db.db isn't available to decode
    names properly (see names.py). Expects a CSV with `player_id` and `full_name` columns
    (matches data/fc25_sofifa_players.csv from the parser research repo). It covers real
    players only -- no external dataset contains a player your career invented."""
    if not csv_path:
        return {}
    path = Path(csv_path)
    if not path.exists():
        raise SaveParseError(f"names CSV not found: {csv_path}")
    lookup: Dict[int, str] = {}
    with path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                lookup[int(row["player_id"])] = row["full_name"]
            except (KeyError, ValueError):
                continue
    return lookup


@dataclass
class ExtractionResult:
    players: List[Player]
    team_ids: List[int]
    as_of_date: Optional[str] = None


def _resolve_name(pid: int, identity: dict, name_tables: NameTables,
                   csv_names: Dict[int, str]):
    """(name, source). The game's own tables win over the CSV join: they cover the players the
    CSV structurally cannot (anyone the career generated), and where both have an opinion they
    agree 99.1% of the time, with the disagreements being the CSV's longer legal names."""
    decoded = name_tables.player_name(identity) if identity else None
    if decoded:
        return decoded, "save"
    if pid in csv_names:
        return csv_names[pid], "sofifa"
    return None, None


def extract(save_path: str, meta_xml_path: str, names_csv_path: Optional[str] = None,
             game_db_path: Optional[str] = None) -> ExtractionResult:
    save_file = Path(save_path)
    if not save_file.exists():
        raise SaveParseError(f"save file not found: {save_path}")
    meta_file = Path(meta_xml_path)
    if not meta_file.exists():
        raise SaveParseError(
            f"fifa_ng_db-meta.xml not found at: {meta_xml_path}\n"
            "This file ships with EA's own game database, not with MMB -- see README for how "
            "to obtain it."
        )

    save_bytes = save_file.read_bytes()
    blobs = find_db_containers(save_bytes)
    if not blobs:
        raise SaveParseError("no embedded DB containers found -- is this really an FC25 ManagerCareer save?")

    schema = SchemaIndex.from_file(str(meta_file))

    identity_pool = _merge_family(blobs, schema, IDENTITY_TABLES)
    growth_pool = _merge_family(blobs, schema, [GROWTH_TABLE])
    club_pool = _merge_family(blobs, schema, CLUB_TABLES)
    csv_names = load_name_lookup(names_csv_path)
    name_tables = load_name_tables(schema, blobs, resolve_game_db(meta_xml_path, game_db_path))

    # The career's current date, estimated from when players joined their clubs (the table
    # that actually holds it doesn't decode reliably -- see parser/dates.py). Every age below
    # is relative to this, and if it can't be established, ages stay unset rather than being
    # computed against the wrong year.
    as_of = estimate_current_date(
        decode_date(row.get("playerjointeamdate")) for row in identity_pool.values()
    )

    all_ids = set(identity_pool) | set(growth_pool) | set(club_pool)
    players = []
    for pid in sorted(all_ids):
        if _is_reserved_template_id(pid):
            continue
        identity = identity_pool.get(pid, {})
        growth = growth_pool.get(pid, {})
        club = club_pool.get(pid, {})

        overall = _valid_rating(growth.get("overall"))
        overall_source = GROWTH_TABLE if overall is not None else None
        if overall is None:
            overall = _valid_rating(identity.get("overallrating"))
            overall_source = "identity" if overall is not None else None

        potential = _valid_rating(identity.get("potential"))
        # A genuine game invariant -- a real player's potential is never below their current
        # overall -- used to catch template/placeholder records and rows read out of a table's
        # unused slots. On the real save it now drops nothing (all 21,777 players satisfy it),
        # since those rows are no longer read at all. Kept as a cheap invariant check: dropping
        # a row known to be implausible is still better than passing it on.
        if overall is not None and potential is not None and potential < overall:
            continue

        attrs: Dict[str, int] = {}
        for field_name in BY_FIELD:
            value = growth.get(field_name, identity.get(field_name))
            if value is not None:
                attrs[field_name] = value

        born = decode_date(identity.get("birthdate"))
        age = age_on(born, as_of) if born and as_of else None
        # A decoded date that implies an impossible playing age is a junk row, not a player.
        if age is not None and not (14 <= age <= 50):
            born, age = None, None

        pos_code = identity.get("preferredposition1")
        other_codes = [identity.get(f"preferredposition{i}") for i in (2, 3, 4)]
        other_positions = [
            position_name(c) for c in other_codes if c is not None and c >= 0
        ]
        other_positions = [p for p in other_positions if p]

        club_id = club.get("teamid") or None
        foot_code = identity.get("preferredfoot")
        name, name_source = _resolve_name(pid, identity, name_tables, csv_names)
        styles, styles_plus, traits = decode_playstyles(
            identity.get("trait1"), identity.get("trait2"),
            identity.get("icontrait1"), identity.get("icontrait2"),
        )

        players.append(Player(
            id=pid,
            name=name,
            name_source=name_source,
            nationality_id=identity.get("nationality"),
            preferred_foot=FOOT_NAMES.get(foot_code),
            height_cm=identity.get("height"),
            weight_kg=identity.get("weight"),
            birth_date=born.isoformat() if born else None,
            age=age,
            overall=overall,
            overall_source=overall_source,
            potential=potential,
            position=position_name(pos_code),
            position_confirmed=is_confirmed(pos_code),
            other_positions=other_positions,
            skill_moves=identity.get("skillmoves"),
            weak_foot=identity.get("weakfootabilitytypecode"),
            playstyles=styles,
            playstyles_plus=styles_plus,
            traits=traits,
            club_id=club_id,
            is_free_agent=club_id == FREE_AGENT_CLUB_ID,
            attributes=attrs,
        ))

    team_ids = sorted({row["teamid"] for row in club_pool.values() if row.get("teamid")})
    return ExtractionResult(players=players, team_ids=team_ids,
                            as_of_date=as_of.isoformat() if as_of else None)
