from __future__ import annotations

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from ui.services.demo import demo_form
from ui.services.form import default_live_form
from ui.services.snapshot import build_snapshot

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "ui" / "app.py"
DEMO_DIR = ROOT / "ui" / "demo_artifacts" / "stepinbel_2025_all_markets_pv500"

_FORBIDDEN = (
    "execute_case_run",
    "execute_market_comparison",
    "execute_asset_sweep",
)
_REQUEST_BUILDERS = (
    "build_case_run_request",
    "build_market_comparison_request",
    "serialize_case_run_request",
    "serialize_market_comparison_request",
)


def _artifact_fingerprint() -> dict[str, tuple[int, int]]:
    return {
        str(path.relative_to(DEMO_DIR)): (path.stat().st_size, path.stat().st_mtime_ns)
        for path in DEMO_DIR.rglob("*")
        if path.is_file()
    }


def test_continue_does_not_call_request_or_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def _bang(*_args, **_kwargs):
        called.append("called")
        raise AssertionError("request or execution helper was called")

    import stepinbel.workflows as workflows

    for name in (*_FORBIDDEN, *_REQUEST_BUILDERS):
        if hasattr(workflows, name):
            monkeypatch.setattr(workflows, name, _bang)
    before = _artifact_fingerprint()
    live = build_snapshot(default_live_form(), demo=False)
    demo = build_snapshot(demo_form(), demo=True)
    assert live["derived"]["coverage_ok"] is True
    assert demo["demo"] is True
    assert called == []
    assert _artifact_fingerprint() == before
    assert "output_directory" not in live
    assert "output_directory" not in demo


def test_product_app_continue_has_no_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    def _bang(*_args, **_kwargs):
        called.append("called")
        raise AssertionError("request or execution helper was called")

    import stepinbel.workflows as workflows

    for name in (*_FORBIDDEN, *_REQUEST_BUILDERS):
        if hasattr(workflows, name):
            monkeypatch.setattr(workflows, name, _bang)
    before = _artifact_fingerprint()
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    next(item for item in at.button if item.label == "Continue").click()
    at.run()
    assert not at.exception
    assert "Review & run" in [item.value for item in at.header]
    assert called == []
    assert _artifact_fingerprint() == before


def test_service_modules_do_not_name_execution() -> None:
    services = ROOT / "ui" / "services"
    for path in services.glob("*.py"):
        text = path.read_text(encoding="utf-8")
        for name in _FORBIDDEN:
            assert name not in text, path.name
