from __future__ import annotations

import ast
from pathlib import Path

import pytest

import stepinbel
import stepinbel.cli
from stepinbel.cli.builders import (
    build_asset,
    build_market_case,
    build_period,
    build_site,
    parse_numeric_axis,
)
from stepinbel.cli.errors import CliError
from stepinbel.cli.parser import build_parser, parse_args
from stepinbel.config import (
    AFRRCase,
    AssetConfig,
    BelgianDeliveryPeriod,
    DayAheadCase,
    FixedMinimumCapacityBid,
    HistoricalQuantileCapacityBid,
    MFRRCase,
    SiteConfig,
    UtcPeriod,
)
from tests.workflow_helpers import utc

REPO_ROOT = Path(__file__).resolve().parents[1]
CLI_ROOT = REPO_ROOT / "src" / "stepinbel" / "cli"
PUBLIC_STEPINBEL = {
    "stepinbel",
    "stepinbel.config",
    "stepinbel.data",
    "stepinbel.optimizer",
    "stepinbel.reporting",
    "stepinbel.workflows",
}


def test_cli_public_export_is_exactly_main() -> None:
    assert stepinbel.cli.__all__ == ["main"]
    assert hasattr(stepinbel.cli, "main")
    assert stepinbel.__all__ == ["__version__"]
    assert not hasattr(stepinbel, "main")


def test_cli_imports_only_public_package_names() -> None:
    for path in CLI_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            if not node.module.startswith("stepinbel"):
                continue
            if node.module.startswith("stepinbel.cli"):
                continue
            assert node.module in PUBLIC_STEPINBEL, f"{path} imports {node.module}"


def test_compare_markets_parse_failures(tmp_path: Path) -> None:
    output = tmp_path / "must-not-be-created"
    with pytest.raises(SystemExit) as unknown:
        parse_args(
            [
                "compare",
                "--markets",
                "fcr",
                "--delivery-start",
                "2025-01-15",
                "--delivery-end",
                "2025-01-15",
                "--data-dir",
                "data",
                "--output-dir",
                str(output),
            ]
        )
    assert unknown.value.code == 2
    with pytest.raises(SystemExit) as repeated:
        parse_args(
            [
                "compare",
                "--markets",
                "da",
                "mfrr",
                "--markets",
                "da",
                "afrr",
                "--delivery-start",
                "2025-01-15",
                "--delivery-end",
                "2025-01-15",
                "--data-dir",
                "data",
                "--output-dir",
                str(output),
            ]
        )
    assert repeated.value.code == 2
    assert not output.exists()


def test_parser_command_tree() -> None:
    parser = build_parser()
    commands = parser._subparsers._group_actions[0].choices
    assert set(commands) == {"run", "compare", "sweep", "validate", "data-info"}


def test_delivery_and_utc_periods() -> None:
    delivery = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-16",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    period = build_period(delivery)
    assert isinstance(period, BelgianDeliveryPeriod)
    assert period.start_date.isoformat() == "2025-01-15"
    assert period.end_date_inclusive.isoformat() == "2025-01-16"

    utc_ns = parse_args(
        [
            "run",
            "--market",
            "da",
            "--utc-start",
            "2025-01-15T00:00:00Z",
            "--utc-end",
            "2025-01-15T02:00:00Z",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    utc_period = build_period(utc_ns)
    assert isinstance(utc_period, UtcPeriod)
    assert utc_period.start_utc == utc(2025, 1, 15, 0, 0)
    assert utc_period.end_exclusive_utc == utc(2025, 1, 15, 2, 0)


@pytest.mark.parametrize(
    "extra",
    (
        ["--delivery-start", "2025-01-15"],
        ["--delivery-end", "2025-01-15"],
        ["--utc-start", "2025-01-15T00:00:00Z"],
        ["--utc-end", "2025-01-15T02:00:00Z"],
        [
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--utc-start",
            "2025-01-15T00:00:00Z",
            "--utc-end",
            "2025-01-15T02:00:00Z",
        ],
        ["--utc-start", "2025-01-15T00:00:00", "--utc-end", "2025-01-15T02:00:00Z"],
        ["--utc-start", "2025-01-15T00:00:00+00:00", "--utc-end", "2025-01-15T02:00:00Z"],
    ),
)
def test_invalid_period_pairs_fail(extra: list[str]) -> None:
    namespace = parse_args(["run", "--market", "da", "--data-dir", "data", "--output-dir", "out", *extra])
    with pytest.raises(CliError):
        build_period(namespace)


@pytest.mark.parametrize(
    "flag,value",
    (
        ("--delivery-start", "2025-01-15"),
        ("--delivery-end", "2025-01-15"),
        ("--utc-start", "2025-01-15T00:00:00Z"),
        ("--utc-end", "2025-01-15T02:00:00Z"),
    ),
)
def test_repeated_period_flags_fail_before_construction(flag: str, value: str, tmp_path: Path) -> None:
    output = tmp_path / "must-not-be-created"
    with pytest.raises(SystemExit) as exc_info:
        parse_args(
            [
                "run",
                "--market",
                "da",
                flag,
                value,
                flag,
                value,
                "--data-dir",
                "data",
                "--output-dir",
                str(output),
            ]
        )
    assert exc_info.value.code == 2
    assert not output.exists()


def test_non_quarter_hour_and_reversed_periods_fail() -> None:
    skewed = parse_args(
        [
            "run",
            "--market",
            "da",
            "--utc-start",
            "2025-01-15T00:07:00Z",
            "--utc-end",
            "2025-01-15T02:00:00Z",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    with pytest.raises(Exception):
        build_period(skewed)
    reversed_ns = parse_args(
        [
            "run",
            "--market",
            "da",
            "--utc-start",
            "2025-01-15T02:00:00Z",
            "--utc-end",
            "2025-01-15T00:00:00Z",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    with pytest.raises(Exception):
        build_period(reversed_ns)


def test_omitted_asset_and_site_use_domain_defaults() -> None:
    namespace = parse_args(
        [
            "run",
            "--market",
            "da",
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
    assert build_asset(namespace) == AssetConfig()
    assert build_site(namespace) == SiteConfig()


def test_pond_storage_and_storage_conflict() -> None:
    pond = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--pond-energy-mwh",
            "12.5",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    asset = build_asset(pond)
    assert asset.pond_energy_mwh == 12.5
    assert asset.storage_hours is None
    conflict = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--storage-hours",
            "3",
            "--pond-energy-mwh",
            "12.5",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    with pytest.raises(CliError, match="only one"):
        build_asset(conflict)


def test_terminal_soc_flags_and_pv_combinations() -> None:
    off = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--no-enforce-terminal-soc",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    assert build_asset(off).enforce_terminal_soc is False
    fixed = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--pv-revenue-mode",
            "fixed",
            "--pv-fixed-price-eur-mwh",
            "40",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    site = build_site(fixed)
    assert site.pv_revenue_mode == "fixed"
    assert site.pv_fixed_price_eur_mwh == 40.0
    stray_price = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--pv-fixed-price-eur-mwh",
            "40",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    with pytest.raises(CliError, match="fixed"):
        build_site(stray_price)
    missing_price = parse_args(
        [
            "run",
            "--market",
            "da",
            "--delivery-start",
            "2025-01-15",
            "--delivery-end",
            "2025-01-15",
            "--pv-revenue-mode",
            "fixed",
            "--data-dir",
            "data",
            "--output-dir",
            "out",
        ]
    )
    with pytest.raises(CliError, match="fixed"):
        build_site(missing_price)


def test_da_rejects_balancing_flags() -> None:
    namespace = parse_args(
        [
            "run",
            "--market",
            "da",
            "--activation-profile",
            "balanced",
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
    with pytest.raises(CliError, match="DA"):
        build_market_case(namespace, "da")


def test_sweep_axis_parsing() -> None:
    assert parse_numeric_axis("0.5,1,2", "--powers-mw") == [0.5, 1.0, 2.0]
    assert parse_numeric_axis("2,1", "--powers-mw") == [2.0, 1.0]
    with pytest.raises(CliError, match="empty"):
        parse_numeric_axis("1,,2", "--powers-mw")
    with pytest.raises(CliError, match="boolean"):
        parse_numeric_axis("1,true", "--powers-mw")
    with pytest.raises(CliError, match="finite"):
        parse_numeric_axis("1,nan", "--powers-mw")
    with pytest.raises(CliError, match="finite"):
        parse_numeric_axis("1,inf", "--powers-mw")
    with pytest.raises(CliError, match="duplicate"):
        parse_numeric_axis("1,1", "--powers-mw")
    with pytest.raises(CliError, match="finite"):
        parse_numeric_axis("1,x", "--powers-mw")


def _direct_base() -> list[str]:
    return [
        "--delivery-start",
        "2025-01-15",
        "--delivery-end",
        "2025-01-15",
        "--data-dir",
        "data",
        "--output-dir",
        "out",
    ]


def test_balanced_passive_and_bid_modes() -> None:
    balanced = parse_args(["run", "--market", "mfrr", *_direct_base(), "--activation-profile", "balanced"])
    case = build_market_case(balanced, "mfrr")
    assert isinstance(case, MFRRCase)
    assert case.activation_profile == "balanced"
    assert isinstance(case.capacity_bid, HistoricalQuantileCapacityBid)
    assert case.capacity_bid.quantile == 0.50
    passive = parse_args(["run", "--market", "afrr", *_direct_base(), "--activation-profile", "passive"])
    afrr = build_market_case(passive, "afrr")
    assert isinstance(afrr, AFRRCase)
    assert afrr.activation_profile == "passive"
    assert afrr.up_capacity_fraction == 1.0
    fixed_mfrr = parse_args(
        [
            "run",
            "--market",
            "mfrr",
            *_direct_base(),
            "--capacity-bid-mode",
            "fixed",
            "--fixed-up-capacity-price-eur-mw-h",
            "8",
        ]
    )
    bid = build_market_case(fixed_mfrr, "mfrr").capacity_bid
    assert isinstance(bid, FixedMinimumCapacityBid)
    assert bid.upward_price_eur_mw_h == 8.0
    assert bid.downward_price_eur_mw_h is None


def test_strict_capacity_bid_combinations() -> None:
    historical_price = parse_args(
        [
            "run",
            "--market",
            "mfrr",
            *_direct_base(),
            "--fixed-up-capacity-price-eur-mw-h",
            "8",
        ]
    )
    with pytest.raises(CliError, match="historical"):
        build_market_case(historical_price, "mfrr")
    mfrr_down = parse_args(
        [
            "run",
            "--market",
            "mfrr",
            *_direct_base(),
            "--capacity-bid-mode",
            "fixed",
            "--fixed-up-capacity-price-eur-mw-h",
            "8",
            "--fixed-down-capacity-price-eur-mw-h",
            "3",
        ]
    )
    with pytest.raises(CliError, match="downward"):
        build_market_case(mfrr_down, "mfrr")
    afrr_up_only = parse_args(
        [
            "run",
            "--market",
            "afrr",
            *_direct_base(),
            "--capacity-bid-mode",
            "fixed",
            "--fixed-up-capacity-price-eur-mw-h",
            "8",
        ]
    )
    with pytest.raises(CliError, match="downward"):
        build_market_case(afrr_up_only, "afrr")
    mfrr_fraction = parse_args(
        [
            "run",
            "--market",
            "mfrr",
            *_direct_base(),
            "--afrr-up-capacity-fraction",
            "0.4",
        ]
    )
    with pytest.raises(CliError, match="mFRR"):
        build_market_case(mfrr_fraction, "mfrr")
    split = parse_args(
        [
            "run",
            "--market",
            "afrr",
            *_direct_base(),
            "--afrr-up-capacity-fraction",
            "0.4",
        ]
    )
    assert build_market_case(split, "afrr").up_capacity_fraction == 0.4
    da = parse_args(["run", "--market", "da", *_direct_base()])
    assert isinstance(build_market_case(da, "da"), DayAheadCase)


@pytest.mark.parametrize(
    "args,market,prefix",
    (
        (
            ["run", "--market", "mfrr", *_direct_base(),
             "--capacity-bid-mode", "fixed",
             "--fixed-up-capacity-price-eur-mw-h", "8",
             "--capacity-quantile", "0.4"],
            "mfrr",
            "",
        ),
        (
            ["run", "--market", "afrr", *_direct_base(),
             "--capacity-bid-mode", "fixed",
             "--fixed-up-capacity-price-eur-mw-h", "8",
             "--fixed-down-capacity-price-eur-mw-h", "3",
             "--capacity-quantile", "0.4"],
            "afrr",
            "",
        ),
        (
            ["compare", *_direct_base(),
             "--mfrr-capacity-bid-mode", "fixed",
             "--mfrr-fixed-up-capacity-price-eur-mw-h", "8",
             "--mfrr-capacity-quantile", "0.4"],
            "mfrr",
            "mfrr_",
        ),
        (
            ["compare", *_direct_base(),
             "--afrr-capacity-bid-mode", "fixed",
             "--afrr-fixed-up-capacity-price-eur-mw-h", "8",
             "--afrr-fixed-down-capacity-price-eur-mw-h", "3",
             "--afrr-capacity-quantile", "0.4"],
            "afrr",
            "afrr_",
        ),
        (
            ["sweep", "--market", "mfrr", *_direct_base(),
             "--powers-mw", "1", "--storage-hours-grid", "2",
             "--capacity-bid-mode", "fixed",
             "--fixed-up-capacity-price-eur-mw-h", "8",
             "--capacity-quantile", "0.4"],
            "mfrr",
            "",
        ),
        (
            ["sweep", "--market", "afrr", *_direct_base(),
             "--powers-mw", "1", "--storage-hours-grid", "2",
             "--capacity-bid-mode", "fixed",
             "--fixed-up-capacity-price-eur-mw-h", "8",
             "--fixed-down-capacity-price-eur-mw-h", "3",
             "--capacity-quantile", "0.4"],
            "afrr",
            "",
        ),
    ),
)
def test_fixed_bid_rejects_historical_quantile(args: list[str], market: str, prefix: str) -> None:
    namespace = parse_args(args)
    with pytest.raises(CliError, match="quantile"):
        build_market_case(namespace, market, prefix=prefix)
