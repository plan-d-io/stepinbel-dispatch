from __future__ import annotations

import re
from pathlib import Path

import pytest

from ui.presentation.shell import UI_VERSION as SHELL_UI_VERSION
from ui.presentation.shell import SIMULATOR_VERSION as SHELL_SIMULATOR_VERSION
from ui.version import UI_VERSION, VersionFileError, read_ui_version

ROOT = Path(__file__).resolve().parents[2]
UI_VERSION_FILE = ROOT / "ui" / "VERSION"
UI_READER = ROOT / "ui" / "version.py"
SIMULATOR_VERSION_FILE = ROOT / "src" / "stepinbel" / "VERSION"
_LITERAL_RE = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


def test_ui_version_has_a_single_file_source() -> None:
    assert UI_VERSION_FILE.read_text(encoding="utf-8") == "0.3.1\n" or (
        UI_VERSION_FILE.read_text(encoding="utf-8").strip() == "0.3.1"
    )
    assert UI_VERSION == UI_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert UI_VERSION == read_ui_version()
    assert SHELL_UI_VERSION is UI_VERSION
    assert _VERSION_RE.fullmatch(UI_VERSION)


def test_simulator_version_comes_from_public_core() -> None:
    from stepinbel import __version__ as simulator_version

    assert simulator_version == SIMULATOR_VERSION_FILE.read_text(encoding="utf-8").strip()
    assert simulator_version == "0.3.0"
    assert SHELL_SIMULATOR_VERSION == simulator_version
    assert UI_VERSION == "0.3.1"
    assert SHELL_UI_VERSION == "0.3.1"
    assert UI_VERSION != simulator_version


def test_version_file_validation(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(VersionFileError):
        read_ui_version(missing)
    empty = tmp_path / "empty"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(VersionFileError):
        read_ui_version(empty)
    blank = tmp_path / "blank"
    blank.write_text("   \n", encoding="utf-8")
    with pytest.raises(VersionFileError):
        read_ui_version(blank)
    many = tmp_path / "many"
    many.write_text("0.1.0\nextra\n", encoding="utf-8")
    with pytest.raises(VersionFileError):
        read_ui_version(many)
    ok = tmp_path / "ok"
    ok.write_text("1.2.3\n", encoding="utf-8")
    assert read_ui_version(ok) == "1.2.3"


def test_ui_version_reader_has_no_fallback_literal() -> None:
    text = UI_READER.read_text(encoding="utf-8")
    assert _LITERAL_RE.search(text) is None


def test_product_code_does_not_parse_project_metadata() -> None:
    root = Path(__file__).resolve().parents[1]
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        if "pyproject.toml" in text:
            offenders.append(str(path.relative_to(root)))
    assert offenders == []
