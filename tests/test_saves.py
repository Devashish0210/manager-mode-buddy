from manager_mode_buddy import saves


def _make_save(directory, name, size=1024):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"\x00" * size)
    return path


def test_discover_finds_manager_career_saves_in_a_given_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    settings = tmp_path / "FC 25" / "settings"
    _make_save(settings, "ManagerCareer20260101000000")

    found = saves.discover_saves([str(settings)])

    assert [s.name for s in found] == ["ManagerCareer20260101000000"]
    assert found[0].size_bytes == 1024


def test_discover_ignores_non_career_files(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    settings = tmp_path / "settings"
    _make_save(settings, "ManagerCareer20260101000000")
    _make_save(settings, "PlayerCareer20260101000000")  # Player Career: a different mode
    _make_save(settings, "Settings20260101000000")

    found = saves.discover_saves([str(settings)])

    assert [s.name for s in found] == ["ManagerCareer20260101000000"]


def test_discover_reads_the_environment_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    settings = tmp_path / "elsewhere"
    _make_save(settings, "ManagerCareer20251212121212")
    monkeypatch.setenv(saves.SAVE_DIRS_ENV, str(settings))

    assert [s.name for s in saves.discover_saves()] == ["ManagerCareer20251212121212"]


def test_discover_sorts_newest_first_and_deduplicates(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    settings = tmp_path / "settings"
    older = _make_save(settings, "ManagerCareer20250101000000")
    newer = _make_save(settings, "ManagerCareer20260101000000")
    import os
    os.utime(older, (1_600_000_000, 1_600_000_000))
    os.utime(newer, (1_700_000_000, 1_700_000_000))

    # The same directory listed twice (the OneDrive/Documents aliasing case) must not double up.
    found = saves.discover_saves([str(settings), str(settings)])

    assert [s.name for s in found] == [
        "ManagerCareer20260101000000",
        "ManagerCareer20250101000000",
    ]


def test_discover_tolerates_a_missing_directory(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    assert saves.discover_saves([str(tmp_path / "nope")]) == []


def test_find_save_accepts_a_path_or_a_listed_file_name(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    settings = tmp_path / "settings"
    path = _make_save(settings, "ManagerCareer20260101000000")

    by_path = saves.find_save(str(path))
    by_name = saves.find_save("ManagerCareer20260101000000", [str(settings)])

    assert by_path is not None and by_path.path == path
    assert by_name is not None and by_name.path == path
    assert saves.find_save("ManagerCareer99999999", [str(settings)]) is None


def test_discover_finds_fc25_saves_under_local_appdata(tmp_path, monkeypatch):
    r"""FC 25 writes to %LOCALAPPDATA%\EA SPORTS FC 25\settings, not Documents -- the older
    FIFA/FC layout. Both are searched; this covers the current one."""
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "_documents_roots", lambda: [])
    monkeypatch.setattr(saves, "_local_appdata_roots", lambda: [tmp_path])
    _make_save(tmp_path / "EA SPORTS FC 25" / "settings", "ManagerCareer20260908152939088")

    found = saves.discover_saves()

    assert [(s.name, s.game) for s in found] == [
        ("ManagerCareer20260908152939088", "FC 25"),  # "EA SPORTS " trimmed for display
    ]


def test_discover_finds_the_older_documents_layout_too(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "_local_appdata_roots", lambda: [])
    monkeypatch.setattr(saves, "_documents_roots", lambda: [tmp_path])
    _make_save(tmp_path / "FC 24" / "settings", "ManagerCareer20250101000000")

    assert [(s.name, s.game) for s in saves.discover_saves()] == [
        ("ManagerCareer20250101000000", "FC 24"),
    ]


def test_a_game_folder_without_a_settings_directory_is_skipped(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "_documents_roots", lambda: [])
    monkeypatch.setattr(saves, "_local_appdata_roots", lambda: [tmp_path])
    (tmp_path / "EA SPORTS FC 25").mkdir()

    assert saves.discover_saves() == []
