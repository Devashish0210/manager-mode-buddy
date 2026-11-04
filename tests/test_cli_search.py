"""CLI-level tests for the V1 workflow: pick a save, import it, filter it.

Nothing here needs a real save file -- `extract` is monkeypatched, the same approach
test_cli_import.py uses -- so these run everywhere. MMB_HOME is redirected per test to keep
the real ~/.manager-buddy untouched.
"""
from typer.testing import CliRunner

from manager_mode_buddy import cli, saves
from manager_mode_buddy.extractor import ExtractionResult
from manager_mode_buddy.models import Player

runner = CliRunner()

SQUAD = [
    Player(id=1001, name="A. Playmaker", position="CAM", other_positions=["CM"], overall=74,
           potential=88, preferred_foot="Left", club_id=9, age=20, birth_date="2007-03-04",
           attributes={"vision": 85, "shortpassing": 80, "finishing": 60}),
    Player(id=1002, name="B. Runner", position="ST", overall=76, potential=79,
           preferred_foot="Right", club_id=9, age=29, birth_date="1998-01-09",
           attributes={"vision": 55, "shortpassing": 62, "finishing": 84}),
    Player(id=1003, name="C. Prospect", position="RCM", overall=64, potential=86,
           preferred_foot="Right", is_free_agent=True, club_id=111592,
           attributes={"vision": 81, "shortpassing": 79, "finishing": 55}),
    Player(id=1004, name="D. Unknown", position="CAM", overall=72, potential=None,
           attributes={}),
    # The decode-artifact shape the real save is full of: an impossible overall next to a
    # maxed-out potential, which would otherwise top every potential-sorted search.
    Player(id=1005, name="E. Artifact", position="ST", overall=1, potential=99,
           attributes={"vision": 99, "shortpassing": 99, "finishing": 99}),
    # The other artifact shape: a believable overall next to an attribute above the game's
    # hard 99 cap.
    Player(id=1006, name="F. Overflow", position="ST", overall=77, potential=98,
           attributes={"vision": 103, "shortpassing": 80, "finishing": 128}),
]


def _fake_extract(save_path, meta_xml_path, names_csv_path=None, game_db_path=None):
    return ExtractionResult(players=list(SQUAD), team_ids=[9], as_of_date="2027-08-21")


def _import(tmp_path, monkeypatch, profile="career_one"):
    """Import the fake squad into a fresh MMB home and return the save file path used."""
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract", _fake_extract)
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    settings = tmp_path / "FC 25" / "settings"
    settings.mkdir(parents=True, exist_ok=True)
    save_file = settings / "ManagerCareer20260101000000"
    save_file.write_bytes(b"\x00")
    result = runner.invoke(
        cli.app,
        ["import", str(save_file), "--meta-xml", str(meta_xml), "--profile", profile],
    )
    assert result.exit_code == 0, result.output
    return save_file


# --- save picker ---------------------------------------------------------------------------

def test_saves_lists_detected_save_files(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "ManagerCareer20260101000000").write_bytes(b"\x00")

    result = runner.invoke(cli.app, ["saves", "--dir", str(settings)])

    assert result.exit_code == 0, result.output
    assert "ManagerCareer20260101000000" in result.output


def test_saves_explains_itself_when_nothing_is_found(tmp_path, monkeypatch):
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    result = runner.invoke(cli.app, ["saves", "--dir", str(tmp_path / "empty")])

    assert result.exit_code == 0
    assert "No Manager Career saves found" in result.output
    assert "MMB_SAVE_DIRS" in result.output


def test_import_with_no_argument_picks_from_the_detected_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    monkeypatch.setattr(cli, "extract", _fake_extract)
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "ManagerCareer20260101000000").write_bytes(b"\x00")

    result = runner.invoke(
        cli.app, ["import", "--dir", str(settings), "--meta-xml", str(meta_xml)], input="1\n"
    )

    assert result.exit_code == 0, result.output
    assert "imported successfully as career profile 'managercareer20260101000000'" in result.output


def test_import_by_save_file_name_resolves_through_the_picker(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    monkeypatch.setattr(cli, "extract", _fake_extract)
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    settings = tmp_path / "settings"
    settings.mkdir()
    (settings / "ManagerCareer20260101000000").write_bytes(b"\x00")

    result = runner.invoke(cli.app, [
        "import", "ManagerCareer20260101000000",
        "--dir", str(settings), "--meta-xml", str(meta_xml), "--profile", "named",
    ])

    assert result.exit_code == 0, result.output
    assert "career profile 'named'" in result.output


def test_import_reports_an_unfindable_save(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.delenv(saves.SAVE_DIRS_ENV, raising=False)
    monkeypatch.setattr(saves, "game_save_dirs", lambda: [])
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")

    result = runner.invoke(cli.app, ["import", "NoSuchSave", "--meta-xml", str(meta_xml)])

    assert result.exit_code == 1
    assert "no save found" in result.output


# --- career selection ----------------------------------------------------------------------

def test_import_makes_the_new_career_active_and_use_switches_it(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch, profile="career_one")
    _import(tmp_path, monkeypatch, profile="career_two")

    listed = runner.invoke(cli.app, ["careers"])
    assert "[career_two]" in listed.output
    assert "* " in listed.output

    switched = runner.invoke(cli.app, ["use", "career_one"])
    assert switched.exit_code == 0, switched.output
    assert "Active career is now 'career_one'" in switched.output
    relisted = runner.invoke(cli.app, ["careers"]).output
    assert "* ManagerCareer20260101000000  [career_one]" in relisted


def test_use_with_no_argument_offers_a_numbered_list(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch, profile="career_one")
    _import(tmp_path, monkeypatch, profile="career_two")

    result = runner.invoke(cli.app, ["use"], input="1\n")

    assert result.exit_code == 0, result.output
    assert "Active career is now 'career_one'" in result.output


def test_use_rejects_an_unknown_profile(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["use", "nope"])
    assert result.exit_code == 1
    assert "no imported career profile" in result.output


def test_search_without_a_career_says_how_to_get_one(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    result = runner.invoke(cli.app, ["search"])
    assert result.exit_code == 1
    assert "no careers imported yet" in result.output


def test_search_asks_which_career_when_several_exist_and_none_is_active(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch, profile="career_one")
    _import(tmp_path, monkeypatch, profile="career_two")
    (tmp_path / ".manager-buddy" / "config" / "active.json").unlink()

    result = runner.invoke(cli.app, ["search"])

    assert result.exit_code == 1
    assert "mmb use" in result.output


# --- search --------------------------------------------------------------------------------

def test_search_filters_by_position_overall_and_attributes(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    result = runner.invoke(cli.app, [
        "search", "--pos", "CAM", "--min-ovr", "70", "--attr", "vision>=80",
        "--attr", "Short Passing >= 78",
    ])

    assert result.exit_code == 0, result.output
    assert "A. Playmaker" in result.output
    assert "B. Runner" not in result.output       # wrong position
    assert "C. Prospect" not in result.output     # overall too low
    assert "D. Unknown" not in result.output      # no parsed attributes
    assert "Matched 1 players" in result.output


def test_search_matches_only_primary_positions_unless_asked(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    primary = runner.invoke(cli.app, ["search", "--pos", "CM"])
    assert "A. Playmaker" not in primary.output   # CM is only a secondary position for them
    assert "C. Prospect" in primary.output        # RCM collapses to CM

    widened = runner.invoke(cli.app, ["search", "--pos", "CM", "--include-secondary"])
    assert "A. Playmaker" in widened.output
    assert "C. Prospect" in widened.output


def test_search_supports_potential_growth_foot_and_free_agent_filters(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    high_potential = runner.invoke(cli.app, ["search", "--min-pot", "85"])
    assert "A. Playmaker" in high_potential.output and "B. Runner" not in high_potential.output

    growth = runner.invoke(cli.app, ["search", "--min-growth", "20"])
    assert "C. Prospect" in growth.output and "A. Playmaker" not in growth.output

    left_footed = runner.invoke(cli.app, ["search", "--foot", "Left"])
    assert "A. Playmaker" in left_footed.output and "B. Runner" not in left_footed.output

    free = runner.invoke(cli.app, ["search", "--free-agents"])
    assert "C. Prospect" in free.output and "A. Playmaker" not in free.output
    assert "free agent" in free.output


def test_search_sorts_and_limits(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    result = runner.invoke(cli.app, ["search", "--sort", "overall", "--limit", "2"])

    assert result.exit_code == 0, result.output
    assert result.output.index("B. Runner") < result.output.index("A. Playmaker")
    assert "C. Prospect" not in result.output     # third by overall, past the limit
    assert "showing 2" in result.output


def test_search_reports_an_unknown_attribute_without_a_traceback(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    result = runner.invoke(cli.app, ["search", "--attr", "wingspan>=80"])

    assert result.exit_code == 1
    assert "unknown attribute 'wingspan'" in result.output


def test_search_hides_implausible_rows_by_default(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    default = runner.invoke(cli.app, ["search", "--pos", "ST"])
    assert default.exit_code == 0, default.output
    assert "E. Artifact" not in default.output
    assert "F. Overflow" not in default.output
    assert "Skipped 2 of 6 rows" in default.output
    assert "B. Runner" in default.output

    kept = runner.invoke(cli.app, ["search", "--pos", "ST", "--include-implausible"])
    assert "E. Artifact" in kept.output
    assert "F. Overflow" in kept.output
    assert "Skipped" not in kept.output


def test_search_explains_an_empty_result(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    result = runner.invoke(cli.app, ["search", "--min-ovr", "99"])

    assert result.exit_code == 0, result.output
    assert "No players matched" in result.output


def test_search_never_reparses_the_save(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    def _broken_extract(*args, **kwargs):
        raise AssertionError("mmb search must read the imported copy, not the save file")

    monkeypatch.setattr(cli, "extract", _broken_extract)

    result = runner.invoke(cli.app, ["search", "--pos", "CAM"])
    assert result.exit_code == 0, result.output


# --- attributes / player detail --------------------------------------------------------------

def test_attributes_lists_only_what_the_career_actually_has(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    result = runner.invoke(cli.app, ["attributes"])

    assert result.exit_code == 0, result.output
    assert "Vision" in result.output and "vision" in result.output
    assert "Short Passing" in result.output
    assert "Sprint Speed" not in result.output    # not parsed for this career


def test_player_shows_detail_for_one_id(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    result = runner.invoke(cli.app, ["player", "1001"])

    assert result.exit_code == 0, result.output
    assert "A. Playmaker" in result.output
    assert "Potential:" in result.output
    assert "Vision" in result.output


def test_player_reports_a_missing_id(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["player", "424242"])
    assert result.exit_code == 1
    assert "no player with id 424242" in result.output


def test_search_by_name_explains_a_career_with_no_names(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract", lambda *a, **k: ExtractionResult(
        players=[Player(id=1001, position="ST", overall=70, potential=80)], team_ids=[]))
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    save_file = tmp_path / "ManagerCareer20260101000000"
    save_file.write_bytes(b"\x00")
    runner.invoke(cli.app, ["import", str(save_file), "--meta-xml", str(meta_xml), "--profile", "nameless"])

    result = runner.invoke(cli.app, ["search", "--name", "Bellingham"])

    assert result.exit_code == 0, result.output
    assert "no player names stored" in result.output


# --- age ------------------------------------------------------------------------------------

def test_search_filters_by_age_and_shows_the_season_date(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    young = runner.invoke(cli.app, ["search", "--max-age", "23"])

    assert young.exit_code == 0, young.output
    assert "A. Playmaker" in young.output
    assert "B. Runner" not in young.output
    assert "age <= 23" in young.output
    # Ages are relative to the career's own date, so the search says which date that is.
    assert "Season date: 2027-08-21" in young.output


def test_player_detail_shows_age_and_date_of_birth(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)

    result = runner.invoke(cli.app, ["player", "1001"])

    assert result.exit_code == 0, result.output
    assert "Age:" in result.output and "20" in result.output
    assert "2007-03-04" in result.output
    assert "as of 2027-08-21" in result.output


def test_age_survives_the_storage_round_trip(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)
    from manager_mode_buddy import storage

    career = storage.load_career("career_one")
    reloaded = next(p for p in career.players if p.id == 1001)

    assert reloaded.age == 20
    assert reloaded.birth_date == "2007-03-04"
    assert career.as_of_date == "2027-08-21"


# --- mmb ask (no API calls: nl.interpret is stubbed) ----------------------------------------

def test_ask_runs_the_interpreted_query_and_shows_how_it_was_read(tmp_path, monkeypatch):
    from manager_mode_buddy import filters as F, nl

    _import(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.nl, "interpret", lambda *a, **k: nl.Interpretation(
        filters=[F.PositionFilter(("CAM",)), F.AgeFilter(maximum=23)],
        sort_by="potential", limit=25,
        reading="Read this as a young attacking midfielder.",
        unsupported=["a price under 10m"],
    ))

    result = runner.invoke(cli.app, ["ask", "young playmaker, cheap"])

    assert result.exit_code == 0, result.output
    assert "Read this as a young attacking midfielder." in result.output
    assert "primary position in CAM; age <= 23" in result.output
    # The honest half: what the model could not express, with MMB's own reason.
    assert "a price under 10m" in result.output and "market value" in result.output
    assert "A. Playmaker" in result.output
    assert "B. Runner" not in result.output


def test_ask_dry_run_does_not_search(tmp_path, monkeypatch):
    from manager_mode_buddy import filters as F, nl

    _import(tmp_path, monkeypatch)
    monkeypatch.setattr(cli.nl, "interpret", lambda *a, **k: nl.Interpretation(
        filters=[F.PositionFilter(("CAM",))], reading="Attacking midfielders."))

    result = runner.invoke(cli.app, ["ask", "playmakers", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert "A. Playmaker" not in result.output


def test_ask_reports_a_translation_failure_without_a_traceback(tmp_path, monkeypatch):
    from manager_mode_buddy import nl

    _import(tmp_path, monkeypatch)

    def _boom(*a, **k):
        raise nl.NLError("no API key found. Set ANTHROPIC_API_KEY ...")

    monkeypatch.setattr(cli.nl, "interpret", _boom)

    result = runner.invoke(cli.app, ["ask", "anything"])

    assert result.exit_code == 1
    assert "no API key found" in result.output


# --- date correction --------------------------------------------------------------------

def test_date_command_recomputes_ages_without_reparsing(tmp_path, monkeypatch):
    """The one number MMB has to estimate is the career's current date -- and the user can read
    it straight off their game. Correcting it must not mean parsing the save again."""
    _import(tmp_path, monkeypatch)

    def _broken_extract(*args, **kwargs):
        raise AssertionError("mmb date must not re-parse the save")

    monkeypatch.setattr(cli, "extract", _broken_extract)

    # A. Playmaker was born 2007-03-04 and was 20 at the imported date of 2027-08-21.
    result = runner.invoke(cli.app, ["date", "2028-03-04"])

    assert result.exit_code == 0, result.output
    assert "now dated 2028-03-04" in result.output
    detail = runner.invoke(cli.app, ["player", "1001"])
    assert "Age:              21" in detail.output


def test_date_command_with_no_argument_reports_the_current_one(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["date"])
    assert result.exit_code == 0, result.output
    assert "2027-08-21" in result.output
    assert "mmb date YYYY-MM-DD" in result.output


def test_date_command_rejects_a_non_date(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)
    result = runner.invoke(cli.app, ["date", "next tuesday"])
    assert result.exit_code != 0
    assert "YYYY-MM-DD" in result.output


def test_unnamed_players_are_explained_not_left_a_mystery(tmp_path, monkeypatch):
    """Names are decoded from the game's own tables now, so a missing one means the shipped
    fifa_ng_db.db wasn't there -- which is fixable, and the fix is what the user needs told.
    (It is no longer true that a career-generated player is unnameable; 462547 here is the
    real regen that motivated the work, and MMB now calls him Benjamin Weber.)"""
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract", lambda *a, **k: ExtractionResult(
        players=[Player(id=462547, position="ST", overall=78, potential=90, age=18)],
        team_ids=[], as_of_date="2027-08-21"))
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    save_file = tmp_path / "ManagerCareer20260101000000"
    save_file.write_bytes(b"\x00")
    runner.invoke(cli.app, ["import", str(save_file), "--meta-xml", str(meta_xml),
                            "--profile", "regens"])

    result = runner.invoke(cli.app, ["player", "462547", "--profile", "regens"])

    assert result.exit_code == 0, result.output
    assert "fifa_ng_db.db" in result.output
    assert "--game-db" in result.output


def test_decoded_names_are_marked_as_coming_from_the_save(tmp_path, monkeypatch):
    """A name off the CSV join and a name decoded from the game are not equally trustworthy,
    so the source travels with the player through storage."""
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract", lambda *a, **k: ExtractionResult(
        players=[Player(id=462547, name="Benjamin Weber", name_source="save",
                        position="ST", overall=78, potential=90, age=18)],
        team_ids=[], as_of_date="2027-08-21"))
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    save_file = tmp_path / "ManagerCareer20260101000000"
    save_file.write_bytes(b"\x00")
    runner.invoke(cli.app, ["import", str(save_file), "--meta-xml", str(meta_xml),
                            "--profile", "regens"])

    from manager_mode_buddy.storage import load_career
    stored = load_career("regens").players[0]
    assert (stored.name, stored.name_source) == ("Benjamin Weber", "save")

    result = runner.invoke(cli.app, ["player", "462547", "--profile", "regens"])
    assert result.exit_code == 0, result.output
    assert "Benjamin Weber" in result.output
    assert "fifa_ng_db.db" not in result.output


# --- interactive scout ------------------------------------------------------------------

def test_scout_lists_saves_by_file_name_and_confirms_the_date(tmp_path, monkeypatch):
    """The picker has to show the save's own file name -- a profile slug doesn't tell you which
    career you're about to search."""
    from manager_mode_buddy import saves as saves_module

    _import(tmp_path, monkeypatch)
    monkeypatch.setattr(saves_module, "discover_saves", lambda *a, **k: [])
    monkeypatch.setattr(cli, "discover_saves", lambda *a, **k: [])

    # pick save 1, accept the offered date, then blank out of the query loop
    result = runner.invoke(cli.app, ["scout"], input="1\n\n\n")

    assert result.exit_code == 0, result.output
    assert "ManagerCareer20260101000000" in result.output     # the save file, not the slug
    assert "2027-08-21" in result.output                      # date offered for confirmation
    assert "scoutable players" in result.output


def test_scout_applies_a_corrected_date_to_ages(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "discover_saves", lambda *a, **k: [])

    result = runner.invoke(cli.app, ["scout"], input="1\n2028-03-04\n\n")

    assert result.exit_code == 0, result.output
    assert "Dated 2028-03-04" in result.output
    assert "ages updated" in result.output


def test_scout_falls_back_to_questions_without_an_api_key(tmp_path, monkeypatch):
    _import(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "discover_saves", lambda *a, **k: [])
    for names in cli.nl.KEY_ENVS.values():
        for name in names:
            monkeypatch.delenv(name, raising=False)

    # save 1, keep date, position CAM, skip the numeric questions, then blank to exit
    result = runner.invoke(cli.app, ["scout"], input="1\n\nCAM\n\n\n\n\n\n")

    assert result.exit_code == 0, result.output
    assert "No API key set" in result.output
    assert "A. Playmaker" in result.output


def test_scout_uses_plain_english_when_a_key_is_present(tmp_path, monkeypatch):
    from manager_mode_buddy import filters as F, nl

    _import(tmp_path, monkeypatch)
    monkeypatch.setattr(cli, "discover_saves", lambda *a, **k: [])
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    monkeypatch.setattr(cli.nl, "interpret", lambda *a, **k: nl.Interpretation(
        filters=[F.PositionFilter(("CAM",))], reading="Attacking midfielders."))

    result = runner.invoke(cli.app, ["scout"], input="1\n\nplaymakers\n\n\n")

    assert result.exit_code == 0, result.output
    assert "Read as: Attacking midfielders." in result.output
    assert "A. Playmaker" in result.output
