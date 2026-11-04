from typer.testing import CliRunner

from manager_mode_buddy import cli
from manager_mode_buddy.extractor import ExtractionResult
from manager_mode_buddy.models import Player

runner = CliRunner()


def _fake_extract(save_path, meta_xml_path, names_csv_path=None, game_db_path=None):
    return ExtractionResult(
        players=[Player(id=123456, overall=75, potential=87, position="CAM",
                         overall_source="career_playergrowthuserseason")],
        team_ids=[999],
    )


def test_import_persists_career_and_reports_success(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract", _fake_extract)

    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    fake_save = tmp_path / "Arsenal 2027.save"
    fake_save.write_bytes(b"\x00")

    result = runner.invoke(cli.app, ["import", str(fake_save), "--meta-xml", str(meta_xml)])

    assert result.exit_code == 0, result.output
    assert "imported successfully as career profile 'arsenal_2027'" in result.output
    assert "Players: 1" in result.output

    careers_result = runner.invoke(cli.app, ["careers"])
    assert careers_result.exit_code == 0
    assert "arsenal_2027" in careers_result.output
    assert "players=1" in careers_result.output


def test_import_accepts_explicit_profile_name(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract", _fake_extract)

    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    fake_save = tmp_path / "save.bin"
    fake_save.write_bytes(b"\x00")

    result = runner.invoke(
        cli.app,
        ["import", str(fake_save), "--meta-xml", str(meta_xml), "--profile", "my_custom_career"],
    )

    assert result.exit_code == 0, result.output
    assert "career profile 'my_custom_career'" in result.output


def test_careers_shows_no_import_message_when_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    result = runner.invoke(cli.app, ["careers"])
    assert result.exit_code == 0
    assert "No imported careers yet" in result.output


def test_careers_show_one_profile_reopens_without_reparsing(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    monkeypatch.setattr(cli, "extract", _fake_extract)

    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    fake_save = tmp_path / "save.bin"
    fake_save.write_bytes(b"\x00")
    runner.invoke(cli.app, ["import", str(fake_save), "--meta-xml", str(meta_xml), "--profile", "p1"])

    # Deliberately break extraction to prove `mmb careers p1` never calls it again.
    def _broken_extract(*args, **kwargs):
        raise AssertionError("mmb careers should not re-parse the original save")

    monkeypatch.setattr(cli, "extract", _broken_extract)

    result = runner.invoke(cli.app, ["careers", "p1"])
    assert result.exit_code == 0, result.output
    assert "Career profile: p1" in result.output
    assert "Players: 1" in result.output


def test_careers_unknown_profile_reports_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MMB_HOME", str(tmp_path / ".manager-buddy"))
    result = runner.invoke(cli.app, ["careers", "nope"])
    assert result.exit_code == 1
    assert "no imported career profile" in result.output.lower()
