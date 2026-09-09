from __future__ import annotations

import ast
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import MappingProxyType

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stepinbel.config import (
    AFRRCase,
    DayAheadCase,
    MFRRCase,
    SimulationConfig,
    SiteConfig,
    UtcPeriod,
)
from stepinbel.data import (
    DataAccessError,
    PublishedDataBundle,
    PublishedTable,
    load_market_data,
    open_published_bundle,
)
from stepinbel.data.load import (
    BALANCING_COLUMNS,
    CAPACITY_COLUMNS,
    DA_COLUMNS,
    PV_COLUMNS,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src" / "stepinbel"
FORBIDDEN_IMPORT_ROOTS = {
    "pandas",
    "data_pipeline",
    "phs",
    "btm_sim",
    "gurobipy",
    "streamlit",
}
SHORT_PERIOD = UtcPeriod(
    datetime(2025, 1, 15, tzinfo=timezone.utc),
    datetime(2025, 1, 15, 2, tzinfo=timezone.utc),
)


def test_real_da_mfrr_afrr_and_pv_slices(data_root) -> None:
    bundle = open_published_bundle(data_root)
    da = load_market_data(
        bundle, SimulationConfig(period=SHORT_PERIOD, market_case=DayAheadCase())
    )
    mfrr = load_market_data(
        bundle, SimulationConfig(period=SHORT_PERIOD, market_case=MFRRCase())
    )
    afrr = load_market_data(
        bundle, SimulationConfig(period=SHORT_PERIOD, market_case=AFRRCase())
    )
    pv = load_market_data(
        bundle,
        SimulationConfig(
            period=SHORT_PERIOD,
            market_case=DayAheadCase(),
            site=SiteConfig(pv_ac_kw=500.0, pv_region="Belgium"),
        ),
    )
    for slice_, expected_da in (
        (da, 8),
        (mfrr, 8),
        (afrr, 8),
        (pv, 8),
    ):
        assert slice_.da_prices.num_rows == expected_da
        assert tuple(slice_.da_prices.column_names) == DA_COLUMNS
        assert slice_.manifest_sha256 == bundle.manifest_sha256
        assert slice_.period.window.interval_count == 8

    assert da.balancing is None
    assert da.capacity_blocks is None
    assert da.pv_profile is None
    assert pv.pv_profile is not None
    assert tuple(pv.pv_profile.column_names) == PV_COLUMNS
    assert pv.pv_profile.num_rows == 8
    assert set(pv.pv_profile.column("region").to_pylist()) == {"Belgium"}

    assert mfrr.balancing is not None
    assert afrr.balancing is not None
    assert tuple(mfrr.balancing.column_names) == BALANCING_COLUMNS
    assert mfrr.balancing.num_rows == 8
    assert afrr.balancing.num_rows == 8
    _assert_qh_grid(mfrr.balancing, SHORT_PERIOD)
    _assert_qh_grid(da.da_prices, SHORT_PERIOD)

    assert mfrr.capacity_blocks is not None
    assert afrr.capacity_blocks is not None
    assert tuple(mfrr.capacity_blocks.column_names) == CAPACITY_COLUMNS
    assert set(mfrr.capacity_blocks.column("product").to_pylist()) == {"mfrr"}
    assert set(afrr.capacity_blocks.column("product").to_pylist()) == {"afrr"}
    starts = mfrr.capacity_blocks.column("block_start_utc").to_pylist()
    assert starts == sorted(starts)
    assert any(start < SHORT_PERIOD.start_utc for start in starts)


def test_load_is_independent_of_cwd(data_root, tmp_path) -> None:
    script = (
        "from pathlib import Path\n"
        "import sys\n"
        "from datetime import datetime, timezone\n"
        "from stepinbel.config import DayAheadCase, SimulationConfig, UtcPeriod\n"
        "from stepinbel.data import load_market_data, open_published_bundle\n"
        "bundle = open_published_bundle(Path(sys.argv[1]))\n"
        "period = UtcPeriod(datetime(2025,1,15,tzinfo=timezone.utc), datetime(2025,1,15,1,tzinfo=timezone.utc))\n"
        "slice_ = load_market_data(bundle, SimulationConfig(period=period, market_case=DayAheadCase()))\n"
        "assert slice_.da_prices.num_rows == 4\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(data_root.resolve())],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_synthetic_overlapping_capacity_and_unavailable_rows(tmp_path) -> None:
    window = UtcPeriod(
        datetime(2025, 1, 15, tzinfo=timezone.utc),
        datetime(2025, 1, 15, 1, tzinfo=timezone.utc),
    )
    bundle = _synthetic_bundle(tmp_path, qh_count=4, extra_capacity=True)
    slice_ = load_market_data(
        bundle, SimulationConfig(period=window, market_case=MFRRCase())
    )
    assert slice_.capacity_blocks is not None
    starts = slice_.capacity_blocks.column("block_start_utc").to_pylist()
    products = slice_.capacity_blocks.column("product").to_pylist()
    available = slice_.capacity_blocks.column("data_available").to_pylist()
    assert "afrr" not in products
    assert any(start < window.start_utc for start in starts)
    assert False in available
    directions = slice_.capacity_blocks.column("direction").to_pylist()
    steps = slice_.capacity_blocks.column("auction_step").to_pylist()
    ordered = list(zip(starts, directions, steps, strict=True))
    assert ordered == sorted(ordered)


@pytest.mark.parametrize("mode", ["duplicate", "missing", "misaligned", "out_of_window"])
def test_quarter_hour_grid_errors(tmp_path, mode: str) -> None:
    start = datetime(2025, 1, 15, tzinfo=timezone.utc)
    stamps = [start + timedelta(minutes=15 * i) for i in range(4)]
    if mode == "duplicate":
        stamps[2] = stamps[1]
    elif mode == "missing":
        stamps = stamps[:3]
    elif mode == "misaligned":
        stamps[1] = start + timedelta(minutes=16)
    elif mode == "out_of_window":
        stamps[0] = start - timedelta(minutes=15)
    bundle = _synthetic_bundle(tmp_path, timestamps=stamps)
    period = UtcPeriod(start, start + timedelta(hours=1))
    with pytest.raises(DataAccessError, match="da_prices"):
        load_market_data(
            bundle, SimulationConfig(period=period, market_case=DayAheadCase())
        )


def test_production_code_has_no_forbidden_imports() -> None:
    for path in SRC_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.append(node.module)
            for name in names:
                assert name.split(".", 1)[0] not in FORBIDDEN_IMPORT_ROOTS, path


def test_import_stepinbel_does_not_import_pyarrow_or_highspy() -> None:
    script = (
        "import sys\n"
        "import stepinbel\n"
        "assert 'pyarrow' not in sys.modules\n"
        "assert 'highspy' not in sys.modules\n"
        "assert stepinbel.__version__ == '0.2.0'\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script], check=False, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr


def _assert_qh_grid(table: pa.Table, period: UtcPeriod) -> None:
    values = table.column("datetime_utc").to_pylist()
    expected = []
    instant = period.start_utc
    while instant < period.end_exclusive_utc:
        expected.append(instant)
        instant += timedelta(minutes=15)
    assert values == expected


def _synthetic_bundle(
    root: Path,
    *,
    qh_count: int = 4,
    timestamps: list[datetime] | None = None,
    extra_capacity: bool = False,
) -> PublishedDataBundle:
    start = datetime(2025, 1, 15, tzinfo=timezone.utc)
    stamps = timestamps or [start + timedelta(minutes=15 * i) for i in range(qh_count)]
    da_path = root / "da_prices_qh.parquet"
    balancing_path = root / "balancing_qh.parquet"
    capacity_path = root / "capacity_blocks.parquet"
    pv_path = root / "pv_profile_qh.parquet"
    _write_da(da_path, stamps)
    _write_balancing(balancing_path, stamps)
    _write_capacity(capacity_path, extra=extra_capacity)
    _write_pv(pv_path, stamps)
    coverage = MappingProxyType(
        {
            "da_prices_qh": MappingProxyType(
                {"coverage_utc": ("2025-01-01T00:00:00Z", "2025-12-31T23:45:00Z")}
            ),
            "balancing_qh": MappingProxyType(
                {"coverage_utc": ("2025-01-01T00:00:00Z", "2025-12-31T23:45:00Z")}
            ),
            "capacity_blocks": MappingProxyType(
                {
                    "coverage_by_product": MappingProxyType(
                        {
                            "mfrr": ("2025-01-01", "2025-12-31"),
                            "afrr": ("2025-01-01", "2025-12-31"),
                        }
                    )
                }
            ),
            "pv_profile_qh": MappingProxyType(
                {
                    "coverage_utc": ("2025-01-01T00:00:00Z", "2025-12-31T23:45:00Z"),
                    "regions": ("Belgium",),
                }
            ),
        }
    )
    tables = MappingProxyType(
        {
            "da_prices_qh": _meta("da_prices_qh", da_path),
            "balancing_qh": _meta("balancing_qh", balancing_path),
            "capacity_blocks": _meta("capacity_blocks", capacity_path),
            "capacity_bids": _meta("capacity_bids", root / "capacity_bids.parquet"),
            "pv_profile_qh": _meta("pv_profile_qh", pv_path),
        }
    )
    return PublishedDataBundle(
        root=root,
        manifest_path=root / "MANIFEST.json",
        manifest_sha256="00" * 32,
        pipeline_version="1.0.0",
        built_at_utc="2026-08-14T17:18:21Z",
        git_commit="test",
        partial_build=False,
        tables=tables,
        coverage=coverage,
    )


def _meta(stem: str, path: Path) -> PublishedTable:
    return PublishedTable(
        stem=stem,
        path=path,
        expected_sha256="00" * 32,
        actual_sha256="00" * 32,
        manifest_row_count=1,
        parquet_row_count=1,
        column_names=(),
    )


def _write_da(path: Path, stamps: list[datetime]) -> None:
    pq.write_table(
        pa.table(
            {
                "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
                "da_price_eur_mwh": pa.array([1.0] * len(stamps)),
                "native_resolution": pa.array(["qh"] * len(stamps)),
                "upsampled_from_hourly": pa.array([False] * len(stamps)),
            }
        ),
        path,
    )


def _write_balancing(path: Path, stamps: list[datetime]) -> None:
    n = len(stamps)
    pq.write_table(
        pa.table(
            {
                "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
                "has_afrr_up": pa.array([False] * n),
                "has_afrr_down": pa.array([False] * n),
                "has_mfrr_up": pa.array([False] * n),
                "has_mfrr_down": pa.array([False] * n),
                "cbmp_afrr_up": pa.array([0.0] * n),
                "cbmp_afrr_down": pa.array([0.0] * n),
                "cbmp_mfrr_up": pa.array([0.0] * n),
                "cbmp_mfrr_down": pa.array([0.0] * n),
                "afrr_price_up_eur_mwh": pa.array([0.0] * n),
                "afrr_price_down_eur_mwh": pa.array([0.0] * n),
            }
        ),
        path,
    )


def _write_pv(path: Path, stamps: list[datetime]) -> None:
    n = len(stamps)
    pq.write_table(
        pa.table(
            {
                "datetime_utc": pa.array(stamps, type=pa.timestamp("us", tz="UTC")),
                "region": pa.array(["Belgium"] * n),
                "measured_mw": pa.array([0.0] * n),
                "monitored_capacity_mw": pa.array([1.0] * n),
                "load_factor": pa.array([0.0] * n),
            }
        ),
        path,
    )


def _write_capacity(path: Path, *, extra: bool) -> None:
    start = datetime(2025, 1, 15, tzinfo=timezone.utc)
    rows = [
        {
            "delivery_date_local": date(2025, 1, 15),
            "block": "00-04",
            "product": "mfrr",
            "direction": "up",
            "auction_step": "step1",
            "block_start_utc": start - timedelta(hours=1),
            "block_end_utc": start + timedelta(hours=3),
            "block_hours": 4.0,
            "data_available": False,
            "awarded_volume_mw": 0.0,
            "marginal_price_eur_mw_h": 0.0,
            "vwap_price_eur_mw_h": 0.0,
        },
        {
            "delivery_date_local": date(2025, 1, 15),
            "block": "04-08",
            "product": "mfrr",
            "direction": "down",
            "auction_step": "step2",
            "block_start_utc": start + timedelta(hours=1),
            "block_end_utc": start + timedelta(hours=2),
            "block_hours": 4.0,
            "data_available": True,
            "awarded_volume_mw": 1.0,
            "marginal_price_eur_mw_h": 1.0,
            "vwap_price_eur_mw_h": 1.0,
        },
    ]
    if extra:
        rows.append(
            {
                "delivery_date_local": date(2025, 1, 15),
                "block": "00-04",
                "product": "afrr",
                "direction": "up",
                "auction_step": "step1",
                "block_start_utc": start,
                "block_end_utc": start + timedelta(hours=4),
                "block_hours": 4.0,
                "data_available": True,
                "awarded_volume_mw": 1.0,
                "marginal_price_eur_mw_h": 1.0,
                "vwap_price_eur_mw_h": 1.0,
            }
        )
        rows.append(
            {
                "delivery_date_local": date(2025, 1, 14),
                "block": "20-24",
                "product": "mfrr",
                "direction": "up",
                "auction_step": "step1",
                "block_start_utc": start - timedelta(hours=5),
                "block_end_utc": start - timedelta(hours=1),
                "block_hours": 4.0,
                "data_available": True,
                "awarded_volume_mw": 1.0,
                "marginal_price_eur_mw_h": 1.0,
                "vwap_price_eur_mw_h": 1.0,
            }
        )
    table = pa.table(
        {
            "delivery_date_local": pa.array(
                [row["delivery_date_local"] for row in rows], type=pa.date32()
            ),
            "block": pa.array([row["block"] for row in rows], type=pa.dictionary(pa.int8(), pa.string())),
            "product": pa.array(
                [row["product"] for row in rows], type=pa.dictionary(pa.int8(), pa.string())
            ),
            "direction": pa.array(
                [row["direction"] for row in rows], type=pa.dictionary(pa.int8(), pa.string())
            ),
            "auction_step": pa.array(
                [row["auction_step"] for row in rows],
                type=pa.dictionary(pa.int8(), pa.string()),
            ),
            "block_start_utc": pa.array(
                [row["block_start_utc"] for row in rows], type=pa.timestamp("us", tz="UTC")
            ),
            "block_end_utc": pa.array(
                [row["block_end_utc"] for row in rows], type=pa.timestamp("us", tz="UTC")
            ),
            "block_hours": pa.array([row["block_hours"] for row in rows]),
            "data_available": pa.array([row["data_available"] for row in rows]),
            "awarded_volume_mw": pa.array([row["awarded_volume_mw"] for row in rows]),
            "marginal_price_eur_mw_h": pa.array(
                [row["marginal_price_eur_mw_h"] for row in rows]
            ),
            "vwap_price_eur_mw_h": pa.array([row["vwap_price_eur_mw_h"] for row in rows]),
        }
    )
    pq.write_table(table, path)
