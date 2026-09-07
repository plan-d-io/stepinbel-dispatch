"""Tests for CLI command output, error mapping, and frozen-request paths."""

from __future__ import annotations

import io
import json
import sys
import textwrap
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from stepinbel.cli.app import main
from stepinbel.cli.errors import CliError
from stepinbel.cli.output import emit_failure, map_exception, write_success
from stepinbel.reporting import (
    ArtifactError,
    validate_asset_sweep_artifacts,
    validate_market_comparison_artifacts,
    validate_run_artifacts,
)
from stepinbel.workflows import (
    RunCancelledError,
    RunExecutionError,
    RunRequestError,
)
from stepinbel.config import ConfigError
from stepinbel.data import DataAccessError, DataBundleError


# ── helpers ──────────────────────────────────────────────────────────────────

def _capture_main(args, *, stdin=None):
    """Run main() and capture stdout/stderr as strings."""
    out = io.StringIO()
    err = io.StringIO()
    with patch("sys.stdout", out), patch("sys.stderr", err):
        code = main(args)
    return code, out.getvalue(), err.getvalue()


# ── exit-code and category mapping ───────────────────────────────────────────

@pytest.mark.parametrize("exc,expected_code,expected_category", [
    (CliError("bad arg"), 2, "invalid_argument"),
    (CliError("bad arg", exit_code=1, category="internal_error"), 1, "internal_error"),
    (RunRequestError("no data", category="data_bundle"), 2, "data_bundle"),
    (RunExecutionError("crash", category="optimizer"), 1, "optimizer"),
    (RunCancelledError("stop"), 130, "cancelled"),
    (ConfigError("bad"), 2, "invalid_configuration"),
    (DataBundleError("missing"), 2, "data_bundle"),
    (DataAccessError("coverage"), 2, "data_coverage"),
    (ArtifactError("stale"), 2, "artifact_validation"),
    (RuntimeError("oops"), 1, "internal_error"),
    (KeyboardInterrupt(), 130, "cancelled"),
])
def test_exception_mapping(exc, expected_code, expected_category) -> None:
    code, category, _ = map_exception(exc)
    assert code == expected_code
    assert category == expected_category


def test_emit_failure_writes_json_to_stderr() -> None:
    err = io.StringIO()
    with patch("sys.stderr", err):
        code = emit_failure(RunRequestError("bad path", category="invalid_output"))
    assert code == 2
    obj = json.loads(err.getvalue())
    assert obj["ok"] is False
    assert obj["error_category"] == "invalid_output"
    assert "bad path" in obj["message"]


# ── successful stdout contract ────────────────────────────────────────────────

def test_write_success_is_single_json_line() -> None:
    out = io.StringIO()
    with patch("sys.stdout", out):
        code = write_success({"ok": True, "kind": "run", "x": 1.5})
    assert code == 0
    line = out.getvalue()
    assert line.endswith("\n")
    obj = json.loads(line.strip())
    assert obj["ok"] is True
    assert obj["x"] == 1.5  # number, not string


def test_write_success_keys_are_sorted() -> None:
    out = io.StringIO()
    with patch("sys.stdout", out):
        write_success({"z": 1, "a": 2, "m": 3})
    keys = list(json.loads(out.getvalue()).keys())
    assert keys == sorted(keys)


def test_write_success_rejects_nan() -> None:
    import math
    with pytest.raises((CliError, ValueError)):
        out = io.StringIO()
        with patch("sys.stdout", out):
            write_success({"ok": True, "x": math.nan})


# ── help and version ──────────────────────────────────────────────────────────

def test_help_exits_0() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_version_exits_0() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0


def test_unknown_subcommand_exits_nonzero() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["bogus"])
    assert exc_info.value.code != 0


# ── validate command ──────────────────────────────────────────────────────────

def test_validate_invalid_dir_returns_2(tmp_path: Path) -> None:
    missing = str(tmp_path / "no-such-dir")
    code, out, err = _capture_main(["validate", "--kind", "run", "--directory", missing])
    assert code == 2
    err_obj = json.loads(err.strip())
    assert err_obj["ok"] is False


def test_validate_wrong_kind_fails(tmp_path: Path) -> None:
    """A completed run directory should fail with --kind sweep (wrong kind)."""
    # We don't execute a real run here—the directory is empty so validation
    # will fail before type detection would matter; this verifies dispatch.
    bad = tmp_path / "run-dir"
    bad.mkdir()
    code, out, err = _capture_main(["validate", "--kind", "sweep", "--directory", str(bad)])
    assert code == 2
    err_obj = json.loads(err.strip())
    assert err_obj["ok"] is False


# ── data-info command ─────────────────────────────────────────────────────────

def test_data_info_missing_dir_returns_2(tmp_path: Path) -> None:
    code, out, err = _capture_main(["data-info", "--data-dir", str(tmp_path / "no-data")])
    assert code == 2
    err_obj = json.loads(err.strip())
    assert err_obj["ok"] is False
    assert err_obj["error_category"] == "data_bundle"


def test_data_info_real_bundle(data_root: Path) -> None:
    code, out, err = _capture_main(["data-info", "--data-dir", str(data_root)])
    assert code == 0
    obj = json.loads(out.strip())
    assert obj["ok"] is True
    assert obj["kind"] == "data"
    assert "tables" in obj
    assert "coverage" in obj
    assert "da_prices" in obj["coverage"]
    # Numbers remain numbers
    assert isinstance(obj["coverage"]["da_prices"]["interval_count"], int)
    assert isinstance(obj["coverage"]["da_prices"]["duration_hours"], float)
    # Timestamps have Z suffix
    assert obj["coverage"]["da_prices"]["start_utc"].endswith("Z")


# ── frozen-request paths ──────────────────────────────────────────────────────

def test_run_frozen_request_ok(data_root: Path, tmp_path: Path) -> None:
    from stepinbel.workflows import build_case_run_request, serialize_case_run_request
    from tests.workflow_helpers import da_config, utc
    request = build_case_run_request(
        da_config(),
        data_root,
        tmp_path / "frozen-run",
        run_id="fr-001",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    req_file = tmp_path / "run_request.json"
    import json as _json
    req_file.write_text(_json.dumps(serialize_case_run_request(request), indent=2) + "\n", encoding="utf-8")
    code, out, err = _capture_main(["run", "--request", str(req_file), "--quiet"])
    assert code == 0
    obj = json.loads(out.strip())
    assert obj["ok"] is True
    assert obj["kind"] == "run"


def test_run_frozen_with_override_fails(tmp_path: Path) -> None:
    fake = tmp_path / "fake.json"
    fake.write_text("{}", encoding="utf-8")
    code, out, err = _capture_main(
        ["run", "--request", str(fake), "--data-dir", "data", "--quiet"]
    )
    assert code == 2


def test_run_frozen_existing_output_fails(data_root: Path, tmp_path: Path) -> None:
    from stepinbel.workflows import build_case_run_request, serialize_case_run_request
    from tests.workflow_helpers import da_config, utc
    import json as _json
    out_dir = tmp_path / "existing"
    out_dir.mkdir()
    request = build_case_run_request(
        da_config(),
        data_root,
        out_dir,
        run_id="fr-002",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    req_file = tmp_path / "run_request2.json"
    req_file.write_text(_json.dumps(serialize_case_run_request(request), indent=2) + "\n", encoding="utf-8")
    code, out, err = _capture_main(["run", "--request", str(req_file), "--quiet"])
    assert code == 2


def test_run_invalid_request_json_fails(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json!", encoding="utf-8")
    code, out, err = _capture_main(["run", "--request", str(bad), "--quiet"])
    assert code == 2


# ── quiet suppresses progress ──────────────────────────────────────────────────

def test_quiet_flag_suppresses_progress(data_root: Path, tmp_path: Path) -> None:
    """With --quiet, stderr has no progress lines but final JSON goes to stdout."""
    code, out, err = _capture_main([
        "run",
        "--market", "da",
        "--utc-start", "2025-01-15T00:00:00Z",
        "--utc-end", "2025-01-15T02:00:00Z",
        "--data-dir", str(data_root),
        "--output-dir", str(tmp_path / "quiet-run"),
        "--quiet",
    ])
    assert code == 0
    # stdout: exactly one non-empty JSON line
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 1
    obj = json.loads(lines[0])
    assert obj["ok"] is True
    # stderr: no stage lines
    assert "started" not in err
    assert "completed" not in err


def test_progress_goes_to_stderr(data_root: Path, tmp_path: Path) -> None:
    code, out, err = _capture_main([
        "run",
        "--market", "da",
        "--utc-start", "2025-01-15T00:00:00Z",
        "--utc-end", "2025-01-15T02:00:00Z",
        "--data-dir", str(data_root),
        "--output-dir", str(tmp_path / "prog-run"),
    ])
    assert code == 0
    # stderr has stage lines
    assert any("validate_request" in line or "completed" in line for line in err.splitlines())
    # stdout is exactly one JSON line
    json_lines = [l for l in out.splitlines() if l.strip()]
    assert len(json_lines) == 1


# ── exception-handling boundary ───────────────────────────────────────────────

def test_argparse_systemexit_propagates() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])
    assert exc_info.value.code == 0


def test_repeated_delivery_start_is_argparse_exit_2(tmp_path: Path) -> None:
    output = tmp_path / "must-not-be-created"
    with pytest.raises(SystemExit) as exc_info:
        main(
            [
                "run",
                "--market",
                "da",
                "--delivery-start",
                "2025-01-15",
                "--delivery-start",
                "2025-01-16",
                "--delivery-end",
                "2025-01-15",
                "--data-dir",
                "data",
                "--output-dir",
                str(output),
            ]
        )
    assert exc_info.value.code == 2
    assert not output.exists()


def test_keyboard_interrupt_during_parse_is_cancellation() -> None:
    with patch("stepinbel.cli.app.parse_args", side_effect=KeyboardInterrupt):
        code, _out, err = _capture_main(["run"])
    assert code == 130
    obj = json.loads(err.strip())
    assert obj["ok"] is False
    assert obj["error_category"] == "cancelled"


def test_keyboard_interrupt_during_dispatch_is_cancellation() -> None:
    with patch("stepinbel.cli.app.dispatch", side_effect=KeyboardInterrupt):
        code, _out, err = _capture_main(["data-info", "--data-dir", "data"])
    assert code == 130
    obj = json.loads(err.strip())
    assert obj["ok"] is False
    assert obj["error_category"] == "cancelled"


def test_unexpected_exception_maps_to_internal_error() -> None:
    with patch("stepinbel.cli.app.dispatch", side_effect=RuntimeError("boom")):
        code, _out, err = _capture_main(["data-info", "--data-dir", "data"])
    assert code == 1
    obj = json.loads(err.strip())
    assert obj["ok"] is False
    assert obj["error_category"] == "internal_error"


class _UnrelatedBaseException(BaseException):
    pass


def test_unrelated_baseexception_is_not_swallowed() -> None:
    with patch("stepinbel.cli.app.dispatch", side_effect=_UnrelatedBaseException("nope")):
        with pytest.raises(_UnrelatedBaseException):
            main(["data-info", "--data-dir", "data"])


# ── fixed bid + quantile rejected before output ───────────────────────────────

@pytest.mark.parametrize(
    "args",
    (
        [
            "run", "--market", "mfrr",
            "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
            "--capacity-bid-mode", "fixed",
            "--fixed-up-capacity-price-eur-mw-h", "8",
            "--capacity-quantile", "0.4",
        ],
        [
            "run", "--market", "afrr",
            "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
            "--capacity-bid-mode", "fixed",
            "--fixed-up-capacity-price-eur-mw-h", "8",
            "--fixed-down-capacity-price-eur-mw-h", "3",
            "--capacity-quantile", "0.4",
        ],
        [
            "compare",
            "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
            "--mfrr-capacity-bid-mode", "fixed",
            "--mfrr-fixed-up-capacity-price-eur-mw-h", "8",
            "--mfrr-capacity-quantile", "0.4",
        ],
        [
            "compare",
            "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
            "--afrr-capacity-bid-mode", "fixed",
            "--afrr-fixed-up-capacity-price-eur-mw-h", "8",
            "--afrr-fixed-down-capacity-price-eur-mw-h", "3",
            "--afrr-capacity-quantile", "0.4",
        ],
        [
            "sweep", "--market", "mfrr",
            "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
            "--powers-mw", "1", "--storage-hours-grid", "2",
            "--capacity-bid-mode", "fixed",
            "--fixed-up-capacity-price-eur-mw-h", "8",
            "--capacity-quantile", "0.4",
        ],
        [
            "sweep", "--market", "afrr",
            "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
            "--powers-mw", "1", "--storage-hours-grid", "2",
            "--capacity-bid-mode", "fixed",
            "--fixed-up-capacity-price-eur-mw-h", "8",
            "--fixed-down-capacity-price-eur-mw-h", "3",
            "--capacity-quantile", "0.4",
        ],
    ),
)
def test_fixed_quantile_rejected_before_output_dir(args: list[str], data_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "must-not-be-created"
    code, _out, err = _capture_main(
        [*args, "--data-dir", str(data_root), "--output-dir", str(output)]
    )
    assert code == 2
    assert not output.exists()
    obj = json.loads(err.strip())
    assert obj["ok"] is False
    assert obj["error_category"] == "invalid_argument"


# ── compare / sweep frozen requests ───────────────────────────────────────────

def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def test_compare_frozen_request_ok(data_root: Path, tmp_path: Path) -> None:
    from stepinbel.config import AssetConfig
    from stepinbel.workflows import (
        build_market_comparison_request,
        serialize_market_comparison_request,
    )
    from tests.workflow_helpers import comparison_configs, utc

    output = tmp_path / "frozen-compare"
    asset = AssetConfig(power_pump_mw=3.0, power_turbine_mw=3.0, storage_hours=6.0)
    request = build_market_comparison_request(
        comparison_configs(asset=asset),
        data_root,
        output,
        run_id="cmp-frozen-001",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    frozen_payload = serialize_market_comparison_request(request)
    req_file = _write_json(tmp_path / "comparison_request.json", frozen_payload)
    original = req_file.read_text(encoding="utf-8")

    code, out, _err = _capture_main(["compare", "--request", str(req_file), "--quiet"])
    assert code == 0
    obj = json.loads(out.strip())
    assert obj["ok"] is True
    assert obj["kind"] == "comparison"
    assert obj["run_id"] == "cmp-frozen-001"
    assert obj["case_count"] == 3

    assert req_file.read_text(encoding="utf-8") == original
    written = json.loads((output / "comparison_request.json").read_text(encoding="utf-8"))
    assert written == frozen_payload
    assert written["case_requests"]["da"]["config"]["asset"]["power_pump_mw"] == 3.0
    assert written["case_requests"]["da"]["config"]["asset"]["storage_hours"] == 6.0
    validate_market_comparison_artifacts(output)
    for market in ("da", "mfrr", "afrr"):
        validate_run_artifacts(output / "cases" / market)


def test_compare_frozen_override_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "fake.json"
    fake.write_text("{}", encoding="utf-8")
    code, _out, _err = _capture_main(
        ["compare", "--request", str(fake), "--data-dir", "data", "--quiet"]
    )
    assert code == 2


def test_compare_frozen_existing_output_fails(data_root: Path, tmp_path: Path) -> None:
    from stepinbel.workflows import (
        build_market_comparison_request,
        serialize_market_comparison_request,
    )
    from tests.workflow_helpers import comparison_configs, utc

    output = tmp_path / "existing-compare"
    output.mkdir()
    request = build_market_comparison_request(
        comparison_configs(),
        data_root,
        output,
        run_id="cmp-frozen-002",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    req_file = _write_json(
        tmp_path / "comparison_request2.json",
        serialize_market_comparison_request(request),
    )
    code, _out, _err = _capture_main(["compare", "--request", str(req_file), "--quiet"])
    assert code == 2
    assert list(output.iterdir()) == []


def test_sweep_frozen_request_ok(data_root: Path, tmp_path: Path) -> None:
    from stepinbel.workflows import (
        build_asset_sweep_request,
        serialize_asset_sweep_request,
    )
    from tests.workflow_helpers import da_config, two_asset_candidates, utc

    output = tmp_path / "frozen-sweep"
    request = build_asset_sweep_request(
        da_config(),
        two_asset_candidates(),
        data_root,
        output,
        run_id="swp-frozen-001",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    frozen_payload = serialize_asset_sweep_request(request)
    req_file = _write_json(tmp_path / "asset_sweep_request.json", frozen_payload)
    original = req_file.read_text(encoding="utf-8")

    code, out, _err = _capture_main(["sweep", "--request", str(req_file), "--quiet"])
    assert code == 0
    obj = json.loads(out.strip())
    assert obj["ok"] is True
    assert obj["kind"] == "sweep"
    assert obj["run_id"] == "swp-frozen-001"
    assert obj["candidate_count"] == 2

    assert req_file.read_text(encoding="utf-8") == original
    written = json.loads((output / "asset_sweep_request.json").read_text(encoding="utf-8"))
    assert written == frozen_payload
    assert written["run_id"] == "swp-frozen-001"
    validate_asset_sweep_artifacts(output)
    for candidate_id in request.candidate_order:
        validate_run_artifacts(output / "cases" / candidate_id)


def test_sweep_frozen_override_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "fake.json"
    fake.write_text("{}", encoding="utf-8")
    code, _out, _err = _capture_main(
        ["sweep", "--request", str(fake), "--powers-mw", "1", "--quiet"]
    )
    assert code == 2


def test_sweep_frozen_existing_output_fails(data_root: Path, tmp_path: Path) -> None:
    from stepinbel.workflows import (
        build_asset_sweep_request,
        serialize_asset_sweep_request,
    )
    from tests.workflow_helpers import da_config, two_asset_candidates, utc

    output = tmp_path / "existing-sweep"
    output.mkdir()
    request = build_asset_sweep_request(
        da_config(),
        two_asset_candidates(),
        data_root,
        output,
        run_id="swp-frozen-002",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    req_file = _write_json(
        tmp_path / "asset_sweep_request2.json",
        serialize_asset_sweep_request(request),
    )
    code, _out, _err = _capture_main(["sweep", "--request", str(req_file), "--quiet"])
    assert code == 2
    assert list(output.iterdir()) == []


def test_compare_one_and_duplicate_markets_fail_before_output(data_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "must-not-exist"
    code, _out, err = _capture_main(
        [
            "compare",
            "--markets",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--data-dir",
            str(data_root),
            "--output-dir",
            str(output),
        ]
    )
    assert code == 2
    assert not output.exists()
    assert json.loads(err.strip())["ok"] is False

    code, _out, err = _capture_main(
        [
            "compare",
            "--markets",
            "da",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--data-dir",
            str(data_root),
            "--output-dir",
            str(output),
        ]
    )
    assert code == 2
    assert not output.exists()


def test_compare_frozen_plus_markets_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "fake.json"
    fake.write_text("{}", encoding="utf-8")
    code, _out, _err = _capture_main(
        ["compare", "--request", str(fake), "--markets", "da", "mfrr", "--quiet"]
    )
    assert code == 2
