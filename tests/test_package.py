from __future__ import annotations

import ast
import subprocess
import sys
import tomllib
from pathlib import Path

import importlib.metadata

import pytest

import stepinbel

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "stepinbel"
VERSION_FILE = SRC_ROOT / "VERSION"
PYPROJECT = REPO_ROOT / "pyproject.toml"
FORBIDDEN_IMPORT_ROOTS = {
    "data_pipeline",
    "phs",
    "btm_sim",
    "gurobipy",
    "pandas",
    "streamlit",
}


def test_public_version_matches_version_file_and_metadata() -> None:
    file_version = VERSION_FILE.read_text(encoding="utf-8").strip()
    assert stepinbel.__version__ == "0.2.0"
    assert file_version == "0.2.0"
    assert importlib.metadata.version("stepinbel") == "0.2.0"


def test_requires_python_313() -> None:
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert payload["project"]["requires-python"] == ">=3.13"
    assert importlib.metadata.metadata("stepinbel")["Requires-Python"] == ">=3.13"


def test_console_script_is_cli_main() -> None:
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    assert payload["project"]["scripts"] == {"stepinbel": "stepinbel.cli:main"}
    assert stepinbel.__all__ == ["__version__"]
    assert not hasattr(stepinbel, "main")
    # cli must not be imported in stepinbel.__init__ (subpackage import in other
    # tests legitimately registers the attribute on the parent; check the source instead).
    import ast, pathlib
    init_src = pathlib.Path(stepinbel.__file__).read_text(encoding="utf-8")
    tree = ast.parse(init_src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in getattr(node, "names", [])]
            module = getattr(node, "module", "") or ""
            assert "cli" not in module and not any("cli" in n for n in names), \
                "stepinbel.__init__ must not import cli"


def test_production_source_has_no_forbidden_imports() -> None:
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                root = name.split(".", 1)[0]
                assert root not in FORBIDDEN_IMPORT_ROOTS, f"{path} imports {name}"


def test_project_requires_highspy_and_not_gurobipy() -> None:
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = payload["project"]["dependencies"]
    assert any(item.startswith("highspy") for item in dependencies)
    assert all("gurobipy" not in item for item in dependencies)

    requires = importlib.metadata.requires("stepinbel") or []
    runtime = [item for item in requires if "extra ==" not in item]
    assert any(item.startswith("highspy") for item in runtime)
    assert all("gurobipy" not in item for item in runtime)


EXPECTED_RUNTIME_DEPENDENCIES = [
    "pyarrow>=14",
    "highspy>=1.8,<2",
    "tzdata>=2024.1",
    "numpy>=2.1",
]


def _requirement_name(item: str) -> str:
    name = item.split(";", 1)[0]
    for separator in ("<", ">", "=", "!", "~"):
        name = name.split(separator, 1)[0]
    return name.strip()


def test_project_requires_authorized_tzdata() -> None:
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = payload["project"]["dependencies"]
    assert "tzdata>=2024.1" in dependencies
    assert dependencies == EXPECTED_RUNTIME_DEPENDENCIES

    requires = importlib.metadata.requires("stepinbel") or []
    runtime = [item for item in requires if "extra ==" not in item]
    assert any(item.startswith("tzdata>=2024.1") for item in runtime)
    assert {_requirement_name(item) for item in runtime} == {
        "pyarrow",
        "highspy",
        "tzdata",
        "numpy",
    }


def test_project_requires_numpy_21() -> None:
    payload = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    dependencies = payload["project"]["dependencies"]
    assert "numpy>=2.1" in dependencies
    requires = importlib.metadata.requires("stepinbel") or []
    runtime = [item for item in requires if "extra ==" not in item]
    assert any(item.startswith("numpy>=2.1") for item in runtime)


OPTIMIZER_PUBLIC_EXPORTS = [
    "solve_case",
    "SolverOptions",
    "DispatchResult",
    "DispatchSummary",
    "SolverMetadata",
    "FeasibilityReport",
    "ModelError",
    "SolverError",
    "TERMINATION_ACCEPTED_WITHIN_GAP",
    "TERMINATION_TIME_LIMIT_FEASIBLE",
    "MIP_TIME_LIMIT_FEASIBLE_WARNING",
    "COMPARISON_MIP_TIME_LIMIT_WARNING",
]


def test_optimizer_public_exports_are_exact() -> None:
    import stepinbel.optimizer as optimizer

    assert optimizer.__all__ == OPTIMIZER_PUBLIC_EXPORTS
    for name in OPTIMIZER_PUBLIC_EXPORTS:
        assert hasattr(optimizer, name)
    with pytest.raises(ImportError):
        from stepinbel.optimizer import solve_from_inputs  # noqa: F401
    assert stepinbel.__all__ == ["__version__"]
    for name in OPTIMIZER_PUBLIC_EXPORTS:
        assert not hasattr(stepinbel, name)


def test_markets_public_exports_are_exact() -> None:
    import stepinbel.markets as markets

    assert markets.__all__ == [
        "MarketDispatchInputs",
        "MarketInputError",
        "build_day_ahead_inputs",
        "build_mfrr_inputs",
        "build_afrr_inputs",
    ]
    assert not hasattr(stepinbel, "build_mfrr_inputs")
    assert not hasattr(stepinbel, "build_afrr_inputs")
    with pytest.raises(ImportError):
        from stepinbel.markets import yearly_energy_bids  # noqa: F401


WORKFLOW_PUBLIC_EXPORTS = [
    "CaseRunRequest",
    "CaseRun",
    "RunEvent",
    "RunError",
    "RunRequestError",
    "RunExecutionError",
    "RunCancelledError",
    "build_case_run_request",
    "serialize_case_run_request",
    "case_run_request_from_payload",
    "load_case_run_request",
    "execute_case_run",
    "MarketComparisonRequest",
    "MarketComparisonRow",
    "MarketComparisonRun",
    "build_market_comparison_request",
    "serialize_market_comparison_request",
    "market_comparison_request_from_payload",
    "load_market_comparison_request",
    "execute_market_comparison",
    "MAX_ASSET_SWEEP_CANDIDATES",
    "AssetSweepCandidate",
    "AssetSweepRequest",
    "AssetSweepRow",
    "AssetSweepRun",
    "build_symmetric_asset_size_candidates",
    "build_asset_sweep_request",
    "serialize_asset_sweep_request",
    "asset_sweep_request_from_payload",
    "load_asset_sweep_request",
    "execute_asset_sweep",
]

REPORTING_PUBLIC_EXPORTS = [
    "RUN_ARTIFACT_SCHEMA_VERSION",
    "RUN_ARTIFACT_SCHEMA_VERSION_V2",
    "ArtifactError",
    "build_period_summaries",
    "render_run_report",
    "validate_run_artifacts",
    "MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION",
    "MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION_V2",
    "render_market_comparison_report",
    "validate_market_comparison_artifacts",
    "ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION",
    "ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION_V2",
    "render_asset_sweep_report",
    "validate_asset_sweep_artifacts",
]


def test_workflow_public_exports_are_exact() -> None:
    import stepinbel.workflows as workflows

    assert workflows.__all__ == WORKFLOW_PUBLIC_EXPORTS
    for name in WORKFLOW_PUBLIC_EXPORTS:
        assert hasattr(workflows, name)
        assert not hasattr(stepinbel, name)


def test_reporting_public_exports_are_exact() -> None:
    import stepinbel.reporting as reporting

    assert reporting.__all__ == REPORTING_PUBLIC_EXPORTS
    for name in REPORTING_PUBLIC_EXPORTS:
        assert hasattr(reporting, name)
        assert not hasattr(stepinbel, name)


def test_import_stepinbel_does_not_import_highspy() -> None:
    script = (
        "import sys\n"
        "import stepinbel\n"
        "assert 'highspy' not in sys.modules\n"
        "assert 'numpy' not in sys.modules\n"
        "assert 'pyarrow' not in sys.modules\n"
        "assert stepinbel.__version__ == '0.2.0'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
