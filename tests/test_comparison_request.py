from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from types import MappingProxyType
from unittest.mock import patch

import pytest

from stepinbel.config import AssetConfig, SiteConfig
from stepinbel.optimizer import SolverOptions
from stepinbel.workflows import (
    MarketComparisonRequest,
    RunRequestError,
    build_market_comparison_request,
    load_market_comparison_request,
    market_comparison_request_from_payload,
    serialize_market_comparison_request,
)
from stepinbel.workflows.comparison_request import child_run_id
from tests.workflow_helpers import (
    afrr_config,
    comparison_configs,
    da_config,
    da_period,
    mfrr_config,
    one_day_belgian,
    utc,
)


def _build(data_root: Path, tmp_path: Path, **kwargs):
    return build_market_comparison_request(
        comparison_configs(),
        data_root,
        tmp_path / "cmp",
        run_id=kwargs.get("run_id", "cmp-parent-01"),
        created_at_utc=kwargs.get("created_at_utc", utc(2026, 1, 1, 12, 0)),
        solver_options=kwargs.get("solver_options"),
    )


def test_deterministic_construction_and_child_ids(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path)
    assert request.run_id == "cmp-parent-01"
    assert tuple(request.case_requests) == ("da", "mfrr", "afrr")
    for market, child in request.case_requests.items():
        assert child.run_id == child_run_id(request.run_id, market)
        assert child.output_directory == (tmp_path / "cmp" / "cases" / market).resolve()
        assert child.created_at_utc == request.created_at_utc
        assert child.data_directory == request.data_directory
        assert child.data_manifest_sha256 == request.data_manifest_sha256
        assert child.software_version == request.software_version
        assert child.config.market == market
    assert not request.output_directory.exists()
    assert request.case_requests["da"].config.period == request.case_requests["mfrr"].config.period
    assert request.case_requests["da"].config.asset == request.case_requests["afrr"].config.asset


def test_generated_parent_uniqueness(data_root: Path, tmp_path: Path) -> None:
    first = build_market_comparison_request(comparison_configs(), data_root, tmp_path / "a")
    second = build_market_comparison_request(comparison_configs(), data_root, tmp_path / "b")
    assert first.run_id != second.run_id
    assert first.case_requests["da"].run_id != second.case_requests["da"].run_id


def test_json_round_trip(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path)
    payload = serialize_market_comparison_request(request)
    restored = market_comparison_request_from_payload(payload)
    assert serialize_market_comparison_request(restored) == payload
    assert restored.case_requests["mfrr"].run_id == request.case_requests["mfrr"].run_id


def test_mapping_is_read_only(data_root: Path, tmp_path: Path) -> None:
    request = _build(data_root, tmp_path)
    assert isinstance(request.case_requests, MappingProxyType)
    with pytest.raises((TypeError, AttributeError)):
        request.case_requests["da"] = request.case_requests["mfrr"]  # type: ignore[index]


@pytest.mark.parametrize("markets", (("da", "mfrr"), ("da", "afrr"), ("mfrr", "afrr"), ("da", "mfrr", "afrr")))
def test_valid_selected_market_sets(data_root: Path, tmp_path: Path, markets: tuple[str, ...]) -> None:
    request = build_market_comparison_request(
        comparison_configs(markets=markets),
        data_root,
        tmp_path / ("sel-" + "-".join(markets)),
        run_id="cmp-sel-01",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    assert tuple(request.case_requests) == markets
    payload = serialize_market_comparison_request(request)
    assert tuple(payload["case_requests"]) == markets
    restored = market_comparison_request_from_payload(payload)
    assert serialize_market_comparison_request(restored) == payload
    assert tuple(restored.case_requests) == markets
    assert isinstance(request.case_requests, MappingProxyType)


def test_insertion_order_is_canonicalized(data_root: Path, tmp_path: Path) -> None:
    request = build_market_comparison_request(
        comparison_configs(markets=("afrr", "da")),
        data_root,
        tmp_path / "order",
        run_id="cmp-order",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    assert tuple(request.case_requests) == ("da", "afrr")
    with pytest.raises((TypeError, AttributeError)):
        request.case_requests["mfrr"] = request.case_requests["da"]  # type: ignore[index]


def test_invalid_membership_rejected(data_root: Path, tmp_path: Path) -> None:
    configs = comparison_configs()
    with pytest.raises(RunRequestError, match="two or three"):
        build_market_comparison_request({}, data_root, tmp_path / "empty")
    with pytest.raises(RunRequestError, match="two or three"):
        build_market_comparison_request({"da": configs["da"]}, data_root, tmp_path / "one")
    extra = dict(configs)
    extra["fcr"] = configs["da"]
    with pytest.raises(RunRequestError, match="subset"):
        build_market_comparison_request(extra, data_root, tmp_path / "extra")
    with pytest.raises(RunRequestError, match="subset"):
        build_market_comparison_request({"da": configs["da"], "fcr": configs["da"]}, data_root, tmp_path / "unknown")


def test_wrong_market_key_pairing(data_root: Path, tmp_path: Path) -> None:
    configs = comparison_configs()
    configs["da"] = mfrr_config(period=one_day_belgian())
    with pytest.raises(RunRequestError, match="market must be"):
        build_market_comparison_request(configs, data_root, tmp_path / "x")


def test_mismatched_shared_configuration(data_root: Path, tmp_path: Path) -> None:
    period = one_day_belgian()
    configs = comparison_configs(period=period)
    configs["afrr"] = afrr_config(period=period, asset=AssetConfig(power_pump_mw=2.5))
    with pytest.raises(RunRequestError, match="asset"):
        build_market_comparison_request(configs, data_root, tmp_path / "asset")
    configs = comparison_configs(period=period)
    configs["mfrr"] = mfrr_config(period=period, site=SiteConfig(grid_import_mw=1.0))
    with pytest.raises(RunRequestError, match="site"):
        build_market_comparison_request(configs, data_root, tmp_path / "site")
    configs = comparison_configs(period=period)
    configs["da"] = da_config(period=da_period())
    with pytest.raises(RunRequestError, match="period"):
        build_market_comparison_request(configs, data_root, tmp_path / "period")


def test_relative_replay_paths_are_rejected(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_market_comparison_request(_build(data_root, tmp_path))
    payload["data_directory"] = "data"
    with pytest.raises(RunRequestError, match="absolute"):
        market_comparison_request_from_payload(payload)
    payload = serialize_market_comparison_request(_build(data_root, tmp_path / "two"))
    payload["case_requests"]["da"]["output_directory"] = "relative-out"
    with pytest.raises(RunRequestError, match="absolute"):
        market_comparison_request_from_payload(payload)


def test_malformed_nested_request_rejected(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_market_comparison_request(_build(data_root, tmp_path))
    payload["case_requests"]["mfrr"]["run_id"] = ""
    with pytest.raises(RunRequestError):
        market_comparison_request_from_payload(payload)


def test_invalid_public_types_become_run_request_error(data_root: Path, tmp_path: Path) -> None:
    with pytest.raises(RunRequestError) as caught:
        build_market_comparison_request(object(), data_root, tmp_path / "x")  # type: ignore[arg-type]
    assert caught.value.category == "invalid_request"
    with pytest.raises(RunRequestError) as caught:
        build_market_comparison_request(
            comparison_configs(),
            data_root,
            tmp_path / "y",
            created_at_utc=object(),  # type: ignore[arg-type]
        )
    assert caught.value.category == "invalid_request"
    with pytest.raises(RunRequestError) as caught:
        build_market_comparison_request(
            comparison_configs(),
            data_root,
            tmp_path / "z",
            solver_options={"detailed_output": False},  # type: ignore[arg-type]
        )
    assert caught.value.category == "invalid_request"
    valid = _build(data_root, tmp_path / "ok")
    with pytest.raises(RunRequestError):
        replace(valid, behavioural_baseline=None)  # type: ignore[arg-type]
    with pytest.raises(RunRequestError):
        load_market_comparison_request(object())  # type: ignore[arg-type]


def test_replay_does_not_reopen_data(data_root: Path, tmp_path: Path) -> None:
    payload = serialize_market_comparison_request(_build(data_root, tmp_path))
    with patch("stepinbel.workflows.comparison_request.open_published_bundle") as mocked:
        restored = market_comparison_request_from_payload(payload)
    mocked.assert_not_called()
    assert restored.run_id == "cmp-parent-01"
    subset = serialize_market_comparison_request(
        build_market_comparison_request(
            comparison_configs(markets=("mfrr", "afrr")),
            data_root,
            tmp_path / "subset-replay",
            run_id="cmp-sub-replay",
            created_at_utc=utc(2026, 1, 1, 12, 0),
        )
    )
    with patch("stepinbel.workflows.comparison_request.open_published_bundle") as mocked:
        restored_subset = market_comparison_request_from_payload(subset)
    mocked.assert_not_called()
    assert tuple(restored_subset.case_requests) == ("mfrr", "afrr")


def test_subset_shared_configuration_mismatch(data_root: Path, tmp_path: Path) -> None:
    period = one_day_belgian()
    configs = comparison_configs(period=period, markets=("da", "afrr"))
    configs["afrr"] = afrr_config(period=period, asset=AssetConfig(power_pump_mw=2.5))
    with pytest.raises(RunRequestError, match="asset"):
        build_market_comparison_request(configs, data_root, tmp_path / "sub-asset")


def test_mismatched_solver_options_on_replay(data_root: Path, tmp_path: Path) -> None:
    request = build_market_comparison_request(
        comparison_configs(),
        data_root,
        tmp_path / "opts",
        run_id="cmp-opts",
        solver_options=SolverOptions(detailed_output=False),
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    payload = serialize_market_comparison_request(request)
    payload["case_requests"]["afrr"]["solver_options"]["detailed_output"] = True
    with pytest.raises(RunRequestError, match="solver options"):
        market_comparison_request_from_payload(payload)


_INVALID_SCHEMA_VALUES = (1.0, 1.5, True, False, "1", None, 0, 2, [1], {"v": 1})


@pytest.mark.parametrize("value", _INVALID_SCHEMA_VALUES)
def test_direct_construction_rejects_non_int_schema_versions(
    data_root: Path, tmp_path: Path, value
) -> None:
    valid = _build(data_root, tmp_path)
    with pytest.raises(RunRequestError) as caught:
        replace(valid, comparison_request_schema_version=value)
    assert caught.value.category == "invalid_request"
    with pytest.raises(RunRequestError) as caught:
        replace(valid, comparison_artifact_schema_version=value)
    assert caught.value.category == "invalid_request"


@pytest.mark.parametrize("value", _INVALID_SCHEMA_VALUES)
@pytest.mark.parametrize(
    "field",
    ("comparison_request_schema_version", "comparison_artifact_schema_version"),
)
def test_payload_replay_rejects_non_int_schema_versions(
    data_root: Path, tmp_path: Path, field: str, value
) -> None:
    payload = copy.deepcopy(serialize_market_comparison_request(_build(data_root, tmp_path)))
    payload[field] = value
    with pytest.raises(RunRequestError) as caught:
        market_comparison_request_from_payload(payload)
    assert caught.value.category == "invalid_request"


@pytest.mark.parametrize("value", _INVALID_SCHEMA_VALUES)
@pytest.mark.parametrize("field", ("request_schema_version", "artifact_schema_version"))
def test_nested_case_schema_versions_must_be_exact_ints(
    data_root: Path, tmp_path: Path, field: str, value
) -> None:
    payload = copy.deepcopy(serialize_market_comparison_request(_build(data_root, tmp_path)))
    payload["case_requests"]["da"][field] = value
    with pytest.raises(RunRequestError) as caught:
        market_comparison_request_from_payload(payload)
    assert caught.value.category == "invalid_request"
