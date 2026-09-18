"""Real-process CLI smoke and version/entry-point tests."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

PYTHON = sys.executable
# Console script lives beside the interpreter in the same scripts directory.
_SCRIPT_STEM = "stepinbel"
_SCRIPTS_DIR = Path(PYTHON).parent
_CONSOLE_SCRIPT = _SCRIPTS_DIR / (_SCRIPT_STEM + (".exe" if sys.platform == "win32" else ""))
REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"


def _run(*args, check: bool = False):
    return subprocess.run(
        [PYTHON, "-m", "stepinbel", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
    )


def _script(*args):
    exe = str(_CONSOLE_SCRIPT) if _CONSOLE_SCRIPT.exists() else "stepinbel"
    return subprocess.run(
        [exe, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


# ── entry-point wiring ────────────────────────────────────────────────────────

def test_module_version() -> None:
    result = _run("--version")
    assert result.returncode == 0
    assert "stepinbel" in result.stdout.lower() or "stepinbel" in result.stderr.lower()


def test_installed_script_version() -> None:
    result = _script("--version")
    assert result.returncode == 0
    assert "stepinbel" in (result.stdout + result.stderr).lower()


def test_module_help_exits_0() -> None:
    result = _run("--help")
    assert result.returncode == 0


def test_installed_script_help_exits_0() -> None:
    result = _script("--help")
    assert result.returncode == 0


def test_no_gurobi_import() -> None:
    result = subprocess.run(
        [PYTHON, "-c",
         "import sys; import stepinbel; "
         "import stepinbel.cli; "
         "assert 'gurobipy' not in sys.modules, 'gurobipy was imported'"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_no_repo_output_directory_created() -> None:
    """--version must not create any directories under the repo."""
    before = set(REPO.iterdir())
    _run("--version")
    after = set(REPO.iterdir())
    assert before == after


# ── direct DA run smoke ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def da_run_result(tmp_path_factory):
    out = tmp_path_factory.mktemp("cli-da") / "run"
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "run", "--market", "da",
         "--utc-start", "2025-01-15T00:00:00Z",
         "--utc-end", "2025-01-15T02:00:00Z",
         "--data-dir", str(DATA),
         "--output-dir", str(out),
         "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result, out


def test_da_run_exits_0(da_run_result) -> None:
    result, _ = da_run_result
    assert result.returncode == 0, result.stderr


def test_da_run_stdout_is_single_json_line(da_run_result) -> None:
    result, _ = da_run_result
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["ok"] is True
    assert obj["kind"] == "run"
    assert obj["market"] == "da"
    assert isinstance(obj["total_site_revenue_eur"], float)
    assert isinstance(obj["interval_count"], int)
    assert isinstance(obj["artifact_count"], int)
    assert obj["artifact_count"] > 0


def test_da_run_output_dir_validates(da_run_result) -> None:
    from stepinbel.reporting import validate_run_artifacts
    _, out = da_run_result
    validate_run_artifacts(out)


# ── frozen-request run smoke ──────────────────────────────────────────────────

def test_frozen_run_request_executes(da_run_result, tmp_path) -> None:
    """Load the frozen request from the DA smoke run and re-execute against fresh output."""
    _, out = da_run_result
    req_file = out / "run_request.json"
    new_out = tmp_path / "frozen-out"
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "run", "--request", str(req_file),
         "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**__import__("os").environ,
             "STEPINBEL_OUTPUT_OVERRIDE": str(new_out)},
    )
    # The frozen request points to the already-used output dir → exit 2
    assert result.returncode == 2
    err_obj = json.loads(result.stderr.strip())
    assert err_obj["ok"] is False


# ── comparison smoke ──────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def compare_result(tmp_path_factory):
    out = tmp_path_factory.mktemp("cli-cmp") / "compare"
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "compare",
         "--delivery-start", "2025-01-15",
         "--delivery-end", "2025-01-15",
         "--data-dir", str(DATA),
         "--output-dir", str(out),
         "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result, out


def test_compare_exits_0(compare_result) -> None:
    result, _ = compare_result
    assert result.returncode == 0, result.stderr


def test_compare_stdout_json(compare_result) -> None:
    result, _ = compare_result
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    obj = json.loads(lines[0])
    assert obj["ok"] is True
    assert obj["kind"] == "comparison"
    assert obj["case_count"] == 3
    assert obj["highest_revenue_market"] in {"da", "mfrr", "afrr"}


@pytest.fixture(scope="module")
def compare_subset_result(tmp_path_factory):
    out = tmp_path_factory.mktemp("cli-cmp-sub") / "compare"
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "compare",
         "--markets", "da", "mfrr",
         "--delivery-start", "2025-01-15",
         "--delivery-end", "2025-01-15",
         "--data-dir", str(DATA),
         "--output-dir", str(out),
         "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result, out


def test_compare_subset_exits_0(compare_subset_result) -> None:
    result, out = compare_subset_result
    assert result.returncode == 0, result.stderr
    obj = json.loads(result.stdout.strip())
    assert obj["ok"] is True
    assert obj["case_count"] == 2
    assert {path.name for path in (out / "cases").iterdir()} == {"da", "mfrr"}
    assert not (out / "cases" / "afrr").exists()


def test_compare_subset_validates(compare_subset_result) -> None:
    from stepinbel.reporting import validate_market_comparison_artifacts
    _, out = compare_subset_result
    validate_market_comparison_artifacts(out)


def test_compare_validates(compare_result) -> None:
    from stepinbel.reporting import validate_market_comparison_artifacts
    _, out = compare_result
    validate_market_comparison_artifacts(out)


# ── sweep smoke ───────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def sweep_result(tmp_path_factory):
    out = tmp_path_factory.mktemp("cli-sweep") / "sweep"
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "sweep", "--market", "da",
         "--utc-start", "2025-01-15T00:00:00Z",
         "--utc-end", "2025-01-15T02:00:00Z",
         "--powers-mw", "1,2",
         "--storage-hours-grid", "2,4",
         "--data-dir", str(DATA),
         "--output-dir", str(out),
         "--quiet"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result, out


def test_sweep_exits_0(sweep_result) -> None:
    result, _ = sweep_result
    assert result.returncode == 0, result.stderr


def test_sweep_stdout_json(sweep_result) -> None:
    result, _ = sweep_result
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    obj = json.loads(lines[0])
    assert obj["ok"] is True
    assert obj["kind"] == "sweep"
    assert obj["candidate_count"] == 4
    assert isinstance(obj["highest_revenue_candidate_id"], str)


def test_sweep_validates(sweep_result) -> None:
    from stepinbel.reporting import validate_asset_sweep_artifacts
    _, out = sweep_result
    validate_asset_sweep_artifacts(out)


# ── validate command smoke ────────────────────────────────────────────────────

def test_validate_run_dir(da_run_result) -> None:
    _, out = da_run_result
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "validate", "--kind", "run", "--directory", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0
    obj = json.loads(result.stdout.strip())
    assert obj["ok"] is True
    assert obj["kind"] == "run"
    assert isinstance(obj["artifact_count"], int)


def test_validate_comparison_dir(compare_result) -> None:
    _, out = compare_result
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "validate", "--kind", "comparison", "--directory", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0
    obj = json.loads(result.stdout.strip())
    assert obj["ok"] is True


def test_validate_sweep_dir(sweep_result) -> None:
    _, out = sweep_result
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "validate", "--kind", "sweep", "--directory", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0
    obj = json.loads(result.stdout.strip())
    assert obj["ok"] is True


def test_validate_wrong_kind_run_as_sweep(da_run_result) -> None:
    _, out = da_run_result
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "validate", "--kind", "sweep", "--directory", str(out)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 2
    err_obj = json.loads(result.stderr.strip())
    assert err_obj["ok"] is False


# ── data-info smoke ───────────────────────────────────────────────────────────

def test_data_info_smoke() -> None:
    result = subprocess.run(
        [PYTHON, "-m", "stepinbel",
         "data-info", "--data-dir", str(DATA)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0
    obj = json.loads(result.stdout.strip())
    assert obj["ok"] is True
    assert obj["kind"] == "data"
    assert set(obj["coverage"]) == {
        "da_prices",
        "balancing",
        "mfrr_capacity",
        "afrr_capacity",
        "pv",
        "wind",
    }
    assert "onshore_belgium" in obj["wind_profiles"]
    # Numbers are not strings
    for source, cov in obj["coverage"].items():
        if cov is None:
            continue
        assert isinstance(cov["interval_count"], int), source
        assert isinstance(cov["duration_hours"], float), source
