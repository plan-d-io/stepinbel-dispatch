from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from stepinbel.config import AssetConfig, SimulationConfig
from stepinbel.optimizer import SolverOptions
from stepinbel.workflows import (
    AssetSweepCandidate,
    RunRequestError,
    asset_sweep_request_from_payload,
    build_asset_sweep_request,
    load_asset_sweep_request,
    serialize_asset_sweep_request,
)
from stepinbel.workflows.sweep_request import child_run_id
from tests.workflow_helpers import da_config, mfrr_config, two_asset_candidates, utc


def _build(data_root: Path, tmp_path: Path, **kwargs):
    return build_asset_sweep_request(
        kwargs.get("config", da_config()),
        kwargs.get("candidates", two_asset_candidates()),
        data_root,
        tmp_path / kwargs.get("name", "sweep"),
        run_id=kwargs.get("run_id", "sweep-parent-01"),
        created_at_utc=kwargs.get("created_at_utc", utc(2026, 1, 1, 12, 0)),
        solver_options=kwargs.get("solver_options"),
    )


def test_builder_writes_nothing_and_freezes_hash(data_root: Path, tmp_path: Path) -> None:
    output = tmp_path / "sweep"
    request = _build(data_root, tmp_path)
    assert not output.exists()
    from stepinbel.data import open_published_bundle

    assert request.data_manifest_sha256 == open_published_bundle(data_root).manifest_sha256
    assert request.market == "da"
    assert request.candidate_order == ("small", "large")
    assert isinstance(request.case_requests, MappingProxyType)
    assert isinstance(request.candidate_labels, MappingProxyType)
    with pytest.raises((TypeError, AttributeError)):
        request.case_requests["small"] = request.case_requests["large"]  # type: ignore[index]


def test_caller_mutation_cannot_alter_frozen_request(data_root: Path, tmp_path: Path) -> None:
    candidates = list(two_asset_candidates())
    request = build_asset_sweep_request(da_config(), candidates, data_root, tmp_path / "mut")
    candidates.append(
        AssetSweepCandidate("third", "third", AssetConfig(power_pump_mw=3.0, power_turbine_mw=3.0, storage_hours=3.0))
    )
    assert request.candidate_order == ("small", "large")


def test_deterministic_child_ids_directories_and_shared_fields(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path)
    first = request.case_requests["small"]
    for candidate_id, child in request.case_requests.items():
        assert child.run_id == child_run_id(request.run_id, candidate_id)
        assert child.output_directory == (tmp_path / "sweep" / "cases" / candidate_id).resolve()
        assert child.created_at_utc == request.created_at_utc
        assert child.software_version == request.software_version
        assert child.data_directory == request.data_directory
        assert child.data_manifest_sha256 == request.data_manifest_sha256
        assert child.solver_options == first.solver_options
        assert dict(child.behavioural_baseline) == dict(request.behavioural_baseline)
        assert child.config.period == first.config.period
        assert child.config.market_case == first.config.market_case
        assert child.config.site == first.config.site
        assert child.config.market == "da"
    assert request.case_requests["small"].config.asset != request.case_requests["large"].config.asset


def test_json_round_trip(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path)
    payload = serialize_asset_sweep_request(request)
    restored = asset_sweep_request_from_payload(payload)
    assert serialize_asset_sweep_request(restored) == payload


def test_wrong_extra_missing_keys(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_asset_sweep_request(_build(data_root, tmp_path))
    missing = dict(payload)
    missing.pop("market")
    with pytest.raises(RunRequestError, match="missing"):
        asset_sweep_request_from_payload(missing)
    extra = dict(payload)
    extra["note"] = "x"
    with pytest.raises(RunRequestError, match="unexpected"):
        asset_sweep_request_from_payload(extra)


@pytest.mark.parametrize("value", (True, 1.0, 1.5, False, "1", None))
@pytest.mark.parametrize("field", ("asset_sweep_request_schema_version", "asset_sweep_artifact_schema_version"))
def test_schema_versions_must_be_exact_ints(data_root: Path, tmp_path: Path, field: str, value) -> None:
    valid = _build(data_root, tmp_path)
    with pytest.raises(RunRequestError) as caught:
        replace(valid, **{field: value})
    assert caught.value.category == "invalid_request"
    payload = copy.deepcopy(serialize_asset_sweep_request(valid))
    payload[field] = value
    with pytest.raises(RunRequestError) as caught:
        asset_sweep_request_from_payload(payload)
    assert caught.value.category == "invalid_request"


@pytest.mark.parametrize("value", (True, 1.0, "1"))
@pytest.mark.parametrize("field", ("request_schema_version", "artifact_schema_version"))
def test_nested_one_case_schema_versions(data_root: Path, tmp_path: Path, field: str, value) -> None:
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path)))
    payload["case_requests"]["small"][field] = value
    with pytest.raises(RunRequestError) as caught:
        asset_sweep_request_from_payload(payload)
    assert caught.value.category == "invalid_request"


def test_wrong_primitive_types_and_path_like_input(data_root: Path, tmp_path: Path) -> None:
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path)))
    payload["market"] = 1
    with pytest.raises(RunRequestError):
        asset_sweep_request_from_payload(payload)
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path / "b")))
    payload["candidate_order"] = {"small": 1, "large": 2}
    with pytest.raises(RunRequestError):
        asset_sweep_request_from_payload(payload)
    with pytest.raises(RunRequestError):
        load_asset_sweep_request(object())  # type: ignore[arg-type]
    payload = serialize_asset_sweep_request(_build(data_root, tmp_path / "rel"))
    payload["data_directory"] = "data"
    with pytest.raises(RunRequestError, match="absolute"):
        asset_sweep_request_from_payload(payload)


def test_swapped_missing_extra_child(data_root: Path, tmp_path: Path) -> None:
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path)))
    payload["candidate_order"] = ["large", "small"]
    restored = asset_sweep_request_from_payload(payload)
    assert restored.candidate_order == ("large", "small")
    payload["case_requests"].pop("large")
    with pytest.raises(RunRequestError):
        asset_sweep_request_from_payload(payload)
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path / "extra")))
    payload["case_requests"]["bonus"] = payload["case_requests"]["small"]
    with pytest.raises(RunRequestError):
        asset_sweep_request_from_payload(payload)


def test_wrong_child_fields(data_root: Path, tmp_path: Path) -> None:
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path)))
    payload["case_requests"]["small"]["run_id"] = "not-the-child"
    with pytest.raises(RunRequestError, match="deterministic"):
        asset_sweep_request_from_payload(payload)
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path / "out")))
    payload["case_requests"]["small"]["output_directory"] = str(tmp_path / "elsewhere")
    with pytest.raises(RunRequestError, match="cases"):
        asset_sweep_request_from_payload(payload)
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path / "sw")))
    payload["case_requests"]["small"]["software_version"] = "other"
    with pytest.raises(RunRequestError, match="software_version"):
        asset_sweep_request_from_payload(payload)
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path / "hash")))
    payload["case_requests"]["small"]["data_manifest_sha256"] = "ab" * 32
    with pytest.raises(RunRequestError, match="data_manifest"):
        asset_sweep_request_from_payload(payload)
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path / "base")))
    payload["case_requests"]["small"]["behavioural_baseline"]["tag"] = "other"
    with pytest.raises(RunRequestError):
        asset_sweep_request_from_payload(payload)
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path / "opt")))
    payload["case_requests"]["large"]["solver_options"]["detailed_output"] = True
    with pytest.raises(RunRequestError, match="solver"):
        asset_sweep_request_from_payload(payload)


def test_duplicate_asset_after_payload_tampering(data_root: Path, tmp_path: Path) -> None:
    payload = copy.deepcopy(serialize_asset_sweep_request(_build(data_root, tmp_path)))
    payload["case_requests"]["large"]["config"]["asset"] = payload["case_requests"]["small"]["config"]["asset"]
    with pytest.raises(RunRequestError, match="AssetConfig"):
        asset_sweep_request_from_payload(payload)


def test_replay_does_not_reopen_data(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_asset_sweep_request(_build(data_root, tmp_path))
    with patch("stepinbel.workflows.sweep_request.open_published_bundle") as mocked:
        restored = asset_sweep_request_from_payload(payload)
    mocked.assert_not_called()
    assert restored.run_id == "sweep-parent-01"


def test_mfrr_market_is_taken_from_base_config(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path, config=mfrr_config(), name="mfrr")
    assert request.market == "mfrr"
    assert all(child.config.market == "mfrr" for child in request.case_requests.values())


def test_base_config_must_be_simulation_config(data_root: Path, tmp_path: Path) -> None:
    with pytest.raises(RunRequestError) as caught:
        build_asset_sweep_request(object(), two_asset_candidates(), data_root, tmp_path / "x")  # type: ignore[arg-type]
    assert caught.value.category == "invalid_request"
    assert type(da_config()) is SimulationConfig
    with pytest.raises(RunRequestError):
        build_asset_sweep_request(
            da_config(),
            two_asset_candidates(),
            data_root,
            tmp_path / "opts",
            solver_options={"detailed_output": False},  # type: ignore[arg-type]
        )
