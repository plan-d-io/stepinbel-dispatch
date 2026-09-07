"""Front-end release identifier. Independent of the simulator version."""

from __future__ import annotations

from pathlib import Path


class VersionFileError(RuntimeError):
    """Raised when ui/VERSION is missing or invalid."""


def read_ui_version(path: Path | None = None) -> str:
    source = Path(__file__).resolve().parent / "VERSION" if path is None else Path(path)
    if not source.is_file():
        raise VersionFileError(f"UI VERSION file is missing: {source}")
    raw = source.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if len(lines) != 1:
        raise VersionFileError("UI VERSION must contain exactly one line.")
    value = lines[0].strip()
    if not value:
        raise VersionFileError("UI VERSION is empty.")
    return value


UI_VERSION = read_ui_version()
