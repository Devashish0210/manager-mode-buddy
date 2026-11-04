from typer.testing import CliRunner

from manager_mode_buddy.cli import app

runner = CliRunner()


def test_help_exits_cleanly():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Manager Mode Buddy" in result.stdout


def test_version_command():
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "Manager Mode Buddy v" in result.stdout
