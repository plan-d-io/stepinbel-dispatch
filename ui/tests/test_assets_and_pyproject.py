from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path

from PIL import Image

from ui.presentation.footer import ENERGENT_ASSET, FPS_ASSET
from ui.presentation.shell import header_logo_size, logo_image_bytes, logo_path
from ui.presentation.styles import stylesheet
from ui.presentation.tokens import (
    ENERGENT_FOOTER_DISPLAY_HEIGHT_PX,
    ENERGENT_FOOTER_DISPLAY_WIDTH_PX,
    FPS_FOOTER_DISPLAY_HEIGHT_PX,
    FPS_FOOTER_DISPLAY_WIDTH_PX,
    FPS_LOGO_HEIGHT_PX,
    FPS_LOGO_WIDTH_PX,
    STEPINBEL_LOGO_HEIGHT_PX,
    STEPINBEL_LOGO_NAME,
    STEPINBEL_LOGO_WIDTH_PX,
)

ROOT = Path(__file__).resolve().parents[2]
ASSETS = ROOT / "ui" / "assets"
STEPINBEL = ASSETS / "STEPinBEL-logo2.png"
FPS = ASSETS / "LOGO-economie-SPF.png"
ENERGENT = ASSETS / "Energent.png"
PLAN_D = ASSETS / "Plan D-small-transparent.png"
PYPROJECT = ROOT / "pyproject.toml"
AUTHORS = ROOT / "AUTHORS.md"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest().upper()


def test_user_provided_asset_hashes_unchanged() -> None:
    assert STEPINBEL.is_file()
    assert STEPINBEL_LOGO_NAME == "STEPinBEL-logo2.png"
    assert STEPINBEL.stat().st_size == 8818
    assert _sha256(STEPINBEL) == "EA98B3F8235CC4E7965797360AD69945F650FE808E91657EDAB042E632AE2F5E"
    assert Image.open(STEPINBEL).size == (STEPINBEL_LOGO_WIDTH_PX, STEPINBEL_LOGO_HEIGHT_PX)
    assert Image.open(STEPINBEL).size == (213, 80)
    assert Image.open(STEPINBEL).mode == "RGBA"
    assert FPS.is_file()
    assert _sha256(FPS) == "639495263A3019CE6E4514ACFA47D60AEF91661B5384A3604565DFEACE664EDF"
    assert Image.open(FPS).size == (FPS_LOGO_WIDTH_PX, FPS_LOGO_HEIGHT_PX)
    assert Image.open(FPS).mode == "RGB"


def test_processed_header_logo_keeps_trimmed_dimensions() -> None:
    assert STEPINBEL_LOGO_WIDTH_PX == 213
    assert STEPINBEL_LOGO_HEIGHT_PX == 80
    assert header_logo_size() == (213, 80)
    from io import BytesIO

    processed = Image.open(BytesIO(logo_image_bytes()))
    assert processed.size == (213, 80)
    source = (ROOT / "ui" / "presentation" / "shell.py").read_text(encoding="utf-8")
    assert "header_logo_bytes()" in source
    assert "crop=False, pad=0" in source
    css = stylesheet()
    assert "width: 213px" in css
    assert "width: 300px" not in css


def test_energent_asset_unchanged() -> None:
    assert ENERGENT.is_file()
    assert Image.open(ENERGENT).size == (600, 600)
    assert _sha256(ENERGENT) == "DFC0FB1CAB04EE340D7245AC81FE90499295A2827702BFC79EAC54A2CF6EF125"
    assert ENERGENT_FOOTER_DISPLAY_WIDTH_PX == 72
    assert ENERGENT_FOOTER_DISPLAY_HEIGHT_PX == 46
    assert FPS_FOOTER_DISPLAY_WIDTH_PX == 104
    assert FPS_FOOTER_DISPLAY_HEIGHT_PX == 46


def test_header_uses_stepinbel_png_not_jpg() -> None:
    assert logo_path() == STEPINBEL
    source = (ROOT / "ui" / "presentation" / "shell.py").read_text(encoding="utf-8")
    assert "STEPINBEL_LOGO_NAME" in source
    assert "STEPinBEL-logo2.jpg" not in source
    assert "Energent.png" not in source
    runtime_roots = (
        ROOT / "ui" / "presentation",
        ROOT / "ui" / "views",
        ROOT / "ui" / "app.py",
    )
    for path in runtime_roots:
        files = [path] if path.is_file() else list(path.rglob("*.py"))
        for file in files:
            text = file.read_text(encoding="utf-8")
            assert "STEPinBEL-logo2.jpg" not in text, file


def test_footer_uses_fps_and_energent_assets() -> None:
    assert FPS_ASSET == FPS
    assert ENERGENT_ASSET == ENERGENT
    source = (ROOT / "ui" / "presentation" / "footer.py").read_text(encoding="utf-8")
    assert "FPS_LOGO_NAME" in source
    assert "ENERGENT_LOGO_NAME" in source
    assert "Plan D" not in source


def test_plan_d_logo_is_not_required_or_rendered() -> None:
    assert not PLAN_D.exists()
    ui_root = ROOT / "ui"
    offenders: list[str] = []
    for path in ui_root.rglob("*"):
        if "tests" in path.parts or not path.is_file():
            continue
        if path.suffix.lower() not in {".py", ".md", ".tsx"}:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "Plan D-small-transparent.png" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_authorship_metadata_and_authors_file() -> None:
    text = AUTHORS.read_text(encoding="utf-8")
    assert "StepInBel is an Energent project." in text
    assert "Made by Joannes Laveyne of Plan-D.io, for Energent cvba." in text
    assert "The project is supported by the FPS Economy." in text
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert data["project"]["authors"] == [{"name": "Joannes Laveyne"}]
    assert data["project"]["urls"]["Developer"] == "https://www.plan-d.io/"
    assert data["project"]["urls"]["Client"] == "https://energent.be/"


def test_pyproject_declares_ui_extra_without_changing_runtime() -> None:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    assert project["requires-python"] == ">=3.13"
    assert project["dependencies"] == [
        "pyarrow>=14",
        "highspy>=1.8,<2",
        "tzdata>=2024.1",
        "numpy>=2.1",
    ]
    assert project["scripts"] == {"stepinbel": "stepinbel.cli:main"}
    extras = project["optional-dependencies"]
    assert extras["dev"] == ["pytest>=8"]
    assert extras["ui"] == [
        "streamlit>=1.61",
        "altair>=5.5",
        "plotly>=5.24",
        "pillow>=11",
    ]
