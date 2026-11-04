"""
SQLite-backed local career storage (spec Section 16, Milestone 2).

Design decisions:
- Each imported save becomes its own on-disk "career profile" under
  ~/.manager-buddy/saves/<profile>/ (metadata.json + players.db), matching spec Section 16's
  conceptual layout. This module only knows about ParsedCareer/Player -- never the parser or a
  raw save file -- keeping the Section 19 layering intact (storage sits below the normalized
  model, not beside the parser).
- Profile identity: the profile name defaults to a filesystem-safe slug of the name the user
  typed in game ("Signed Hartmann" -> `signed_hartmann`), falling back to the save file's own
  generated name when a save carries none, with an explicit override available to callers. Re-importing the same profile name overwrites that profile's data in place (a
  re-import, not an error) -- there's no versioning requirement in the spec, and a stale
  profile silently shadowing a fresh import would be worse than overwriting.
- `~/.manager-buddy` is resolved fresh on every call (not cached at import time) so tests can
  redirect it via the MMB_HOME environment variable without needing to reload the module.
"""
import json
import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import List, Optional

from .models import ParsedCareer, Player
from .parser.dates import age_on
from .saves import read_save_name


class CareerNotFoundError(RuntimeError):
    pass


@dataclass
class CareerMetadata:
    profile: str
    save_path: str
    imported_at: str
    player_count: int
    team_count: int
    # The career's current in-game date. Defaulted so metadata.json files written before ages
    # existed still load.
    as_of_date: Optional[str] = None
    # The name the user gave this save in game. Stored rather than re-read from the save file
    # because a career outlives the file it was imported from -- the save can be overwritten by
    # the next autosave, renamed, or deleted, and the imported career still has to be
    # identifiable. Defaulted for metadata.json files written before this existed.
    save_name: Optional[str] = None

    @property
    def label(self) -> str:
        """What to show the user for this career, best available: the in-game name, else the
        save file's generated name, else the profile."""
        return self.save_name or Path(self.save_path).name or self.profile


def _home() -> Path:
    override = os.environ.get("MMB_HOME")
    return Path(override) if override else Path.home() / ".manager-buddy"


def _saves_dir() -> Path:
    return _home() / "saves"


def profile_dir(profile: str) -> Path:
    return _saves_dir() / profile


def _db_path(profile: str) -> Path:
    return profile_dir(profile) / "players.db"


def _metadata_path(profile: str) -> Path:
    return profile_dir(profile) / "metadata.json"


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")
    return slug or "career"


def default_profile_name(save_path: str, save_name: Optional[str] = None) -> str:
    """A profile name the user will recognise. `mmb search --profile <name>` and the "Career:"
    line both show this, so deriving it from the in-game name means those read as
    `signed_hartmann` rather than `managercareer20260908152939088`."""
    return slugify(save_name or Path(save_path).stem)


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id INTEGER PRIMARY KEY,
    name TEXT,
    name_source TEXT,
    nationality_id INTEGER,
    preferred_foot TEXT,
    height_cm INTEGER,
    weight_kg INTEGER,
    birth_date TEXT,
    age INTEGER,
    overall INTEGER,
    overall_source TEXT,
    potential INTEGER,
    position TEXT,
    position_confirmed INTEGER NOT NULL DEFAULT 0,
    other_positions TEXT NOT NULL DEFAULT '[]',
    skill_moves INTEGER,
    weak_foot INTEGER,
    playstyles TEXT NOT NULL DEFAULT '[]',
    playstyles_plus TEXT NOT NULL DEFAULT '[]',
    traits TEXT NOT NULL DEFAULT '[]',
    club_id INTEGER,
    is_free_agent INTEGER NOT NULL DEFAULT 0,
    attributes TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS teams (
    id INTEGER PRIMARY KEY
);
"""


def list_careers() -> List[CareerMetadata]:
    saves_dir = _saves_dir()
    if not saves_dir.exists():
        return []
    profiles = []
    for child in sorted(saves_dir.iterdir()):
        meta_path = child / "metadata.json"
        if not meta_path.exists():
            continue
        stored = json.loads(meta_path.read_text(encoding="utf-8"))
        # Tolerate keys a newer MMB doesn't know about rather than failing to list the career.
        meta = CareerMetadata(**{k: v for k, v in stored.items()
                                  if k in CareerMetadata.__dataclass_fields__})
        if meta.save_name is None:
            # Imported before save names were read. Recover it from the original file if it's
            # still there, so existing careers get their real name without a re-import. Not
            # written back: listing careers shouldn't have side effects.
            meta.save_name = read_save_name(Path(meta.save_path))
        profiles.append(meta)
    return profiles


def save_career(profile: str, career: ParsedCareer,
                 save_name: Optional[str] = None) -> CareerMetadata:
    """Persist a freshly-parsed career into the named profile, replacing any prior data for
    that profile."""
    target_dir = profile_dir(profile)
    target_dir.mkdir(parents=True, exist_ok=True)
    db_path = _db_path(profile)
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript(SCHEMA)
        with conn:
            conn.executemany(
                """INSERT INTO players (
                    id, name, name_source, nationality_id, preferred_foot, height_cm,
                    weight_kg, birth_date, age, overall, overall_source, potential, position,
                    position_confirmed, other_positions, skill_moves, weak_foot,
                    playstyles, playstyles_plus, traits, club_id,
                    is_free_agent, attributes
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        p.id, p.name, p.name_source, p.nationality_id, p.preferred_foot,
                        p.height_cm, p.weight_kg, p.birth_date, p.age,
                        p.overall, p.overall_source, p.potential,
                        p.position, int(p.position_confirmed), json.dumps(p.other_positions),
                        p.skill_moves, p.weak_foot,
                        json.dumps(p.playstyles), json.dumps(p.playstyles_plus),
                        json.dumps(p.traits),
                        p.club_id, int(p.is_free_agent),
                        json.dumps(p.attributes),
                    )
                    for p in career.players
                ],
            )
            conn.executemany(
                "INSERT INTO teams (id) VALUES (?)",
                [(team_id,) for team_id in career.team_ids],
            )
    finally:
        conn.close()

    metadata = CareerMetadata(
        profile=profile,
        save_path=str(career.save_path),
        imported_at=datetime.now(timezone.utc).isoformat(),
        player_count=career.player_count,
        team_count=career.team_count,
        as_of_date=career.as_of_date,
        save_name=save_name,
    )
    _metadata_path(profile).write_text(json.dumps(asdict(metadata), indent=2), encoding="utf-8")
    return metadata


def load_career(profile: str) -> ParsedCareer:
    """Reopen a previously-imported career straight from SQLite -- no save file or meta.xml
    needed, and the original save is never touched (Milestone 2's core requirement)."""
    meta_path = _metadata_path(profile)
    db_path = _db_path(profile)
    if not meta_path.exists() or not db_path.exists():
        raise CareerNotFoundError(f"no imported career profile named {profile!r}")

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        players = [_row_to_player(row) for row in conn.execute("SELECT * FROM players ORDER BY id")]
        team_ids = [row["id"] for row in conn.execute("SELECT id FROM teams ORDER BY id")]
    finally:
        conn.close()

    return ParsedCareer(save_path=metadata["save_path"], players=players, team_ids=team_ids,
                        as_of_date=metadata.get("as_of_date"))


def _optional(row: sqlite3.Row, column: str):
    return row[column] if column in row.keys() else None


def _json_list(row: sqlite3.Row, column: str) -> List[str]:
    """A JSON list column, tolerating a profile imported before that column existed -- those
    load without the field rather than failing, and a re-import fills it in."""
    raw = _optional(row, column)
    return json.loads(raw) if raw else []


def _row_to_player(row: sqlite3.Row) -> Player:
    return Player(
        id=row["id"],
        name=row["name"],
        name_source=row["name_source"],
        nationality_id=row["nationality_id"],
        preferred_foot=row["preferred_foot"],
        height_cm=row["height_cm"],
        weight_kg=row["weight_kg"],
        # Profiles imported before birth dates were decoded simply don't have these columns;
        # they load without an age rather than failing, and a re-import fills them in.
        birth_date=_optional(row, "birth_date"),
        age=_optional(row, "age"),
        overall=row["overall"],
        overall_source=row["overall_source"],
        potential=row["potential"],
        position=row["position"],
        position_confirmed=bool(row["position_confirmed"]),
        other_positions=json.loads(row["other_positions"]),
        skill_moves=row["skill_moves"],
        weak_foot=row["weak_foot"],
        playstyles=_json_list(row, "playstyles"),
        playstyles_plus=_json_list(row, "playstyles_plus"),
        traits=_json_list(row, "traits"),
        club_id=row["club_id"],
        is_free_agent=bool(row["is_free_agent"]),
        attributes=json.loads(row["attributes"]),
    )


# --- active career selection -------------------------------------------------------------
#
# The spec's Section 16 layout reserves a `config/` directory next to `saves/`; this is its
# first use. Remembering which career the user is working on means the search commands don't
# need a --profile flag on every invocation (they still accept one, which wins). Stored as
# JSON rather than a bare name so other settings can join it later without a format change.

def _config_dir() -> Path:
    return _home() / "config"


def _active_path() -> Path:
    return _config_dir() / "active.json"


def set_active_career(profile: str) -> None:
    """Remember `profile` as the career subsequent commands operate on. Validates the profile
    exists so a typo fails now rather than at the next search."""
    if not _metadata_path(profile).exists():
        raise CareerNotFoundError(f"no imported career profile named {profile!r}")
    _config_dir().mkdir(parents=True, exist_ok=True)
    _active_path().write_text(json.dumps({"active": profile}, indent=2), encoding="utf-8")


def get_active_career() -> Optional[str]:
    """The remembered career, or None if none is set or it has since been removed from disk."""
    path = _active_path()
    if not path.exists():
        return None
    try:
        profile = json.loads(path.read_text(encoding="utf-8")).get("active")
    except json.JSONDecodeError:
        return None
    if not profile or not _metadata_path(profile).exists():
        return None
    return profile


def resolve_profile(explicit: Optional[str] = None) -> str:
    """Which career a command should act on: an explicit --profile wins, then the remembered
    active career, then -- if exactly one career is imported -- that one, since asking the
    user to choose between one option is pointless. Raises with actionable text otherwise."""
    if explicit:
        return explicit
    active = get_active_career()
    if active:
        return active
    profiles = list_careers()
    if len(profiles) == 1:
        return profiles[0].profile
    if not profiles:
        raise CareerNotFoundError(
            "no careers imported yet. Run `mmb saves` to find your save files, then "
            "`mmb import` to import one."
        )
    names = ", ".join(p.profile for p in profiles)
    raise CareerNotFoundError(
        f"several careers are imported ({names}). Run `mmb use <profile>` to pick one, or "
        "pass --profile."
    )


def load_metadata(profile: str) -> CareerMetadata:
    path = _metadata_path(profile)
    if not path.exists():
        raise CareerNotFoundError(f"no imported career profile named {profile!r}")
    return CareerMetadata(**json.loads(path.read_text(encoding="utf-8")))


def set_as_of_date(profile: str, as_of: date) -> int:
    """Set the career's current in-game date and recompute every player's age from it.

    Ages are derived from stored birth dates, so this never re-parses the save -- it is the
    cheap correction for the one number MMB has to estimate. Returns how many ages changed.

    The estimate exists because `career_calendar` doesn't decode reliably (see
    parser/dates.py); the user can just read the date off their game, which is exact.
    """
    meta_path = _metadata_path(profile)
    db_path = _db_path(profile)
    if not meta_path.exists() or not db_path.exists():
        raise CareerNotFoundError(f"no imported career profile named {profile!r}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    updated = 0
    try:
        rows = conn.execute("SELECT id, birth_date, age FROM players "
                            "WHERE birth_date IS NOT NULL").fetchall()
        changes = []
        for row in rows:
            born = date.fromisoformat(row["birth_date"])
            new_age = age_on(born, as_of)
            if new_age != row["age"]:
                changes.append((new_age, row["id"]))
        with conn:
            conn.executemany("UPDATE players SET age = ? WHERE id = ?", changes)
        updated = len(changes)
    finally:
        conn.close()

    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["as_of_date"] = as_of.isoformat()
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return updated
