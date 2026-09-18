from __future__ import annotations

import copy
import json
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pyarrow.parquet as pq
import pytest

from stepinbel.config import (
    AssetConfig,
    DayAheadCase,
    MachineCommitmentConfig,
    SimulationConfig,
    SiteConfig,
)
from stepinbel.data import DataAccessError, coverage_from_bundle, load_market_data, open_published_bundle
from stepinbel.data.bundle import DataBundleError, OPTIONAL_WIND_TABLE, REQUIRED_TABLES
from stepinbel.data.load import WIND_COLUMNS
from stepinbel.optimizer import ModelError, SolverOptions
from stepinbel.optimizer.highs import solve_sparse_model
from stepinbel.optimizer.types import DISPATCH_COLUMNS, DISPATCH_COLUMNS_V3
from stepinbel.reporting import (
    RUN_ARTIFACT_SCHEMA_VERSION,
    RUN_ARTIFACT_SCHEMA_VERSION_V2,
    RUN_ARTIFACT_SCHEMA_VERSION_V3,
    validate_asset_sweep_artifacts,
    validate_market_comparison_artifacts,
    validate_run_artifacts,
)
from stepinbel.workflows import (
    RunRequestError,
    build_asset_sweep_request,
    build_case_run_request,
    build_market_comparison_request,
    build_symmetric_asset_size_candidates,
    case_run_request_from_payload,
    execute_asset_sweep,
    execute_case_run,
    execute_market_comparison,
    serialize_case_run_request,
)
from tests.da_solve_helpers import relabel_time_limit_feasible, solve_arrays
from tests.test_published_bundle import _sha256, _write_complete_bundle, _write_parquet
from tests.workflow_helpers import comparison_configs, da_config, da_period, mfrr_config, utc

ALL_ON = MachineCommitmentConfig(
    fixed_speed_pump=True,
    turbine_minimum_output_fraction=0.18,
    forbid_simultaneous_operation=True,
)
WIND_SITE = SiteConfig(wind_capacity_kw=1000.0, wind_profile_id="onshore_belgium")
_FAKE_SHA256 = "00" * 32


def _wind_site(**kwargs) -> SiteConfig:
    payload = {
        "wind_capacity_kw": 1000.0,
        "wind_profile_id": "onshore_belgium",
        "grid_export_mw": 2.0,
        "grid_import_mw": 2.0,
    }
    payload.update(kwargs)
    return SiteConfig(**payload)


def _quiet_asset() -> AssetConfig:
    return AssetConfig(
        soc_initial_frac=0.0,
        enforce_terminal_soc=False,
        pump_ramp_power_frac=0.0,
        turbine_ramp_power_frac=0.0,
    )


def _add_wind_table(root: Path, payload: dict, *, schema=None) -> dict:
    path = root / f"{OPTIONAL_WIND_TABLE}.parquet"
    _write_parquet(path, OPTIONAL_WIND_TABLE, schema=schema)
    payload["tables"][OPTIONAL_WIND_TABLE] = {"sha256": _sha256(path), "rows": 1}
    (root / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    return payload


def test_five_table_dummy_bundle_still_opens(tmp_path: Path) -> None:
    _write_complete_bundle(tmp_path)
    bundle = open_published_bundle(tmp_path)
    assert OPTIONAL_WIND_TABLE not in bundle.tables
    assert set(bundle.tables) == set(REQUIRED_TABLES)


def test_wind_table_requires_manifest_and_file(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    (tmp_path / f"{OPTIONAL_WIND_TABLE}.parquet").write_bytes(b"x")
    with pytest.raises(DataBundleError, match="wind_profile_qh"):
        open_published_bundle(tmp_path)
    (tmp_path / f"{OPTIONAL_WIND_TABLE}.parquet").unlink()
    payload["tables"][OPTIONAL_WIND_TABLE] = {"sha256": _FAKE_SHA256, "rows": 1}
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="wind_profile_qh.parquet is missing"):
        open_published_bundle(tmp_path)


def test_wind_table_hash_schema_and_rows(tmp_path: Path) -> None:
    payload = _write_complete_bundle(tmp_path)
    payload = _add_wind_table(tmp_path, payload)
    open_published_bundle(tmp_path)

    payload["tables"][OPTIONAL_WIND_TABLE]["sha256"] = _FAKE_SHA256
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="wind_profile_qh sha256"):
        open_published_bundle(tmp_path)

    payload = _add_wind_table(tmp_path, payload)
    payload["tables"][OPTIONAL_WIND_TABLE]["rows"] = 2
    (tmp_path / "MANIFEST.json").write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DataBundleError, match="wind_profile_qh Parquet row count"):
        open_published_bundle(tmp_path)

    payload = _add_wind_table(
        tmp_path,
        payload,
        schema=(("wrong", "double"),),
    )
    with pytest.raises(DataBundleError, match="wind_profile_qh schema"):
        open_published_bundle(tmp_path)


def test_live_wind_coverage_and_profile_selection(data_root: Path) -> None:
    bundle = open_published_bundle(data_root)
    coverage = coverage_from_bundle(bundle)
    assert "onshore_belgium" in coverage.wind_profiles
    period = da_period()
    config = da_config(period=period, site=WIND_SITE)
    resolved = load_market_data(bundle, config)
    assert resolved.period.required_sources[-1] == "wind_profile_qh"
    assert resolved.period.wind_profile_id == "onshore_belgium"
    assert resolved.wind_profile is not None
    assert tuple(resolved.wind_profile.column_names) == WIND_COLUMNS
    assert resolved.wind_profile.num_rows == 8
    assert set(resolved.wind_profile.column("profile_id").to_pylist()) == {"onshore_belgium"}
    with pytest.raises(DataAccessError, match="wind_profile_id"):
        load_market_data(
            bundle,
            da_config(
                period=period,
                site=SiteConfig(wind_capacity_kw=10.0, wind_profile_id="missing_fleet"),
            ),
        )


def test_wind_only_da_routing_and_accounting() -> None:
    result = solve_arrays(
        sell=np.array([5.0, 5.0, 100.0, 100.0]),
        buy=np.array([50.0, 50.0, 50.0, 50.0]),
        asset=_quiet_asset(),
        site=_wind_site(),
        wind_load_factor=np.array([1.0, 1.0, 0.0, 0.0]),
    )
    avail = result.dispatch.column("wind_available_mw").to_numpy()
    to_pump = result.dispatch.column("wind_to_pump_mw").to_numpy()
    grid = result.dispatch.column("p_pump_grid_mw").to_numpy()
    np.testing.assert_allclose(avail[:2], [1.0, 1.0], atol=1e-8)
    assert to_pump[0] == pytest.approx(1.0, abs=1e-6)
    assert grid[0] == pytest.approx(0.0, abs=1e-6)
    split = (
        result.summary.wind_self_consumed_mwh
        + result.summary.wind_exported_mwh
        + result.summary.wind_curtailed_mwh
    )
    assert split == pytest.approx(result.summary.wind_available_mwh, abs=1e-7)
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur + result.summary.wind_revenue_eur,
        abs=1e-7,
    )
    assert result.solver.continuous_lp is True
    assert result.solver.num_integer == 0


def test_fixed_and_da_wind_settlement() -> None:
    da = solve_arrays(
        sell=np.array([200.0, 200.0]),
        buy=np.array([10.0, 10.0]),
        day_ahead_price=np.array([10.0, 10.0]),
        asset=_quiet_asset(),
        site=_wind_site(wind_revenue_mode="da"),
        wind_load_factor=np.array([1.0, 0.0]),
    )
    assert da.dispatch.column("wind_export_price_eur_mwh").to_pylist()[0] == pytest.approx(10.0)
    fixed = solve_arrays(
        sell=np.array([10.0, 80.0]),
        asset=_quiet_asset(),
        site=_wind_site(wind_revenue_mode="fixed", wind_fixed_price_eur_mwh=40.0),
        wind_load_factor=np.array([0.5, 0.0]),
    )
    assert fixed.dispatch.column("wind_export_price_eur_mwh").to_pylist()[0] == pytest.approx(40.0)


def test_wind_behind_the_meter_uses_da_price_not_activation() -> None:
    result = solve_arrays(
        sell=np.array([200.0, 200.0]),
        buy=np.array([10.0, 10.0]),
        day_ahead_price=np.array([10.0, 10.0]),
        asset=_quiet_asset(),
        site=_wind_site(wind_revenue_mode="da"),
        wind_load_factor=np.array([1.0, 0.0]),
    )
    prices = result.dispatch.column("wind_export_price_eur_mwh").to_pylist()
    assert prices[0] == pytest.approx(10.0)
    assert prices[0] != pytest.approx(200.0)


def test_shared_import_export_and_curtailment() -> None:
    result = solve_arrays(
        sell=np.array([100.0, 100.0]),
        buy=np.array([100.0, 100.0]),
        sell_ub=np.array([0.0, 0.0]),
        buy_ub=np.array([0.0, 0.0]),
        asset=_quiet_asset(),
        site=_wind_site(grid_export_mw=0.4),
        wind_load_factor=np.array([1.0, 1.0]),
    )
    export = result.dispatch.column("wind_export_mw").to_numpy()
    curtail = result.dispatch.column("wind_curtail_mw").to_numpy()
    assert float(np.max(export)) == pytest.approx(0.4, abs=1e-6)
    assert float(np.min(curtail)) == pytest.approx(0.6, abs=1e-6)
    grid = result.dispatch.column("p_pump_grid_mw").to_numpy()
    np.testing.assert_allclose(grid, 0.0, atol=1e-6)


def test_pv_and_wind_operate_together_without_double_counting() -> None:
    result = solve_arrays(
        sell=np.array([5.0, 80.0]),
        buy=np.array([50.0, 50.0]),
        asset=_quiet_asset(),
        site=SiteConfig(
            pv_ac_kw=1000.0,
            pv_region="Belgium",
            wind_capacity_kw=1000.0,
            wind_profile_id="onshore_belgium",
            grid_export_mw=2.0,
            grid_import_mw=2.0,
        ),
        pv_load_factor=np.array([1.0, 0.0]),
        wind_load_factor=np.array([1.0, 0.0]),
    )
    pv_split = (
        result.dispatch.column("pv_export_mw").to_numpy()
        + result.dispatch.column("pv_to_pump_mw").to_numpy()
        + result.dispatch.column("pv_curtail_mw").to_numpy()
    )
    wind_split = (
        result.dispatch.column("wind_export_mw").to_numpy()
        + result.dispatch.column("wind_to_pump_mw").to_numpy()
        + result.dispatch.column("wind_curtail_mw").to_numpy()
    )
    np.testing.assert_allclose(pv_split, result.dispatch.column("pv_available_mw").to_numpy(), atol=1e-6)
    np.testing.assert_allclose(
        wind_split, result.dispatch.column("wind_available_mw").to_numpy(), atol=1e-6
    )
    pump = result.dispatch.column("p_pump_mw").to_numpy()
    grid = result.dispatch.column("p_pump_grid_mw").to_numpy()
    np.testing.assert_allclose(
        pump,
        grid
        + result.dispatch.column("pv_to_pump_mw").to_numpy()
        + result.dispatch.column("wind_to_pump_mw").to_numpy(),
        atol=1e-6,
    )
    assert result.summary.total_site_revenue_eur == pytest.approx(
        result.summary.market_energy_net_eur
        + result.summary.pv_revenue_eur
        + result.summary.wind_revenue_eur,
        abs=1e-7,
    )
    assert result.summary.pv_self_consumed_mwh + result.summary.wind_self_consumed_mwh == pytest.approx(
        float(
            (
                result.dispatch.column("pv_to_pump_mw").to_numpy()
                + result.dispatch.column("wind_to_pump_mw").to_numpy()
            ).sum()
            * 0.25
        ),
        abs=1e-7,
    )


def test_wind_load_factors_between_one_and_limit() -> None:
    result = solve_arrays(
        sell=np.array([80.0]),
        asset=_quiet_asset(),
        site=_wind_site(),
        wind_load_factor=np.array([1.0304]),
    )
    assert result.dispatch.column("wind_available_mw").to_numpy()[0] == pytest.approx(1.0304, abs=1e-8)


@pytest.mark.parametrize("factor", [np.nan, -0.1, np.inf, 1.2, 1.5])
def test_wind_load_factors_are_rejected(factor: float) -> None:
    with pytest.raises(ModelError, match="wind load_factor"):
        solve_arrays(
            sell=np.array([80.0]),
            asset=_quiet_asset(),
            site=_wind_site(),
            wind_load_factor=np.array([factor]),
        )


def test_wind_with_machine_commitment_remains_milp() -> None:
    result = solve_arrays(
        sell=np.array([5.0, 5.0, 100.0, 100.0]),
        buy=np.array([50.0, 50.0, 50.0, 50.0]),
        asset=_quiet_asset(),
        site=_wind_site(),
        wind_load_factor=np.array([1.0, 1.0, 0.0, 0.0]),
        commitment=ALL_ON,
    )
    assert result.solver.continuous_lp is False
    assert result.solver.num_binary > 0
    pump = result.dispatch.column("p_pump_mw").to_numpy()
    grid = result.dispatch.column("p_pump_grid_mw").to_numpy()
    to_pump = result.dispatch.column("wind_to_pump_mw").to_numpy()
    np.testing.assert_allclose(pump, grid + to_pump, atol=1e-7)
    assert result.feasibility.ok


def test_disabled_wind_keeps_v1_dispatch_and_null_behaviour() -> None:
    result = solve_arrays(sell=np.array([10.0, 80.0]))
    assert tuple(result.dispatch.column_names) == DISPATCH_COLUMNS
    assert "wind_available_mw" not in result.dispatch.column_names
    assert result.summary.wind_revenue_eur == 0.0
    assert result.summary.wind_available_mwh == 0.0
    assert result.solver.continuous_lp is True


def test_schema_v3_round_trip_and_fail_closed(data_root: Path, tmp_path: Path) -> None:
    request = build_case_run_request(
        da_config(site=WIND_SITE),
        data_root,
        tmp_path / "v3",
    )
    assert request.request_schema_version == 3
    assert request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION_V3
    payload = serialize_case_run_request(request)
    assert payload["config"]["site"]["wind_capacity_kw"] == 1000.0
    assert "machine_commitment" in payload["config"]
    assert "mip_rel_gap" in payload["solver_options"]
    restored = case_run_request_from_payload(payload)
    assert serialize_case_run_request(restored) == payload
    missing = copy.deepcopy(payload)
    del missing["config"]["site"]["wind_capacity_kw"]
    with pytest.raises(RunRequestError):
        case_run_request_from_payload(missing)
    no_wind = copy.deepcopy(payload)
    no_wind["config"]["site"]["wind_capacity_kw"] = 0.0
    with pytest.raises(RunRequestError, match="schema-v3"):
        case_run_request_from_payload(no_wind)


def test_schema_v1_and_v2_payloads_remain_without_wind(data_root: Path, tmp_path: Path) -> None:
    v1 = serialize_case_run_request(build_case_run_request(da_config(), data_root, tmp_path / "v1"))
    assert v1["request_schema_version"] == 1
    assert "wind_capacity_kw" not in v1["config"]["site"]
    v2 = serialize_case_run_request(
        build_case_run_request(
            SimulationConfig(
                period=da_period(),
                market_case=DayAheadCase(),
                machine_commitment=ALL_ON,
            ),
            data_root,
            tmp_path / "v2",
        )
    )
    assert v2["request_schema_version"] == 2
    assert "wind_capacity_kw" not in v2["config"]["site"]
    v2["config"]["site"]["wind_capacity_kw"] = 10.0
    with pytest.raises(RunRequestError):
        case_run_request_from_payload(v2)


def test_short_wind_lp_artifacts_validate(data_root: Path, tmp_path: Path) -> None:
    request = build_case_run_request(
        da_config(site=WIND_SITE),
        data_root,
        tmp_path / "wind-lp",
        run_id="wind-lp-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    result = execute_case_run(request)
    artifacts = validate_run_artifacts(result.directory)
    metadata = json.loads((result.directory / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V3
    assert metadata["solver"]["formulation"] == "lp"
    assert metadata["solver"]["termination"] == "lp_optimum"
    assert metadata["solver"]["requested_mip_gap"] is None
    assert "wind_profile_qh" in metadata["published_data"]["tables"]
    summary = json.loads((result.directory / "summary.json").read_text(encoding="utf-8"))
    assert "wind_revenue_eur" in summary
    dispatch = pq.read_table(result.directory / "dispatch.parquet")
    assert tuple(dispatch.column_names) == DISPATCH_COLUMNS_V3
    report = (result.directory / "report.txt").read_text(encoding="utf-8")
    assert "Co-located wind:" in report
    assert "Day-ahead wind settlement uses day-ahead prices" in report
    assert artifacts["summary.json"].is_file()
    reopened = case_run_request_from_payload(
        json.loads((result.directory / "run_request.json").read_text(encoding="utf-8"))
    )
    assert reopened.config.wind_enabled() is True


def test_short_pv_and_wind_and_balancing_smokes(data_root: Path, tmp_path: Path) -> None:
    both = execute_case_run(
        build_case_run_request(
            da_config(
                site=SiteConfig(
                    pv_ac_kw=100.0,
                    pv_region="Belgium",
                    wind_capacity_kw=100.0,
                    wind_profile_id="onshore_belgium",
                )
            ),
            data_root,
            tmp_path / "pv-wind",
            run_id="pv-wind-01",
            created_at_utc=utc(2026, 1, 1, 12, 0),
        )
    )
    validate_run_artifacts(both.directory)
    assert both.result.summary.pv_available_mwh >= 0.0
    assert both.result.summary.wind_available_mwh >= 0.0
    balancing = execute_case_run(
        build_case_run_request(
            mfrr_config(site=WIND_SITE),
            data_root,
            tmp_path / "wind-mfrr",
            run_id="wind-mfrr-01",
            created_at_utc=utc(2026, 1, 1, 12, 0),
        )
    )
    validate_run_artifacts(balancing.directory)
    assert balancing.result.summary.wind_available_mwh > 0.0


def test_time_limited_wind_milp_artifacts(data_root: Path, tmp_path: Path) -> None:
    original = solve_sparse_model

    def wrapper(model, options, *, build_s, require_usable=True):
        return relabel_time_limit_feasible(
            original(model, options, build_s=build_s, require_usable=False),
            achieved_gap=0.02,
        )

    request = build_case_run_request(
        SimulationConfig(
            period=da_period(),
            market_case=DayAheadCase(),
            site=WIND_SITE,
            machine_commitment=ALL_ON,
        ),
        data_root,
        tmp_path / "wind-milp-tl",
        solver_options=SolverOptions(mip_rel_gap=0.01, time_limit_s=30.0),
        run_id="wind-milp-tl-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    with patch("stepinbel.optimizer.solve.solve_sparse_model", wrapper):
        result = execute_case_run(request)
    validate_run_artifacts(result.directory)
    metadata = json.loads((result.directory / "run_metadata.json").read_text(encoding="utf-8"))
    assert metadata["artifact_schema_version"] == RUN_ARTIFACT_SCHEMA_VERSION_V3
    assert metadata["solver"]["formulation"] == "milp"
    assert metadata["solver"]["status"] == "time_limit"
    assert result.result.solver.continuous_lp is False


def test_comparison_and_sweep_include_wind_totals(data_root: Path, tmp_path: Path) -> None:
    configs = comparison_configs(site=WIND_SITE, markets=("da", "mfrr"))
    comparison = execute_market_comparison(
        build_market_comparison_request(
            configs,
            data_root,
            tmp_path / "wind-compare",
            run_id="wind-cmp-01",
            created_at_utc=utc(2026, 1, 1, 12, 0),
        )
    )
    validate_market_comparison_artifacts(comparison.directory)
    summary = json.loads(
        (comparison.directory / "comparison_summary.json").read_text(encoding="utf-8")
    )
    assert summary["comparison_artifact_schema_version"] == 3
    for row in summary["rows"]:
        assert "wind_revenue_eur" in row
        assert "wind_available_mwh" in row
    candidates = build_symmetric_asset_size_candidates(
        AssetConfig(),
        powers_mw=(1.0, 2.0),
        storage_hours=(2.0,),
    )
    sweep = execute_asset_sweep(
        build_asset_sweep_request(
            da_config(site=WIND_SITE),
            candidates,
            data_root,
            tmp_path / "wind-sweep",
            run_id="wind-swp-01",
            created_at_utc=utc(2026, 1, 1, 12, 0),
        )
    )
    validate_asset_sweep_artifacts(sweep.directory)
    sweep_summary = json.loads(
        (sweep.directory / "asset_sweep_summary.json").read_text(encoding="utf-8")
    )
    assert sweep_summary["asset_sweep_artifact_schema_version"] == 3
    assert "wind_revenue_eur" in sweep_summary["rows"][0]


def test_no_wind_schema_versions_stay_v1(data_root: Path, tmp_path: Path) -> None:
    request = build_case_run_request(da_config(), data_root, tmp_path / "no-wind")
    assert request.request_schema_version == 1
    assert request.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION
    milp = build_case_run_request(
        SimulationConfig(period=da_period(), market_case=DayAheadCase(), machine_commitment=ALL_ON),
        data_root,
        tmp_path / "no-wind-milp",
    )
    assert milp.request_schema_version == 2
    assert milp.artifact_schema_version == RUN_ARTIFACT_SCHEMA_VERSION_V2
