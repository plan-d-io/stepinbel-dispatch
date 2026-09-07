from __future__ import annotations

import hashlib
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from types import MappingProxyType

import pytest

from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    DayAheadCase,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SiteConfig,
)
from stepinbel.optimizer import SolverOptions
from stepinbel.reporting import validate_market_comparison_artifacts, validate_run_artifacts
from stepinbel.workflows import load_market_comparison_request
from stepinbel.workflows.constants import BEHAVIOURAL_BASELINE

REPO_ROOT = Path(__file__).resolve().parents[1]
DEMO_ROOT = REPO_ROOT / "ui" / "demo_artifacts"
INDEX_PATH = DEMO_ROOT / "index.json"
ARTIFACT_DIR = DEMO_ROOT / "stepinbel_2025_all_markets_pv500"
MANIFEST_PATH = ARTIFACT_DIR / "artifact_manifest.json"
SOURCE_COMMIT = "48cbd889f41d6f34fbbfab4c18c735d4c015c1a8"
INDEX_KEYS = (
    "demo_index_schema_version",
    "demo_id",
    "display_name",
    "relative_directory",
    "source_stepinbel_commit",
    "source_data_manifest_sha256",
    "comparison_artifact_schema_version",
    "artifact_manifest_byte_size",
    "artifact_manifest_sha256",
    "markets",
    "period",
    "configuration",
    "highest_revenue_market",
    "total_site_revenue_eur_by_market",
    "interpretation",
)
MARKETS = ("da", "mfrr", "afrr")
MAX_DIRECTORY_BYTES = 30 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_index() -> dict[str, object]:
    assert INDEX_PATH.is_file()
    raw = INDEX_PATH.read_bytes()
    assert raw.endswith(b"\n")
    assert b"\r" not in raw
    payload = json.loads(raw.decode("utf-8"))
    assert type(payload) is dict
    return payload


def _require_int(value: object, name: str) -> int:
    assert type(value) is int, f"{name} must be an int, not {type(value).__name__}"
    return value


def _require_str(value: object, name: str) -> str:
    assert type(value) is str, f"{name} must be a str"
    assert value, f"{name} must be non-empty"
    return value


def _require_finite_number(value: object, name: str) -> float:
    assert type(value) is not bool, f"{name} must not be a boolean"
    assert type(value) in (int, float), f"{name} must be a number"
    number = float(value)
    assert math.isfinite(number), f"{name} must be finite"
    return number


@pytest.fixture(scope="module")
def index() -> dict[str, object]:
    return _load_index()


@pytest.fixture(scope="module")
def request_obj():
    return load_market_comparison_request(ARTIFACT_DIR / "comparison_request.json")


def test_demo_paths_are_project_relative() -> None:
    assert INDEX_PATH.is_relative_to(REPO_ROOT)
    assert ARTIFACT_DIR.is_relative_to(REPO_ROOT)
    assert INDEX_PATH.is_file()
    assert ARTIFACT_DIR.is_dir()
    assert (ARTIFACT_DIR / "comparison_request.json").is_file()
    assert MANIFEST_PATH.is_file()


def test_index_keys_and_types(index: dict[str, object]) -> None:
    assert tuple(index) == INDEX_KEYS
    assert set(index) == set(INDEX_KEYS)
    assert _require_int(index["demo_index_schema_version"], "demo_index_schema_version") == 1
    assert _require_str(index["demo_id"], "demo_id") == "stepinbel-2025-all-markets-pv500"
    assert _require_str(index["display_name"], "display_name") == "2025 all-market demonstration"
    assert _require_int(
        index["comparison_artifact_schema_version"],
        "comparison_artifact_schema_version",
    ) == 1
    relative = _require_str(index["relative_directory"], "relative_directory")
    assert relative == "stepinbel_2025_all_markets_pv500"
    assert "/" not in relative
    assert "\\" not in relative
    assert ".." not in relative
    assert Path(relative).name == relative
    assert not Path(relative).is_absolute()
    assert ARTIFACT_DIR == DEMO_ROOT / relative
    assert type(index["markets"]) is list
    assert index["markets"] == list(MARKETS)
    assert all(type(market) is str for market in index["markets"])
    period = _require_str(index["period"], "period")
    assert "Belgian" in period
    assert "2025" in period
    configuration = _require_str(index["configuration"], "configuration")
    assert "1 MW" in configuration
    assert "4 h" in configuration
    assert "0.84" in configuration
    assert "0.90" in configuration
    assert "500 kW" in configuration
    assert "balanced" in configuration
    assert "P50" in configuration
    interpretation = _require_str(index["interpretation"], "interpretation")
    assert "dedicated-market" in interpretation
    assert "not additive" in interpretation
    assert "perfect-foresight" in interpretation
    assert "forecast" in interpretation
    totals = index["total_site_revenue_eur_by_market"]
    assert type(totals) is dict
    assert tuple(totals) == MARKETS
    for market, value in totals.items():
        _require_finite_number(value, f"total_site_revenue_eur_by_market.{market}")


def test_index_rejects_boolean_numbers() -> None:
    raw = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    raw["demo_index_schema_version"] = True
    raw["artifact_manifest_byte_size"] = False
    raw["total_site_revenue_eur_by_market"] = {
        "da": True,
        "mfrr": False,
        "afrr": 1.0,
    }
    with pytest.raises(AssertionError):
        _require_int(raw["demo_index_schema_version"], "demo_index_schema_version")
    with pytest.raises(AssertionError):
        _require_int(raw["artifact_manifest_byte_size"], "artifact_manifest_byte_size")
    with pytest.raises(AssertionError):
        _require_finite_number(raw["total_site_revenue_eur_by_market"]["da"], "da")
    with pytest.raises(AssertionError):
        _require_finite_number(raw["total_site_revenue_eur_by_market"]["mfrr"], "mfrr")


def test_source_commit_and_manifest_hash(index: dict[str, object]) -> None:
    assert index["source_stepinbel_commit"] == SOURCE_COMMIT
    size = _require_int(index["artifact_manifest_byte_size"], "artifact_manifest_byte_size")
    digest = _require_str(index["artifact_manifest_sha256"], "artifact_manifest_sha256")
    assert size == MANIFEST_PATH.stat().st_size
    assert digest == _sha256(MANIFEST_PATH)
    assert len(digest) == 64


def test_validate_comparison_is_read_only_and_unmodified() -> None:
    before = _sha256(MANIFEST_PATH)
    artifacts = validate_market_comparison_artifacts(ARTIFACT_DIR)
    assert isinstance(artifacts, MappingProxyType)
    with pytest.raises((TypeError, AttributeError)):
        artifacts["report.txt"] = ARTIFACT_DIR  # type: ignore[index]
    assert _sha256(MANIFEST_PATH) == before


def test_frozen_request_matches_demo_contract(index: dict[str, object], request_obj) -> None:
    assert tuple(request_obj.case_requests) == MARKETS
    assert request_obj.run_id == "demo-2025-all-markets-pv500"
    assert request_obj.created_at_utc == datetime(2026, 9, 5, 0, 0, tzinfo=timezone.utc)
    assert request_obj.data_manifest_sha256 == index["source_data_manifest_sha256"]
    assert dict(request_obj.behavioural_baseline) == dict(BEHAVIOURAL_BASELINE)
    expected_period = BelgianDeliveryPeriod(date(2025, 1, 1), date(2025, 12, 31))
    expected_asset = AssetConfig(
        power_pump_mw=1.0,
        power_turbine_mw=1.0,
        eta_pump=0.84,
        eta_turbine=0.90,
        storage_hours=4.0,
        storage_hours_basis="discharge_at_rated",
        pond_energy_mwh=None,
        soc_initial_frac=0.5,
        soc_terminal_frac=0.5,
        enforce_terminal_soc=True,
        pump_ramp_up_min=10.0,
        pump_ramp_down_min=1.0,
        pump_ramp_power_frac=0.10,
        turbine_ramp_up_min=2.0,
        turbine_ramp_down_min=2.0,
        turbine_ramp_power_frac=0.0,
    )
    expected_site = SiteConfig(
        grid_import_mw=1.0,
        grid_export_mw=1.0,
        pv_ac_kw=500.0,
        pv_region="Belgium",
        pv_revenue_mode="da",
        pv_fixed_price_eur_mwh=None,
    )
    expected_options = SolverOptions(detailed_output=False)
    assert expected_asset.e_max_mwh() == 4.444444444444445
    assert expected_asset.usable_energy_mwh() == 4.0
    assert expected_asset.round_trip_efficiency() == 0.756
    expected_cases = {
        "da": DayAheadCase(),
        "mfrr": MFRRCase(
            activation_profile="balanced",
            capacity_bid=HistoricalQuantileCapacityBid(0.50),
            capacity_coverage_hours=4.0,
        ),
        "afrr": AFRRCase(
            activation_profile="balanced",
            capacity_bid=HistoricalQuantileCapacityBid(0.50),
            capacity_coverage_hours=4.0,
            up_capacity_fraction=1.0,
        ),
    }
    for market, child in request_obj.case_requests.items():
        assert child.config.period == expected_period
        assert child.config.asset == expected_asset
        assert child.config.site == expected_site
        assert child.config.market_case == expected_cases[market]
        assert child.solver_options == expected_options
        assert child.data_manifest_sha256 == request_obj.data_manifest_sha256
        assert dict(child.behavioural_baseline) == dict(BEHAVIOURAL_BASELINE)
        assert child.created_at_utc == request_obj.created_at_utc


def test_summaries_accounting_and_index_totals(index: dict[str, object]) -> None:
    artifacts = validate_market_comparison_artifacts(ARTIFACT_DIR)
    summary = json.loads((ARTIFACT_DIR / "comparison_summary.json").read_text(encoding="utf-8"))
    assert summary["interval_count"] == 35040
    assert summary["duration_hours"] == 8760.0
    assert summary["highest_revenue_market"] == index["highest_revenue_market"] == "afrr"
    index_totals = index["total_site_revenue_eur_by_market"]
    assert type(index_totals) is dict
    for row in summary["rows"]:
        assert row["interval_count"] == 35040
        assert row["duration_hours"] == 8760.0
        assert row["total_site_revenue_eur"] == (
            row["market_energy_net_eur"]
            + row["capacity_revenue_eur"]
            + row["pv_revenue_eur"]
        )
        market = row["market"]
        child = json.loads((ARTIFACT_DIR / "cases" / market / "summary.json").read_text(encoding="utf-8"))
        assert child["interval_count"] == 35040
        assert child["duration_hours"] == 8760.0
        assert child["total_site_revenue_eur"] == (
            child["market_energy_net_eur"]
            + child["capacity_revenue_eur"]
            + child["pv_revenue_eur"]
        )
        assert child["total_site_revenue_eur"] == row["total_site_revenue_eur"]
        assert index_totals[market] == child["total_site_revenue_eur"]
        validate_run_artifacts(ARTIFACT_DIR / "cases" / market)
    assert set(artifacts)  # mapping is populated after successful validation


def test_no_gurobi_reference_or_dependency() -> None:
    assert "gurobipy" not in sys.modules
    needles = ("gurobi", "gurobipy")
    text_suffixes = {".json", ".jsonl", ".csv", ".txt", ".log", ".md"}
    for path in DEMO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in text_suffixes:
            continue
        text = path.read_text(encoding="utf-8").lower()
        for needle in needles:
            assert needle not in text, f"{path} contains {needle}"
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "gurobipy" not in pyproject


def test_artifact_footprint_bounds() -> None:
    files = [path for path in ARTIFACT_DIR.rglob("*") if path.is_file()]
    assert files
    total = 0
    for path in files:
        size = path.stat().st_size
        assert size < MAX_FILE_BYTES, f"{path} is {size} bytes"
        total += size
    assert total < MAX_DIRECTORY_BYTES
