from manager_mode_buddy.names import NameTables, resolve_game_db

STRINGS = {1: "Benjamin", 2: "Weber", 3: "Rodri", 4: "Hernández"}


def _row(**kw):
    row = {"playerid": 1, "firstnameid": 0, "lastnameid": 0, "commonnameid": 0}
    row.update(kw)
    return row


def test_first_and_last_name_are_joined():
    tables = NameTables(strings=dict(STRINGS))
    assert tables.player_name(_row(firstnameid=1, lastnameid=2)) == "Benjamin Weber"


def test_common_name_wins_over_first_and_last():
    """The game shows a player's common name when they have one, so MMB has to as well --
    otherwise a player known by one name gets displayed under a name nobody uses."""
    tables = NameTables(strings=dict(STRINGS))
    assert tables.player_name(_row(firstnameid=1, lastnameid=2, commonnameid=3)) == "Rodri"


def test_an_in_game_edit_wins_over_the_decoded_name():
    tables = NameTables(
        strings=dict(STRINGS),
        edited={7: {"firstname": "Ben", "surname": "Webber", "commonname": "",
                    "playerjerseyname": "WEBBER"}},
    )
    row = _row(playerid=7, firstnameid=1, lastnameid=2)
    assert tables.player_name(row) == "Ben Webber"


def test_an_empty_edit_row_falls_through_to_the_decoded_name():
    """editedplayernames has fixed capacity and blank slots; a blank one must not blank out a
    player who does have a perfectly good decoded name."""
    tables = NameTables(
        strings=dict(STRINGS),
        edited={7: {"firstname": "", "surname": "", "commonname": "", "playerjerseyname": ""}},
    )
    row = _row(playerid=7, firstnameid=1, lastnameid=2)
    assert tables.player_name(row) == "Benjamin Weber"


def test_a_player_with_only_a_surname_gets_just_that():
    tables = NameTables(strings=dict(STRINGS))
    assert tables.player_name(_row(lastnameid=4)) == "Hernández"


def test_no_name_ids_at_all_returns_none_rather_than_an_empty_string():
    tables = NameTables(strings=dict(STRINGS))
    assert tables.player_name(_row()) is None


def test_unknown_name_ids_return_none():
    tables = NameTables(strings=dict(STRINGS))
    assert tables.player_name(_row(firstnameid=999, lastnameid=998)) is None


def test_resolve_game_db_defaults_to_the_meta_xml_sibling(tmp_path):
    meta = tmp_path / "fifa_ng_db-meta.xml"
    meta.write_text("<database></database>")
    assert resolve_game_db(str(meta)) is None
    db = tmp_path / "fifa_ng_db.db"
    db.write_bytes(b"\x00")
    assert resolve_game_db(str(meta)) == db


def test_resolve_game_db_prefers_an_explicit_path(tmp_path):
    meta = tmp_path / "fifa_ng_db-meta.xml"
    meta.write_text("<database></database>")
    (tmp_path / "fifa_ng_db.db").write_bytes(b"\x00")
    elsewhere = tmp_path / "other.db"
    elsewhere.write_bytes(b"\x00")
    assert resolve_game_db(str(meta), str(elsewhere)) == elsewhere


def test_resolve_game_db_returns_none_for_a_bad_explicit_path(tmp_path):
    """Silently falling back to the sibling would hide a typo'd --game-db behind names that
    still work; returning None surfaces it."""
    meta = tmp_path / "fifa_ng_db-meta.xml"
    meta.write_text("<database></database>")
    (tmp_path / "fifa_ng_db.db").write_bytes(b"\x00")
    assert resolve_game_db(str(meta), str(tmp_path / "nope.db")) is None
