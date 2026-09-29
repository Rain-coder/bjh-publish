from typer.testing import CliRunner
from bjh.cli import app

runner = CliRunner()


def test_help_lists_all_commands():
    r = runner.invoke(app, ["--help"])
    assert r.exit_code == 0
    for cmd in ("login", "fetch", "create", "publish", "run", "web"):
        assert cmd in r.output


def test_fetch_requires_keyword():
    r = runner.invoke(app, ["fetch"])
    assert r.exit_code != 0
