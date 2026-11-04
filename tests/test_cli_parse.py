from typer.testing import CliRunner

from manager_mode_buddy.cli import app

runner = CliRunner()


def test_parse_without_meta_xml_fails_clearly(tmp_path, monkeypatch):
    monkeypatch.delenv("MMB_META_XML", raising=False)
    fake_save = tmp_path / "ManagerCareerFake"
    fake_save.write_bytes(b"\x00")
    result = runner.invoke(app, ["parse", str(fake_save)])
    assert result.exit_code != 0
    assert "meta-xml" in result.output.lower() or "meta_xml" in result.output.lower()


def test_parse_missing_save_file_reports_error(tmp_path):
    meta_xml = tmp_path / "fifa_ng_db-meta.xml"
    meta_xml.write_text("<database></database>")
    result = runner.invoke(app, ["parse", str(tmp_path / "does_not_exist"), "--meta-xml", str(meta_xml)])
    assert result.exit_code == 1
    assert "save file not found" in result.output.lower()


def test_parse_missing_meta_xml_file_reports_error(tmp_path):
    fake_save = tmp_path / "ManagerCareerFake"
    fake_save.write_bytes(b"\x00")
    result = runner.invoke(
        app, ["parse", str(fake_save), "--meta-xml", str(tmp_path / "does_not_exist.xml")]
    )
    assert result.exit_code == 1
    assert "meta.xml not found" in result.output.lower() or "not found" in result.output.lower()
