"""Tests for CLI request-builder construction paths."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from stepinbel.cli.builders import (
    build_asset,
    build_comparison_request,
    build_machine_commitment,
    build_market_case,
    build_period,
    build_site,
    build_solver_options,
    build_sweep_request,
    reject_request_overrides,
    require_direct_paths,
    selected_compare_markets,
)
from stepinbel.cli.errors import CliError
from stepinbel.cli.parser import parse_args
from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    DayAheadCase,
    MachineCommitmentConfig,
    MFRRCase,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.optimizer import SolverOptions
from stepinbel.workflows import (
    build_case_run_request,
    build_asset_sweep_request,
    build_market_comparison_request,
    build_symmetric_asset_size_candidates,
)
from tests.workflow_helpers import da_config


def _ns(*args):
    return parse_args(list(args))


def _run_ns(*extra):
    return _ns(
        "run",
        "--market", "da",
        "--delivery-start", "2025-01-15",
        "--delivery-end", "2025-01-15",
        "--data-dir", "data",
        "--output-dir", "out",
        *extra,
    )


def _sweep_ns(*extra):
    return _ns(
        "sweep",
        "--market", "da",
        "--delivery-start", "2025-01-15",
        "--delivery-end", "2025-01-15",
        "--powers-mw", "1,2",
        "--storage-hours-grid", "2,4",
        "--data-dir", "data",
        "--output-dir", "out",
        *extra,
    )


def test_delivery_period_roundtrip() -> None:
    ns = _run_ns()
    period = build_period(ns)
    assert isinstance(period, BelgianDeliveryPeriod)
    assert period.start_date.isoformat() == "2025-01-15"


def test_utc_period_roundtrip() -> None:
    ns = _ns(
        "run", "--market", "da",
        "--utc-start", "2025-01-15T00:00:00Z",
        "--utc-end", "2025-01-15T02:00:00Z",
        "--data-dir", "data", "--output-dir", "out",
    )
    period = build_period(ns)
    assert isinstance(period, UtcPeriod)


def test_mixed_period_fails() -> None:
    ns = _ns(
        "run", "--market", "da",
        "--delivery-start", "2025-01-15",
        "--utc-end", "2025-01-15T02:00:00Z",
        "--data-dir", "data", "--output-dir", "out",
    )
    with pytest.raises(CliError):
        build_period(ns)


def test_asset_defaults_match_domain() -> None:
    ns = _run_ns()
    assert build_asset(ns) == AssetConfig()


def test_storage_hours_supplied() -> None:
    ns = _run_ns("--storage-hours", "6")
    asset = build_asset(ns)
    assert asset.storage_hours == 6.0
    assert asset.pond_energy_mwh is None


def test_pond_energy_supplied() -> None:
    ns = _run_ns("--pond-energy-mwh", "20")
    asset = build_asset(ns)
    assert asset.pond_energy_mwh == 20.0
    assert asset.storage_hours is None


def test_storage_conflict_fails() -> None:
    ns = _run_ns("--storage-hours", "4", "--pond-energy-mwh", "20")
    with pytest.raises(CliError, match="only one"):
        build_asset(ns)


def test_pump_efficiency_override() -> None:
    ns = _run_ns("--pump-efficiency", "0.90")
    assert build_asset(ns).eta_pump == 0.90


def test_enforce_terminal_soc_flags() -> None:
    ns_off = _run_ns("--no-enforce-terminal-soc")
    assert build_asset(ns_off).enforce_terminal_soc is False
    ns_on = _run_ns("--enforce-terminal-soc")
    assert build_asset(ns_on).enforce_terminal_soc is True


def test_site_defaults_match_domain() -> None:
    ns = _run_ns()
    assert build_site(ns) == SiteConfig()


def test_site_pv_fixed_requires_price() -> None:
    ns = _run_ns("--pv-revenue-mode", "fixed")
    with pytest.raises(CliError, match="fixed"):
        build_site(ns)


def test_site_pv_price_requires_fixed_mode() -> None:
    ns = _run_ns("--pv-fixed-price-eur-mwh", "40")
    with pytest.raises(CliError, match="fixed"):
        build_site(ns)


def test_site_pv_fixed_ok() -> None:
    ns = _run_ns("--pv-revenue-mode", "fixed", "--pv-fixed-price-eur-mwh", "40")
    site = build_site(ns)
    assert site.pv_revenue_mode == "fixed"
    assert site.pv_fixed_price_eur_mwh == 40.0


def test_solver_options_default() -> None:
    ns = _run_ns()
    assert build_solver_options(ns) == SolverOptions()


def test_solver_options_detailed() -> None:
    ns = _run_ns("--detailed-solver-output")
    assert build_solver_options(ns).detailed_output is True


def test_machine_commitment_and_mip_flags() -> None:
    ns = _run_ns(
        "--fixed-speed-pump",
        "--forbid-simultaneous-operation",
        "--turbine-minimum-output-fraction",
        "0.18",
        "--mip-rel-gap",
        "0.02",
        "--mip-time-limit-s",
        "45",
    )
    assert build_machine_commitment(ns) == MachineCommitmentConfig(
        fixed_speed_pump=True,
        turbine_minimum_output_fraction=0.18,
        forbid_simultaneous_operation=True,
    )
    options = build_solver_options(ns)
    assert options.mip_rel_gap == 0.02
    assert options.time_limit_s == 45.0


def test_da_market_case() -> None:
    ns = _run_ns()
    case = build_market_case(ns, "da")
    assert isinstance(case, DayAheadCase)


def test_mfrr_historical_defaults() -> None:
    ns = _ns("run", "--market", "mfrr",
              "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
              "--data-dir", "data", "--output-dir", "out")
    case = build_market_case(ns, "mfrr")
    assert isinstance(case, MFRRCase)
    assert case.activation_profile == "balanced"
    assert case.capacity_coverage_hours == 4.0


def test_afrr_passive_fixed() -> None:
    ns = _ns("run", "--market", "afrr",
              "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
              "--activation-profile", "passive",
              "--capacity-bid-mode", "fixed",
              "--fixed-up-capacity-price-eur-mw-h", "5",
              "--fixed-down-capacity-price-eur-mw-h", "2",
              "--afrr-up-capacity-fraction", "0.6",
              "--data-dir", "data", "--output-dir", "out")
    case = build_market_case(ns, "afrr")
    assert isinstance(case, AFRRCase)
    assert case.activation_profile == "passive"
    assert case.up_capacity_fraction == 0.6


@pytest.mark.parametrize(
    "flag,value",
    (
        ("--activation-profile", "balanced"),
        ("--capacity-bid-mode", "historical"),
        ("--capacity-quantile", "0.5"),
        ("--fixed-up-capacity-price-eur-mw-h", "8"),
        ("--fixed-down-capacity-price-eur-mw-h", "3"),
        ("--capacity-coverage-hours", "4"),
        ("--afrr-up-capacity-fraction", "0.4"),
    ),
)
def test_da_rejects_all_balancing_flags(flag: str, value: str) -> None:
    ns = _ns(
        "run",
        "--market",
        "da",
        flag,
        value,
        "--delivery-start",
        "2025-01-15",
        "--delivery-end",
        "2025-01-15",
        "--data-dir",
        "data",
        "--output-dir",
        "out",
    )
    with pytest.raises(CliError, match="DA"):
        build_market_case(ns, "da")


def test_compare_prefixes_are_independent() -> None:
    ns = _ns(
        "compare",
        "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
        "--mfrr-activation-profile", "passive",
        "--afrr-activation-profile", "balanced",
        "--afrr-up-capacity-fraction", "0.7",
        "--data-dir", "data", "--output-dir", "out",
    )
    mfrr = build_market_case(ns, "mfrr", prefix="mfrr_")
    afrr = build_market_case(ns, "afrr", prefix="afrr_")
    assert isinstance(mfrr, MFRRCase)
    assert mfrr.activation_profile == "passive"
    assert isinstance(afrr, AFRRCase)
    assert afrr.activation_profile == "balanced"
    assert afrr.up_capacity_fraction == 0.7


def test_sweep_forbidden_flags() -> None:
    """The sweep subparser does not register --pump-mw, so argparse exits 2."""
    with pytest.raises(SystemExit) as exc_info:
        _ns(
            "sweep",
            "--market", "da",
            "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
            "--pump-mw", "2",
            "--powers-mw", "1", "--storage-hours-grid", "2",
            "--data-dir", "data", "--output-dir", "out",
        )
    assert exc_info.value.code == 2


def test_relative_paths_become_absolute(data_root: Path, tmp_path: Path) -> None:
    import os
    # Build request from relative data path to verify builders resolve it
    ns = _run_ns()
    # Override data_dir and output_dir by constructing directly via build functions
    from stepinbel.cli.builders import build_simulation_config
    config = da_config()
    request = build_case_run_request(
        config,
        data_root,
        tmp_path / "out-rel",
    )
    assert request.data_directory.is_absolute()
    assert request.output_directory.is_absolute()


def test_reject_request_overrides_no_extra() -> None:
    ns = _ns("run", "--request", "some.json", "--quiet")
    reject_request_overrides(ns)  # should not raise


def test_reject_request_overrides_with_extra() -> None:
    ns = _ns("run", "--request", "some.json", "--data-dir", "data", "--quiet")
    with pytest.raises(CliError, match="overrides"):
        reject_request_overrides(ns)


def test_sweep_24_candidate_limit_is_enforced(data_root: Path, tmp_path: Path) -> None:
    # 5x5=25 > 24 — build_symmetric_asset_size_candidates raises RunRequestError
    from stepinbel.workflows import RunRequestError
    template = AssetConfig()
    with pytest.raises(RunRequestError, match="24"):
        build_symmetric_asset_size_candidates(
            template,
            powers_mw=[1, 2, 3, 4, 5],
            storage_hours=[2, 4, 6, 8, 10],
        )


def test_comparison_direct_request_construction(data_root: Path, tmp_path: Path) -> None:
    ns = _ns(
        "compare",
        "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
        "--data-dir", str(data_root),
        "--output-dir", str(tmp_path / "compare-out"),
    )
    request = build_comparison_request(ns)
    assert tuple(request.case_requests) == ("da", "mfrr", "afrr")
    assert selected_compare_markets(ns) == ("da", "mfrr", "afrr")
    # period, asset, site are shared
    da_req = request.case_requests["da"]
    mf_req = request.case_requests["mfrr"]
    af_req = request.case_requests["afrr"]
    assert da_req.config.period == mf_req.config.period
    assert da_req.config.asset == mf_req.config.asset == af_req.config.asset
    assert da_req.config.site == mf_req.config.site == af_req.config.site


def test_sweep_direct_request_construction(data_root: Path, tmp_path: Path) -> None:
    ns = _sweep_ns()
    # Override data_dir and output_dir
    ns2 = parse_args([
        "sweep", "--market", "da",
        "--delivery-start", "2025-01-15", "--delivery-end", "2025-01-15",
        "--powers-mw", "1,2", "--storage-hours-grid", "2,4",
        "--data-dir", str(data_root),
        "--output-dir", str(tmp_path / "sweep-out"),
    ])
    request = build_sweep_request(ns2)
    # 2x2=4 candidates
    assert len(request.candidate_order) == 4
    # All share the same market
    assert request.market == "da"
    # Candidate AssetConfigs should differ only in power_pump/turbine and storage_hours
    c0 = request.case_requests[request.candidate_order[0]].config.asset
    assert c0.pond_energy_mwh is None


@pytest.mark.parametrize(
    "raw,canonical",
    (
        (["da", "mfrr"], ("da", "mfrr")),
        (["da", "afrr"], ("da", "afrr")),
        (["mfrr", "afrr"], ("mfrr", "afrr")),
        (["afrr", "da"], ("da", "afrr")),
        (["mfrr", "da", "afrr"], ("da", "mfrr", "afrr")),
    ),
)
def test_compare_markets_selection_and_order(data_root: Path, tmp_path: Path, raw, canonical) -> None:
    ns = parse_args(
        [
            "compare",
            "--markets",
            *raw,
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--data-dir",
            str(data_root),
            "--output-dir",
            str(tmp_path / "sel"),
        ]
    )
    assert selected_compare_markets(ns) == canonical
    request = build_comparison_request(ns)
    assert tuple(request.case_requests) == canonical


def test_compare_omitted_market_flags_rejected() -> None:
    ns = parse_args(
        [
            "compare",
            "--markets",
            "da",
            "mfrr",
            "--afrr-activation-profile",
            "passive",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    with pytest.raises(CliError, match="aFRR"):
        build_comparison_request(ns)
    ns_mfrr = parse_args(
        [
            "compare",
            "--markets",
            "da",
            "afrr",
            "--mfrr-capacity-quantile",
            "0.4",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    with pytest.raises(CliError, match="mFRR"):
        build_comparison_request(ns_mfrr)
