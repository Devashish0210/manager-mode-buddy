"""The in-game save name, which is what the user recognises a career by.

The game names save *files* by timestamp (`ManagerCareer20260908152939088`), so a picker that
lists file names can't tell two careers apart. The name typed in game is in the file's header;
these tests cover reading it and falling back gracefully when it isn't there.
"""
import os
import struct
from pathlib import Path

from manager_mode_buddy.saves import SAVE_MAGIC, discover_saves, find_save, read_save_name


def _write_save(path: Path, name: bytes | None, payload: bytes = b"\x00" * 16,
                 block_size: int = 184) -> Path:
    header = SAVE_MAGIC + struct.pack("<HII", 1, block_size, len(payload))
    block = bytearray(block_size)
    if name is not None:
        block[:len(name)] = name
    path.write_bytes(header + bytes(block) + payload)
    return path


def test_reads_the_name_the_user_typed_in_game(tmp_path):
    save = _write_save(tmp_path / "ManagerCareer20260908152939088", b"Signed Hartmann")
    assert read_save_name(save) == "Signed Hartmann"


def test_handles_a_non_ascii_name(tmp_path):
    save = _write_save(tmp_path / "ManagerCareer1", "Köln aufstieg".encode("utf-8"))
    assert read_save_name(save) == "Köln aufstieg"


def test_an_unnamed_save_reads_as_none_not_an_empty_string(tmp_path):
    """Callers fall back to the file name on None. An empty string would put a blank row in
    the picker instead."""
    assert read_save_name(_write_save(tmp_path / "ManagerCareer1", None)) is None
    assert read_save_name(_write_save(tmp_path / "ManagerCareer2", b"   ")) is None


def test_a_file_that_is_not_an_ea_save_reads_as_none(tmp_path):
    other = tmp_path / "ManagerCareer1"
    other.write_bytes(b"not an EA save at all" + b"\x00" * 200)
    assert read_save_name(other) is None


def test_a_missing_or_truncated_file_does_not_raise(tmp_path):
    """A save being listed must never depend on it being readable -- a locked or partial file
    should degrade to no name, not take down `mmb saves`."""
    assert read_save_name(tmp_path / "nothing-here") is None
    truncated = tmp_path / "ManagerCareer1"
    truncated.write_bytes(SAVE_MAGIC + b"\x01")
    assert read_save_name(truncated) is None


def test_an_implausible_header_size_is_rejected(tmp_path):
    save = _write_save(tmp_path / "ManagerCareer1", b"Signed Hartmann", block_size=0)
    assert read_save_name(save) is None


def test_discovery_labels_saves_by_their_in_game_name(tmp_path, monkeypatch):
    from manager_mode_buddy import saves as saves_module
    monkeypatch.setattr(saves_module, "game_save_dirs", lambda: [])
    monkeypatch.delenv(saves_module.SAVE_DIRS_ENV, raising=False)
    settings = tmp_path / "settings"
    settings.mkdir()
    _write_save(settings / "ManagerCareer20260908152939088", b"Signed Hartmann")
    _write_save(settings / "ManagerCareer20260906130310265", None)

    found = {s.name: s for s in discover_saves([str(settings)])}
    assert found["ManagerCareer20260908152939088"].label == "Signed Hartmann"
    # No name in the file, so the label falls back to the file name rather than going blank.
    assert found["ManagerCareer20260906130310265"].label == "ManagerCareer20260906130310265"


def test_a_save_can_be_found_by_its_in_game_name(tmp_path, monkeypatch):
    """The picker shows the in-game name, so that has to be what `mmb import <name>` takes."""
    from manager_mode_buddy import saves as saves_module
    monkeypatch.setattr(saves_module, "game_save_dirs", lambda: [])
    monkeypatch.delenv(saves_module.SAVE_DIRS_ENV, raising=False)
    settings = tmp_path / "settings"
    settings.mkdir()
    _write_save(settings / "ManagerCareer20260908152939088", b"Signed Hartmann")

    found = find_save("signed hartmann", [str(settings)])
    assert found is not None
    assert found.name == "ManagerCareer20260908152939088"
    # The file name still resolves, for scripts and for saves with no in-game name.
    assert find_save("ManagerCareer20260908152939088", [str(settings)]) is not None


def test_a_full_path_still_resolves_and_carries_the_name(tmp_path):
    save = _write_save(tmp_path / "ManagerCareer20260908152939088", b"Signed Hartmann")
    found = find_save(str(save))
    assert found is not None and found.save_name == "Signed Hartmann"


def test_default_profile_name_comes_from_the_in_game_name():
    from manager_mode_buddy.storage import default_profile_name
    assert default_profile_name("/x/ManagerCareer20260908152939088", "Signed Hartmann") == \
        "signed_hartmann"
    # No in-game name: fall back to the file's own name rather than to nothing.
    assert default_profile_name("/x/ManagerCareer20260908152939088") == \
        "managercareer20260908152939088"


def test_career_metadata_label_survives_the_save_file_being_deleted(tmp_path, monkeypatch):
    """A career outlives the file it came from -- the next autosave can overwrite it. The name
    is stored at import so the career stays identifiable afterwards."""
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    from manager_mode_buddy.models import ParsedCareer
    from manager_mode_buddy.storage import list_careers, save_career

    save = _write_save(tmp_path / "ManagerCareer20260908152939088", b"Signed Hartmann")
    career = ParsedCareer(save_path=str(save), players=[], team_ids=[])
    save_career("signed_hartmann", career, read_save_name(save))
    save.unlink()

    listed = list_careers()
    assert [c.label for c in listed] == ["Signed Hartmann"]


def test_a_career_imported_before_names_existed_recovers_its_name(tmp_path, monkeypatch):
    """Older metadata.json files have no save_name. Rather than make the user re-import, it is
    read back off the save file when that file is still on disk."""
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    import json

    from manager_mode_buddy.storage import list_careers, profile_dir

    save = _write_save(tmp_path / "ManagerCareer20260908152939088", b"Signed Hartmann")
    directory = profile_dir("old_profile")
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text(json.dumps({
        "profile": "old_profile", "save_path": str(save),
        "imported_at": "2026-09-08T00:00:00+00:00",
        "player_count": 10, "team_count": 2,
    }), encoding="utf-8")

    assert [c.label for c in list_careers()] == ["Signed Hartmann"]


def test_unknown_metadata_keys_do_not_break_listing(tmp_path, monkeypatch):
    """A profile written by a newer MMB should still list, not raise."""
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    import json

    from manager_mode_buddy.storage import list_careers, profile_dir

    directory = profile_dir("future")
    directory.mkdir(parents=True)
    (directory / "metadata.json").write_text(json.dumps({
        "profile": "future", "save_path": "/gone", "imported_at": "2026-09-08T00:00:00+00:00",
        "player_count": 1, "team_count": 1, "save_name": "Later Career",
        "something_from_the_future": 42,
    }), encoding="utf-8")

    assert [c.label for c in list_careers()] == ["Later Career"]


# --- the interactive picker ------------------------------------------------------------------

def _picker_rows(output: str) -> list[str]:
    """The table's data rows, in display order."""
    return [line for line in output.splitlines()
            if line.startswith("│") and "Save " not in line and "─" not in line]


def test_picker_orders_by_when_the_game_last_saved(tmp_path, monkeypatch):
    """FC writes a new timestamped file on every save, so playing on leaves an imported career
    beside a newer unimported file with the same in-game name. Newest first puts the one the
    user just made at the top, and the Saved column is what tells them apart."""
    from typer.testing import CliRunner

    from manager_mode_buddy import cli, saves as saves_module
    from manager_mode_buddy.extractor import ExtractionResult
    from manager_mode_buddy.models import ParsedCareer
    from manager_mode_buddy.storage import save_career

    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves_module.SAVE_DIRS_ENV, raising=False)
    settings = tmp_path / "settings"
    settings.mkdir()
    older = _write_save(settings / "ManagerCareer20260908152939088", b"Signed Hartmann")
    newer = _write_save(settings / "ManagerCareer20260912132033158", b"Signed Hartmann")
    os.utime(older, (1757000000, 1757000000))   # 2025-09-04
    os.utime(newer, (1757600000, 1757600000))   # 2025-09-11
    monkeypatch.setattr(saves_module, "game_save_dirs", lambda: [("FC 25", settings)])
    monkeypatch.setattr(cli, "extract",
                        lambda *a, **k: ExtractionResult(players=[], team_ids=[]))

    save_career("signed_hartmann",
                 ParsedCareer(save_path=str(older), players=[], team_ids=[]),
                 "Signed Hartmann")

    result = CliRunner().invoke(cli.app, ["scout"], input="\x04")
    rows = _picker_rows(result.output)

    # The unimported newer file leads; the imported older one follows.
    assert "not imported" in rows[0] and "2025-09-11" in rows[0]
    assert "imported" in rows[1] and "2025-09-04" in rows[1]
    assert "not imported" in result.output and "MMB hasn't read that save file yet" in result.output


def test_picker_survives_a_career_whose_save_file_is_gone(tmp_path, monkeypatch):
    """A deleted save leaves the career with a UTC-aware import timestamp as its only date,
    which must still sort against other rows' naive file mtimes rather than raising."""
    from typer.testing import CliRunner

    from manager_mode_buddy import cli, saves as saves_module
    from manager_mode_buddy.models import ParsedCareer
    from manager_mode_buddy.storage import save_career

    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves_module.SAVE_DIRS_ENV, raising=False)
    settings = tmp_path / "settings"
    settings.mkdir()
    present = _write_save(settings / "ManagerCareer20260912132033158", b"Exp")
    monkeypatch.setattr(saves_module, "game_save_dirs", lambda: [("FC 25", settings)])

    save_career("gone", ParsedCareer(save_path=str(tmp_path / "deleted"), players=[],
                                      team_ids=[]), "Deleted Career")

    result = CliRunner().invoke(cli.app, ["scout"], input="\x04")
    assert "Deleted Career" in result.output
    assert "Exp" in result.output
    assert present.exists()


def test_picker_flags_an_imported_career_that_has_been_saved_again(tmp_path, monkeypatch):
    """The trap this catches: FC replaced the file the career was imported from, so the career
    silently holds an older session's players. Nothing on the row would say so otherwise."""
    from typer.testing import CliRunner

    from manager_mode_buddy import cli, saves as saves_module
    from manager_mode_buddy.models import ParsedCareer
    from manager_mode_buddy.storage import save_career

    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves_module.SAVE_DIRS_ENV, raising=False)
    settings = tmp_path / "settings"
    settings.mkdir()
    older = _write_save(settings / "ManagerCareer20260908152939088", b"Signed Hartmann")
    newer = _write_save(settings / "ManagerCareer20260912132033158", b"Signed Hartmann")
    os.utime(older, (1757000000, 1757000000))
    os.utime(newer, (1757600000, 1757600000))
    monkeypatch.setattr(saves_module, "game_save_dirs", lambda: [("FC 25", settings)])

    save_career("signed_hartmann",
                 ParsedCareer(save_path=str(older), players=[], team_ids=[]),
                 "Signed Hartmann")

    result = CliRunner().invoke(cli.app, ["scout"], input="\x04")
    assert "OUTDATED" in result.output
    assert "bring it up to date" in result.output


def test_picker_does_not_cry_outdated_over_a_different_career(tmp_path, monkeypatch):
    """A newer save of some *other* career says nothing about this one."""
    from typer.testing import CliRunner

    from manager_mode_buddy import cli, saves as saves_module
    from manager_mode_buddy.models import ParsedCareer
    from manager_mode_buddy.storage import save_career

    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves_module.SAVE_DIRS_ENV, raising=False)
    settings = tmp_path / "settings"
    settings.mkdir()
    mine = _write_save(settings / "ManagerCareer20260908152939088", b"Signed Hartmann")
    other = _write_save(settings / "ManagerCareer20260912132033158", b"Exp")
    os.utime(mine, (1757000000, 1757000000))
    os.utime(other, (1757600000, 1757600000))
    monkeypatch.setattr(saves_module, "game_save_dirs", lambda: [("FC 25", settings)])

    save_career("signed_hartmann", ParsedCareer(save_path=str(mine), players=[], team_ids=[]),
                 "Signed Hartmann")

    result = CliRunner().invoke(cli.app, ["scout"], input="\x04")
    assert "OUTDATED" not in result.output


def test_a_career_whose_file_is_gone_is_judged_against_when_it_was_imported(tmp_path,
                                                                             monkeypatch):
    """When FC replaces the file a career came from, the career's only remaining date is its
    import time -- so a save written *before* that import is not evidence the career is
    behind, and must not be flagged as though it were."""
    from typer.testing import CliRunner

    from manager_mode_buddy import cli, saves as saves_module
    from manager_mode_buddy.models import ParsedCareer
    from manager_mode_buddy.storage import save_career

    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves_module.SAVE_DIRS_ENV, raising=False)
    settings = tmp_path / "settings"
    settings.mkdir()
    stale = _write_save(settings / "ManagerCareer20260912132033158", b"Signed Hartmann")
    os.utime(stale, (1757600000, 1757600000))  # long before this import happens
    monkeypatch.setattr(saves_module, "game_save_dirs", lambda: [("FC 25", settings)])

    save_career("signed_hartmann",
                 ParsedCareer(save_path=str(settings / "gone-ManagerCareer"), players=[],
                               team_ids=[]), "Signed Hartmann")

    result = CliRunner().invoke(cli.app, ["scout"], input="\x04")
    assert "OUTDATED" not in result.output
