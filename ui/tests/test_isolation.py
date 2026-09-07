from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
UI_ROOT = ROOT / "ui"

_FORBIDDEN_EVERYWHERE = (
    "stepinbel.markets",
    "stepinbel.cli",
    "btm_sim",
    "phs",
)

_FORBIDDEN_OUTSIDE_SERVICES = (
    "stepinbel.config",
    "stepinbel.data",
    "stepinbel.workflows",
    "stepinbel.reporting",
    "stepinbel.optimizer",
    *_FORBIDDEN_EVERYWHERE,
)

_SERVICE_ALLOWED = {
    "stepinbel.config",
    "stepinbel.data",
    "stepinbel.workflows",
    "stepinbel.reporting",
}

_FORBIDDEN_SUBSTRINGS = (
    "Battery dispatch",
    "Battery Dispatch",
    "Cursor\\PHS",
    "Cursor/PHS",
)

_FORBIDDEN_CALL_NAMES = (
    "execute_case_run",
    "execute_market_comparison",
    "execute_asset_sweep",
    "solve_case",
)


def _product_python_files() -> list[Path]:
    files: list[Path] = []
    for path in UI_ROOT.rglob("*.py"):
        if "tests" in path.parts:
            continue
        files.append(path)
    return files


def _is_module(name: str | None, prefixes: tuple[str, ...]) -> bool:
    if not name:
        return False
    return any(name == item or name.startswith(f"{item}.") for item in prefixes)


def _is_service(path: Path) -> bool:
    return "services" in path.parts


def _is_presentation(path: Path) -> bool:
    return "presentation" in path.parts


def _is_view(path: Path) -> bool:
    return "views" in path.parts


def test_presentation_stays_core_independent() -> None:
    offenders: list[str] = []
    for path in _product_python_files():
        if not _is_presentation(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "stepinbel" and path.name == "shell.py":
                        continue
                    if _is_module(alias.name, _FORBIDDEN_OUTSIDE_SERVICES) or alias.name.startswith("stepinbel."):
                        offenders.append(f"{path.relative_to(ROOT)}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if path.name == "shell.py" and node.module == "stepinbel" and {item.name for item in node.names} == {"__version__"}:
                    continue
                if _is_module(node.module, _FORBIDDEN_OUTSIDE_SERVICES) or (
                    node.module is not None and node.module.startswith("stepinbel")
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.module}")
    assert offenders == []


def test_views_call_ui_services_not_core() -> None:
    offenders: list[str] = []
    service_users = 0
    for path in _product_python_files():
        if not _is_view(path):
            continue
        text = path.read_text(encoding="utf-8")
        if "ui.services" in text and path.name in {
            "configure.py",
            "review.py",
            "working.py",
            "recovery.py",
            "results.py",
            "router.py",
        }:
            service_users += 1
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if _is_module(alias.name, _FORBIDDEN_OUTSIDE_SERVICES) or alias.name.startswith("stepinbel"):
                        offenders.append(f"{path.relative_to(ROOT)}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if _is_module(node.module, _FORBIDDEN_OUTSIDE_SERVICES) or (
                    node.module is not None and node.module.startswith("stepinbel")
                ):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.module}")
    assert offenders == []
    assert service_users == 6


def test_flow_and_entry_points_do_not_import_core() -> None:
    offenders: list[str] = []
    for path in _product_python_files():
        if _is_service(path) or _is_presentation(path) or _is_view(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("stepinbel"):
                        offenders.append(f"{path.relative_to(ROOT)}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                if node.module is not None and node.module.startswith("stepinbel"):
                    offenders.append(f"{path.relative_to(ROOT)}:{node.module}")
    assert offenders == []


def test_permitted_services_import_only_approved_public_modules() -> None:
    offenders: list[str] = []
    for path in _product_python_files():
        if not _is_service(path):
            continue
        text = path.read_text(encoding="utf-8")
        for name in _FORBIDDEN_CALL_NAMES:
            if name in text:
                offenders.append(f"{path.relative_to(ROOT)}:{name}")
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("stepinbel") and alias.name not in _SERVICE_ALLOWED:
                        offenders.append(f"{path.relative_to(ROOT)}:{alias.name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if not module.startswith("stepinbel"):
                    continue
                if _is_module(module, _FORBIDDEN_EVERYWHERE):
                    offenders.append(f"{path.relative_to(ROOT)}:{module}")
                    continue
                if module == "stepinbel.optimizer" or module.startswith("stepinbel.optimizer."):
                    names = {item.name for item in node.names}
                    if path.name != "request.py" or names != {"SolverOptions"}:
                        offenders.append(f"{path.relative_to(ROOT)}:{module}:{sorted(names)}")
                    continue
                if module in _SERVICE_ALLOWED:
                    continue
                offenders.append(f"{path.relative_to(ROOT)}:{module}")
    assert offenders == []
    rejected_submodules = (
        "stepinbel.reporting.constants",
        "stepinbel.workflows.some_internal_module",
        "stepinbel.config.some_internal_module",
    )
    for name in rejected_submodules:
        assert name not in _SERVICE_ALLOWED
        assert name.startswith("stepinbel.")
        assert not any(name == item for item in _SERVICE_ALLOWED)


def test_no_imports_from_phs_or_battery_dispatch() -> None:
    app = (UI_ROOT / "app.py").read_text(encoding="utf-8")
    assert "Battery dispatch" not in app
    assert "Battery Dispatch" not in app
    offenders: list[str] = []
    for path in _product_python_files():
        text = path.read_text(encoding="utf-8")
        for needle in _FORBIDDEN_SUBSTRINGS:
            if needle in text:
                offenders.append(f"{path.relative_to(ROOT)}:{needle}")
        if "sys.path" in text and path.name == "app.py":
            assert "_PROJECT_ROOT" in text
            assert "_SRC" in text
    assert offenders == []


def test_app_path_setup_is_local_only() -> None:
    source = (UI_ROOT / "app.py").read_text(encoding="utf-8")
    assert "parent.parent" in source
    assert "Battery" not in source
    assert "PHS" not in source
