from __future__ import annotations

from dataclasses import replace

import pytest

from stepinbel.config import AssetConfig
from stepinbel.workflows import (
    MAX_ASSET_SWEEP_CANDIDATES,
    AssetSweepCandidate,
    RunRequestError,
    build_asset_sweep_request,
    build_symmetric_asset_size_candidates,
)
from tests.workflow_helpers import da_config, two_asset_candidates


def test_explicit_symmetric_asymmetric_and_pond_candidates() -> None:
    symmetric = AssetSweepCandidate("sym", "symmetric", AssetConfig(power_pump_mw=1.0, power_turbine_mw=1.0, storage_hours=4.0))
    asymmetric = AssetSweepCandidate(
        "asym",
        "asymmetric",
        AssetConfig(power_pump_mw=1.0, power_turbine_mw=2.0, storage_hours=3.0),
    )
    pond = AssetSweepCandidate(
        "pond",
        "pond energy",
        AssetConfig(power_pump_mw=1.5, power_turbine_mw=1.5, storage_hours=None, pond_energy_mwh=8.0),
    )
    assert symmetric.asset.power_pump_mw == symmetric.asset.power_turbine_mw
    assert asymmetric.asset.machines_asymmetric()
    assert pond.asset.e_max_source() == "pond_energy_mwh"


@pytest.mark.parametrize(
    "candidate_id",
    ("", "A1", "bad id", "con", "PRN", "aux", "nul", "com1", "lpt9", "a" * 49, "-leading"),
)
def test_invalid_candidate_ids(candidate_id: str) -> None:
    with pytest.raises(RunRequestError) as caught:
        AssetSweepCandidate(candidate_id, "ok", AssetConfig())
    assert caught.value.category == "invalid_request"


@pytest.mark.parametrize(
    "label",
    ("", " padded", "padded ", "a" * 121, "line\nbreak", "tab\there"),
)
def test_invalid_labels(label: str) -> None:
    with pytest.raises(RunRequestError) as caught:
        AssetSweepCandidate("ok-id", label, AssetConfig())
    assert caught.value.category == "invalid_request"


def test_invalid_candidate_types() -> None:
    with pytest.raises(RunRequestError):
        AssetSweepCandidate("ok-id", "ok", object())  # type: ignore[arg-type]
    with pytest.raises(RunRequestError):
        AssetSweepCandidate(1, "ok", AssetConfig())  # type: ignore[arg-type]


def test_helper_sorts_duration_then_power_and_preserves_template() -> None:
    template = AssetConfig(
        power_pump_mw=9.0,
        power_turbine_mw=8.0,
        eta_pump=0.8,
        eta_turbine=0.85,
        storage_hours=1.0,
        storage_hours_basis="stored_energy",
        soc_initial_frac=0.4,
        soc_terminal_frac=0.6,
        enforce_terminal_soc=False,
        pump_ramp_up_min=12.0,
    )
    candidates = build_symmetric_asset_size_candidates(
        template,
        powers_mw=(2.0, 1.0),
        storage_hours=(4, 2.0),
    )
    assert tuple(item.candidate_id for item in candidates) == ("asset-001", "asset-002", "asset-003", "asset-004")
    assert [item.asset.storage_hours for item in candidates] == [2.0, 2.0, 4.0, 4.0]
    assert [item.asset.power_pump_mw for item in candidates] == [1.0, 2.0, 1.0, 2.0]
    assert [item.asset.power_turbine_mw for item in candidates] == [1.0, 2.0, 1.0, 2.0]
    assert candidates[0].label == "1 MW / 2 h symmetric"
    assert candidates[3].label == "2 MW / 4 h symmetric"
    for item in candidates:
        assert item.asset.pond_energy_mwh is None
        assert item.asset.eta_pump == 0.8
        assert item.asset.storage_hours_basis == "stored_energy"
        assert item.asset.soc_initial_frac == 0.4
        assert item.asset.enforce_terminal_soc is False
        assert item.asset.pump_ramp_up_min == 12.0
    assert isinstance(candidates, tuple)


def test_helper_rejects_bad_axes() -> None:
    template = AssetConfig()
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(object(), powers_mw=(1.0,), storage_hours=(2.0,))  # type: ignore[arg-type]
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(template, powers_mw="1,2", storage_hours=(2.0,))
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(template, powers_mw={"a": 1}, storage_hours=(2.0,))
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(template, powers_mw=(), storage_hours=(2.0,))
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(template, powers_mw=(1.0, 1.0), storage_hours=(2.0,))
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(template, powers_mw=(0.0,), storage_hours=(2.0,))
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(template, powers_mw=(True,), storage_hours=(2.0,))
    with pytest.raises(RunRequestError):
        build_symmetric_asset_size_candidates(
            template,
            powers_mw=tuple(float(i) for i in range(1, 6)),
            storage_hours=tuple(float(i) for i in range(1, 7)),
        )


def test_duplicate_ids_and_assets_and_count_limits(data_root, tmp_path) -> None:
    first, second = two_asset_candidates()
    with pytest.raises(RunRequestError, match="unique"):
        build_asset_sweep_request(da_config(), (first, replace(second, candidate_id="small")), data_root, tmp_path / "dup-id")
    clone = AssetSweepCandidate("other", "other label", first.asset)
    with pytest.raises(RunRequestError, match="AssetConfig"):
        build_asset_sweep_request(da_config(), (first, clone), data_root, tmp_path / "dup-asset")
    with pytest.raises(RunRequestError):
        build_asset_sweep_request(da_config(), (), data_root, tmp_path / "empty")
    with pytest.raises(RunRequestError):
        build_asset_sweep_request(da_config(), set(two_asset_candidates()), data_root, tmp_path / "set")  # type: ignore[arg-type]
    too_many = build_symmetric_asset_size_candidates(
        AssetConfig(),
        powers_mw=tuple(float(i) for i in range(1, 5)),
        storage_hours=tuple(float(i) for i in range(1, 7)),
    )
    assert len(too_many) == 24
    extra = AssetSweepCandidate(
        "extra-id",
        "extra",
        AssetConfig(power_pump_mw=9.0, power_turbine_mw=9.0, storage_hours=9.0),
    )
    with pytest.raises(RunRequestError):
        build_asset_sweep_request(da_config(), too_many + (extra,), data_root, tmp_path / "too-many")
    assert MAX_ASSET_SWEEP_CANDIDATES == 24
