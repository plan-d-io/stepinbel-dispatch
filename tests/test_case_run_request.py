from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.optimizer import SolverOptions
from stepinbel.workflows import (
    CaseRunRequest,
    RunRequestError,
    build_case_run_request,
    case_run_request_from_payload,
    execute_case_run,
    load_case_run_request,
    serialize_case_run_request,
)
from tests.workflow_helpers import afrr_config, build_request, da_config, da_period, mfrr_config, utc


def _round_trip(request):
    payload = serialize_case_run_request(request)
    restored = case_run_request_from_payload(payload)
    assert serialize_case_run_request(restored) == payload
    return restored


def test_deterministic_construction(data_root: Path, tmp_path: Path) -> None:
    created = utc(2026, 2, 3, 4, 5)
    request = build_request(
        data_root,
        tmp_path / "out",
        run_id="fixed-id-01",
        created_at=created,
        solver_options=SolverOptions(detailed_output=False),
    )
    assert request.run_id == "fixed-id-01"
    assert request.created_at_utc == created
    assert request.data_directory == data_root.resolve()
    assert request.output_directory == (tmp_path / "out").resolve()
    assert request.data_manifest_sha256
    assert not request.output_directory.exists()


def test_generated_id_shape_and_uniqueness(data_root: Path, tmp_path: Path) -> None:
    first = build_case_run_request(da_config(), data_root, tmp_path / "a")
    second = build_case_run_request(da_config(), data_root, tmp_path / "b")
    assert first.run_id != second.run_id
    assert "_" in first.run_id
    prefix, suffix = first.run_id.rsplit("_", 1)
    assert prefix.endswith("Z")
    assert len(suffix) == 8


def test_relative_paths_resolve_and_manifest_is_frozen(
    data_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(data_root.parent)
    request = build_case_run_request(da_config(), Path("data"), tmp_path / "rel-out")
    assert request.data_directory.is_absolute()
    assert request.data_directory == data_root.resolve()
    assert request.data_manifest_sha256 == build_request(data_root, tmp_path / "x").data_manifest_sha256


def test_round_trip_da_belgian(data_root: Path, tmp_path: Path) -> None:
    config = da_config(period=BelgianDeliveryPeriod(date(2025, 1, 15), date(2025, 1, 16)))
    restored = _round_trip(build_request(data_root, tmp_path / "out", config))
    assert isinstance(restored.config.period, BelgianDeliveryPeriod)
    assert isinstance(restored.config.market_case, DayAheadCase)


def test_round_trip_da_utc(data_root: Path, tmp_path: Path) -> None:
    restored = _round_trip(build_request(data_root, tmp_path / "out", da_config(period=da_period())))
    assert isinstance(restored.config.period, UtcPeriod)


@pytest.mark.parametrize("passive", [False, True])
def test_round_trip_mfrr(data_root: Path, tmp_path: Path, passive: bool) -> None:
    restored = _round_trip(build_request(data_root, tmp_path / "out", mfrr_config(passive=passive)))
    assert isinstance(restored.config.market_case, MFRRCase)
    assert restored.config.market_case.activation_profile == ("passive" if passive else "balanced")


@pytest.mark.parametrize("fixed,fraction", [(False, 0.25), (True, 0.6)])
def test_round_trip_afrr(data_root: Path, tmp_path: Path, fixed: bool, fraction: float) -> None:
    restored = _round_trip(
        build_request(data_root, tmp_path / "out", afrr_config(fixed=fixed, up_fraction=fraction))
    )
    case = restored.config.market_case
    assert isinstance(case, AFRRCase)
    assert case.up_capacity_fraction == fraction
    if fixed:
        assert isinstance(case.capacity_bid, FixedMinimumCapacityBid)
        assert case.capacity_bid.downward_price_eur_mw_h == 3.0
    else:
        assert isinstance(case.capacity_bid, HistoricalQuantileCapacityBid)


@pytest.mark.parametrize("mode", ["da", "fixed"])
def test_round_trip_pv_modes(data_root: Path, tmp_path: Path, mode: str) -> None:
    site = SiteConfig(
        pv_ac_kw=120.0,
        pv_region="Belgium",
        pv_revenue_mode=mode,  # type: ignore[arg-type]
        pv_fixed_price_eur_mwh=45.0 if mode == "fixed" else None,
    )
    restored = _round_trip(build_request(data_root, tmp_path / "out", da_config(site=site)))
    assert restored.config.site.pv_revenue_mode == mode
    assert restored.config.site.pv_ac_kw == 120.0


def test_round_trip_asymmetric_pond(data_root: Path, tmp_path: Path) -> None:
    asset = AssetConfig(
        power_pump_mw=2.5,
        power_turbine_mw=1.5,
        storage_hours=None,
        pond_energy_mwh=8.0,
    )
    restored = _round_trip(build_request(data_root, tmp_path / "out", da_config(asset=asset)))
    assert restored.config.asset.pond_energy_mwh == 8.0
    assert restored.config.asset.storage_hours is None
    assert restored.config.asset.machines_asymmetric()


def test_load_does_not_replace_frozen_values(data_root: Path, tmp_path: Path) -> None:
    asset = AssetConfig(storage_hours=6.0, soc_initial_frac=0.25)
    request = build_request(data_root, tmp_path / "out", da_config(asset=asset), run_id="keep-me")
    path = tmp_path / "request.json"
    path.write_text(json.dumps(serialize_case_run_request(request)), encoding="utf-8")
    loaded = load_case_run_request(path)
    assert loaded.run_id == "keep-me"
    assert loaded.config.asset.storage_hours == 6.0
    assert loaded.config.asset.soc_initial_frac == 0.25


def test_wrong_schema_version(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_case_run_request(build_request(data_root, tmp_path / "out"))
    payload["request_schema_version"] = 99
    with pytest.raises(RunRequestError, match="request_schema_version"):
        case_run_request_from_payload(payload)


def test_corrupt_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(RunRequestError, match="corrupt") as caught:
        load_case_run_request(path)
    assert caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "patch",
    [
        {"market_case": {"kind": "FCRCase", "market": "fcr"}},
        {"market_case": {"kind": "DayAheadCase", "market": "god"}},
        {"period": {"kind": "year_2025", "start_date": "2025-01-01", "end_date_inclusive": "2025-12-31"}},
        {"period": {"kind": "preset", "name": "full_year"}},
        {"market_case": {"kind": "MFRRCase", "market": "mfrr", "activation_profile": "god", "capacity_coverage_hours": 4.0, "capacity_bid": {"kind": "historical_quantile", "quantile": 0.5}}},
        {"market_case": {"kind": "MFRRCase", "market": "mfrr", "activation_profile": "active", "capacity_coverage_hours": 4.0, "capacity_bid": {"kind": "historical_quantile", "quantile": 0.5}}},
        {"market_case": {"kind": "MFRRCase", "market": "mfrr", "activation_profile": "balanced", "capacity_coverage_hours": 4.0, "capacity_bid": {"kind": "mystery", "quantile": 0.5}}},
    ],
)
def test_invalid_discriminators_rejected(data_root: Path, tmp_path: Path, patch: dict) -> None:
    payload = serialize_case_run_request(build_request(data_root, tmp_path / "out"))
    payload["config"].update(patch)
    with pytest.raises(RunRequestError):
        case_run_request_from_payload(payload)


def test_nan_and_infinity_rejected(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_case_run_request(build_request(data_root, tmp_path / "out"))
    payload["config"]["asset"]["power_pump_mw"] = math.nan
    with pytest.raises(ValueError):
        json.dumps(payload, allow_nan=False)
    text = json.dumps(payload, allow_nan=True)
    with pytest.raises(RunRequestError):
        case_run_request_from_payload(json.loads(text))
    payload["config"]["asset"]["power_pump_mw"] = math.inf
    with pytest.raises(RunRequestError):
        case_run_request_from_payload(payload)


def test_changed_frozen_manifest_rejected_at_execution(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_case_run_request(build_request(data_root, tmp_path / "changed"))
    payload["data_manifest_sha256"] = "ab" * 32
    request = case_run_request_from_payload(payload)
    with pytest.raises(RunRequestError, match="manifest hash") as caught:
        execute_case_run(request)
    assert caught.value.category == "data_bundle"


def test_wrong_public_types_rejected(data_root: Path, tmp_path: Path) -> None:
    with pytest.raises(RunRequestError, match="SimulationConfig"):
        build_case_run_request({"period": "nope"}, data_root, tmp_path / "out")  # type: ignore[arg-type]
    with pytest.raises(RunRequestError, match="SolverOptions"):
        build_case_run_request(da_config(), data_root, tmp_path / "out", solver_options={"detailed_output": True})  # type: ignore[arg-type]


def test_invalid_injected_timestamp(data_root: Path, tmp_path: Path) -> None:
    with pytest.raises(RunRequestError, match="timezone-aware"):
        build_case_run_request(
            da_config(),
            data_root,
            tmp_path / "out",
            created_at_utc=datetime(2026, 1, 1, 12, 0),
        )


def test_relative_payload_paths_are_rejected(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_case_run_request(build_request(data_root, tmp_path / "out"))
    payload["data_directory"] = "data"
    with pytest.raises(RunRequestError, match="absolute"):
        case_run_request_from_payload(payload)
    payload = serialize_case_run_request(build_request(data_root, tmp_path / "out2"))
    payload["output_directory"] = "relative-out"
    with pytest.raises(RunRequestError, match="absolute"):
        case_run_request_from_payload(payload)


def test_invalid_direct_request_fields_raise(data_root: Path, tmp_path: Path) -> None:
    valid = build_request(data_root, tmp_path / "out")
    with pytest.raises(RunRequestError):
        replace(valid, created_at_utc=datetime(2026, 1, 1, 12, 0))
    with pytest.raises(RunRequestError):
        replace(
            valid,
            created_at_utc=datetime(2026, 1, 1, 12, 0, tzinfo=timezone(timedelta(hours=1))),
        )
    with pytest.raises(RunRequestError):
        replace(valid, created_at_utc=utc(2026, 1, 1, 12, 0).replace(microsecond=1))
    with pytest.raises(RunRequestError):
        replace(valid, data_directory=Path("relative-data"))
    with pytest.raises(RunRequestError):
        replace(valid, output_directory=Path("relative-out"))
    with pytest.raises(RunRequestError):
        replace(valid, data_manifest_sha256="not-a-sha256")
    with pytest.raises(RunRequestError):
        build_case_run_request(da_config(), object(), tmp_path / "x")  # type: ignore[arg-type]
    with pytest.raises(RunRequestError) as caught:
        build_case_run_request(
            da_config(),
            data_root,
            tmp_path / "obj-time",
            created_at_utc=object(),  # type: ignore[arg-type]
        )
    assert caught.value.category == "invalid_request"
    with pytest.raises(RunRequestError) as caught:
        replace(valid, behavioural_baseline=None)  # type: ignore[arg-type]
    assert caught.value.category == "invalid_request"
    with pytest.raises(RunRequestError) as caught:
        load_case_run_request(object())  # type: ignore[arg-type]
    assert caught.value.category == "invalid_request"


def test_opaque_run_id_may_contain_reserved_substring(data_root: Path, tmp_path: Path) -> None:
    request = build_request(data_root, tmp_path / "out", run_id="case-active-god-fcr")
    assert request.run_id == "case-active-god-fcr"
    restored = case_run_request_from_payload(serialize_case_run_request(request))
    assert restored.run_id == "case-active-god-fcr"
    assert isinstance(restored, CaseRunRequest)
