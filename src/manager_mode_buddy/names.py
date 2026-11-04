"""
Real names, decoded from the game's own data (spec Section 6: display-only, never scored).

Until now MMB could only put a name on a player by joining an external sofifa CSV, which by
construction covers no player the career itself created -- youth intake and regens all showed
up as `Player 462547`. Those names are in the game data; they just needed the string tables to
be readable. They now are, so a name here is the game's own, for generated and real players
alike, and the CSV join is a fallback rather than the only source.

Where the names live:

  players.firstnameid / lastnameid / commonnameid / playerjerseynameid
      -> a name id, used directly (these are NOT indexes into career_firstnames -- that table
         is the *generator's* pool, mapping a small ordinal to one of these same name ids)
  playernames        (in the shipped fifa_ng_db.db)  name ids 0..43999
  dcplayernames      (in the save; also shipped)     name ids 44000..49999
  editedplayernames  (in the save)                   whole names, typed by the user in-game

So resolving a name needs the shipped database as well as the save. That file is EA's, not
redistributable, and MMB already asks for its `fifa_ng_db-meta.xml` sibling; when it isn't
available, everything else still works and names fall back to the CSV join.

A player's displayed name is their common name if they have one (that is how the game shows
e.g. a player known by a single name), otherwise first + last. An in-game edit wins over both.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from .parser.meta_schema import SchemaIndex
from .parser.table_reader import read_table

# Name-string tables, in increasing precedence: dcplayernames overlays playernames on the id
# ranges where both have an entry.
_STRING_TABLES = [("playernames", "nameid"), ("dcplayernames", "nameid")]

_EDITED_TABLE = "editedplayernames"


@dataclass
class NameTables:
    """name id -> string, plus whatever names the user has edited in-game."""
    strings: Dict[int, str] = field(default_factory=dict)
    edited: Dict[int, Dict[str, str]] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return bool(self.strings) or bool(self.edited)

    def string(self, name_id: Optional[int]) -> Optional[str]:
        if not name_id:
            return None
        value = self.strings.get(name_id)
        return value or None

    def player_name(self, row: Dict[str, object]) -> Optional[str]:
        """Full display name for one decoded `players` row, or None if it has no name at all.

        `playerjerseynameid` is deliberately not used: it is the back-of-shirt name, which is
        frequently just the surname, and reading it as a full name would silently shorten
        people's names rather than admit a gap."""
        player_id = row.get("playerid")
        edit = self.edited.get(player_id) if player_id else None
        if edit:
            edited_name = self._from_edit(edit)
            if edited_name:
                return edited_name

        common = self.string(row.get("commonnameid"))
        if common:
            return common
        parts = [self.string(row.get("firstnameid")), self.string(row.get("lastnameid"))]
        full = " ".join(p for p in parts if p).strip()
        return full or None

    @staticmethod
    def _from_edit(edit: Dict[str, str]) -> Optional[str]:
        common = (edit.get("commonname") or "").strip()
        if common:
            return common
        parts = [(edit.get("firstname") or "").strip(), (edit.get("surname") or "").strip()]
        full = " ".join(p for p in parts if p).strip()
        return full or None


def resolve_game_db(meta_xml_path: str, explicit: Optional[str] = None) -> Optional[Path]:
    """Find the shipped fifa_ng_db.db. It ships alongside fifa_ng_db-meta.xml, which MMB is
    already told about, so the sibling file is the default and needs no extra configuration."""
    candidate = explicit or os.environ.get("MMB_GAME_DB")
    if candidate:
        path = Path(candidate)
        return path if path.exists() else None
    sibling = Path(meta_xml_path).with_name("fifa_ng_db.db")
    return sibling if sibling.exists() else None


def load_name_tables(schema: SchemaIndex, save_blobs: Iterable[bytes],
                     game_db_path: Optional[Path]) -> NameTables:
    """Build the name lookup from the shipped database plus the save's own overrides.

    Order matters: the shipped tables are read first and the save's are laid over them, so a
    squad-update or career-edited name replaces the shipped one for that id."""
    tables = NameTables()
    blobs: List[bytes] = []
    if game_db_path is not None:
        blobs.append(Path(game_db_path).read_bytes())
    blobs.extend(save_blobs)

    for blob in blobs:
        for table_name, key in _STRING_TABLES:
            table_def = schema.by_name.get(table_name)
            if table_def is None:
                continue
            for row in read_table(blob, table_def, key_field_name=key,
                                   fields={key, "name"}):
                name = row.get("name")
                if isinstance(name, str) and name:
                    tables.strings[row[key]] = name

        edited_def = schema.by_name.get(_EDITED_TABLE)
        if edited_def is not None:
            for row in read_table(blob, edited_def, key_field_name="playerid"):
                values = {k: v for k, v in row.items() if isinstance(v, str)}
                if any(v.strip() for v in values.values()):
                    tables.edited[row["playerid"]] = values

    return tables
