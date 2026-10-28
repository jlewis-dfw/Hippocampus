"""scripts/system_specs.py: the file it writes holds versions and sizes only, and it refuses to write where git would not ignore it."""
from __future__ import annotations

import getpass
import importlib.util
import socket
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "system_specs.py"


@pytest.fixture(scope="module")
def specs():
    spec = importlib.util.spec_from_file_location("system_specs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_text_has_no_user_host_or_path(specs) -> None:
    text = specs.build()
    for private in (str(Path.home()), getpass.getuser(), socket.gethostname().split(".")[0], str(SCRIPT.parents[1])):
        assert private not in text
    assert "## Container engines" in text and "**Container engine to use:**" in text


def test_it_refuses_a_file_git_does_not_ignore(specs, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setattr(specs, "OUT", specs.ROOT / "not-ignored-specs.md")
    monkeypatch.setattr(specs, "build", lambda: "text")
    assert specs.main([]) == 2
    assert not (specs.ROOT / "not-ignored-specs.md").exists()
    assert "git does not ignore it" in capsys.readouterr().err


def test_print_mode_writes_nothing(specs, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(specs, "OUT", tmp_path / "system-specs.md")
    monkeypatch.setattr(specs, "build", lambda: "text")
    assert specs.main(["--print"]) == 0
    assert not (tmp_path / "system-specs.md").exists()
