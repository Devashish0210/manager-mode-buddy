"""
FC save-file discovery: the "select a save file" half of spec Sections 16/17.

FC-family games on PC keep career saves as extensionless files in a `settings/` directory,
named `ManagerCareer<timestamp>` (Manager Career) or `PlayerCareer<timestamp>` (Player Career,
which MMB doesn't parse). The game also writes a second copy of a save with an `A` suffix
appended -- both are real files and either can be the newer one, so both are listed rather
than guessing which is canonical.

**Where that `settings/` directory lives changed between titles**, and both layouts are still
in use on the same machine, so both are searched:

  - `%LOCALAPPDATA%/EA SPORTS FC 25/settings/` -- where FC 25 actually writes (confirmed on a
    real installation).
  - `<Documents>/FC 24/settings/` -- the older FIFA/FC layout, including the OneDrive-redirected
    Documents folders Windows commonly sets up.

Discovery is best-effort by design: it looks in those locations and returns what it finds.
Nothing here validates that a file really is an FC25 save -- confirming that means finding
embedded DB containers, which costs a full scan of a ~15MB file; `mmb import` does that
properly and reports a clear error if a picked file turns out not to be one.

**The name the user typed in game is in the file**, in a 202-byte `FBCHUNKS` header that every
EA save of every kind carries -- see `read_save_name`. Reading it costs 202 bytes, so the
picker can label saves the way the user thinks of them ("Signed Hartmann") instead of by the
timestamped file name the game generates ("ManagerCareer20260908152939088"), which tells them
nothing about which career it is.

That name is also the only *stable* handle on a career. FC keeps one file per career and
replaces it on every save -- writing a new timestamped file and deleting the old one -- so the
file name changes under you every session, while the in-game name doesn't.
"""
import os
import struct
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

# Only Manager Career saves: Player Career saves use different tables and aren't what MMB
# scouts. Kept as a tuple so a future mode can be added without touching the scan logic.
SAVE_NAME_PREFIXES: Tuple[str, ...] = ("ManagerCareer",)

# Extra directories to scan, os.pathsep-separated. Useful when saves live somewhere
# non-standard (a copied-out save, a second drive, a test fixture).
SAVE_DIRS_ENV = "MMB_SAVE_DIRS"


# Every EA save file -- career, squads, settings, DLC assets -- starts with the same header:
#
#     0   b"FBCHUNKS"
#     8   version (u16), 1 in every file seen
#     10  header block size (u32), 184 in every file seen
#     14  payload size (u32)
#     18  the name the user typed in game: NUL-terminated UTF-8, within the 184-byte block
#
# Verified across all 10 saves on the user's machine (career, squad, Ultimate Team, settings
# and DLC files alike): file size minus payload size is exactly 202 = 18 + 184 for every one,
# which pins both the name's offset and the bound on its length.
SAVE_MAGIC = b"FBCHUNKS"
_NAME_OFFSET = 18
_HEADER_SIZE_OFFSET = 10
_MAX_HEADER_BLOCK = 4096  # sanity bound; the real value is 184


def read_save_name(path: Path) -> Optional[str]:
    """The career name as typed in game, or None if this isn't an EA save or has no name.

    Reads only the header, so it is cheap enough to call while listing saves. Never raises on
    a bad or unreadable file: an unlabelled save still needs to appear in the picker, so a
    failure here degrades to the file name rather than hiding the save."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(_NAME_OFFSET + _MAX_HEADER_BLOCK)
    except OSError:
        return None
    if not head.startswith(SAVE_MAGIC) or len(head) < _NAME_OFFSET:
        return None
    block_size, = struct.unpack_from("<I", head, _HEADER_SIZE_OFFSET)
    if not 0 < block_size <= _MAX_HEADER_BLOCK:
        return None
    raw = head[_NAME_OFFSET:_NAME_OFFSET + block_size].split(b"\x00", 1)[0]
    name = raw.decode("utf-8", "replace").strip()
    return name or None


@dataclass(frozen=True)
class SaveFile:
    path: Path
    game: str          # e.g. "FC 25", or the containing folder's name for a custom directory
    modified: datetime
    size_bytes: int
    # The in-game career name. None for a file that isn't an EA save, or one saved without a
    # name -- callers fall back to `name` rather than showing a blank row.
    save_name: Optional[str] = None

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def label(self) -> str:
        """What to show the user. The in-game name is what they chose and what they recognise;
        the generated file name is the fallback, not the default."""
        return self.save_name or self.path.name

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


def _documents_roots() -> List[Path]:
    home = Path.home()
    candidates = [home / "Documents", home / "OneDrive" / "Documents"]
    # Business/school OneDrive tenants redirect Documents into "OneDrive - <Org>".
    candidates.extend(sorted(home.glob("OneDrive - */Documents")))
    return [c for c in candidates if c.is_dir()]


def _local_appdata_roots() -> List[Path]:
    local = os.environ.get("LOCALAPPDATA")
    candidates = [Path(local)] if local else []
    candidates.append(Path.home() / "AppData" / "Local")
    return [c for c in candidates if c.is_dir()]


# Game folder names, per root. FC 25 uses "EA SPORTS FC 25" under LocalAppData; the older
# titles use a bare "FC 24" / "FIFA 23" under Documents. Globbed rather than enumerated so a
# future FC 26 is found without a code change.
GAME_DIR_PATTERNS: Tuple[str, ...] = ("EA SPORTS FC *", "FC *", "EA SPORTS FIFA *", "FIFA *")


def _game_roots() -> List[Path]:
    return _local_appdata_roots() + _documents_roots()


def game_save_dirs() -> List[Tuple[str, Path]]:
    """Every game `settings` directory found, as (game label, directory)."""
    found: List[Tuple[str, Path]] = []
    seen = set()
    for root in _game_roots():
        for pattern in GAME_DIR_PATTERNS:
            for game_dir in sorted(root.glob(pattern)):
                settings = game_dir / "settings"
                if not settings.is_dir():
                    continue
                key = str(settings.resolve()).lower()
                if key in seen:
                    continue
                seen.add(key)
                found.append((_game_label(game_dir.name), settings))
    return found


def _game_label(folder_name: str) -> str:
    """"EA SPORTS FC 25" -> "FC 25". The prefix is the same on every folder, so it only makes
    the picker's Game column wider without telling the user anything."""
    prefix = "EA SPORTS "
    return folder_name[len(prefix):] if folder_name.upper().startswith(prefix) else folder_name


def _env_dirs() -> List[Path]:
    raw = os.environ.get(SAVE_DIRS_ENV, "")
    return [Path(part) for part in raw.split(os.pathsep) if part.strip()]


def _scan(directory: Path, game: str) -> List[SaveFile]:
    if not directory.is_dir():
        return []
    saves = []
    for child in sorted(directory.iterdir()):
        if not child.is_file() or not child.name.startswith(SAVE_NAME_PREFIXES):
            continue
        stat = child.stat()
        saves.append(SaveFile(
            path=child,
            game=game,
            modified=datetime.fromtimestamp(stat.st_mtime),
            size_bytes=stat.st_size,
            save_name=read_save_name(child),
        ))
    return saves


def discover_saves(extra_dirs: Iterable[str] = ()) -> List[SaveFile]:
    """All Manager Career saves found in the standard game folders, plus any directory given
    via `extra_dirs` or the MMB_SAVE_DIRS environment variable. Newest first, deduplicated by
    resolved path (the same folder can be reachable through both Documents and OneDrive)."""
    results: List[SaveFile] = []
    seen = set()

    scans: List[Tuple[str, Path]] = list(game_save_dirs())
    scans.extend((d.name or str(d), d) for d in _env_dirs())
    scans.extend((Path(d).name or str(d), Path(d)) for d in extra_dirs)

    for game, directory in scans:
        for save in _scan(directory, game):
            key = str(save.path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(save)

    results.sort(key=lambda s: s.modified, reverse=True)
    return results


def find_save(reference: str, extra_dirs: Iterable[str] = ()) -> Optional[SaveFile]:
    """Resolve a save the user named either by path or by file name (as shown by `mmb saves`)."""
    as_path = Path(reference)
    if as_path.is_file():
        stat = as_path.stat()
        # Inside a game folder the useful label is the game ("FC 25/settings/<save>"); anywhere
        # else, the containing folder is the most informative thing available.
        parent = as_path.parent
        game = parent.parent.name if parent.name.lower() == "settings" else parent.name
        return SaveFile(
            path=as_path,
            game=game or "unknown",
            modified=datetime.fromtimestamp(stat.st_mtime),
            size_bytes=stat.st_size,
            save_name=read_save_name(as_path),
        )
    wanted = reference.strip().lower()
    found = discover_saves(extra_dirs)
    # The in-game name is what the picker shows, so it has to be what `mmb import <name>`
    # accepts; the file name still resolves, for scripts and for saves with no name.
    for save in found:
        if save.save_name and save.save_name.lower() == wanted:
            return save
    for save in found:
        if save.name.lower() == wanted:
            return save
    return None
