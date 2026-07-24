from tracelane import __version__
from tracelane.cli import main


def test_package_exposes_version() -> None:
    assert __version__ == "0.1.0"


def test_cli_help_exits_successfully(capsys) -> None:
    assert main(["--help"]) == 0
    assert "trace-first evaluation harness" in capsys.readouterr().out.lower()
