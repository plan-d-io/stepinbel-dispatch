from __future__ import annotations

import hashlib
import json
from pathlib import Path

EXPECTED_METADATA_HASHES = {
    "da_balanced_2025_utc_no_pv": "eaedb6683260c9f8e3043b4e4b6e6c21091de15270b78fe2bd7ec43e712bfe95",
    "mfrr_balanced_2025_delivery_no_pv": "cbafd7d0134a165cc59d9fa99498373fbbc1ea247b33f399c48400518fecf48c",
    "afrr_balanced_2025_delivery_no_pv": "55b03cc53b63f5a0308c8782a075df79246c3c7408e3bed2fdfd236906b6be74",
    "da_balanced_2025_delivery_pv500": "3dcb7982aa9a0719078fb6ebfca9d165a817733d4cb3c73ebebab568af1ec69f",
}

EXPECTED_SOURCE_PATHS = {
    "da_balanced_2025_utc_no_pv": "outputs/headline_2025/da_balanced/da_yearly_balanced_1MW_4h.meta.json",
    "mfrr_balanced_2025_delivery_no_pv": "outputs/headline_2025/mfrr_balanced/mfrr_yearly_balanced_1MW_4h.meta.json",
    "afrr_balanced_2025_delivery_no_pv": "outputs/headline_2025/afrr_balanced/afrr_yearly_balanced_1MW_4h.meta.json",
    "da_balanced_2025_delivery_pv500": "outputs/ui/20260902-115027/da/da_yearly_balanced_1MW_4h.meta.json",
}

EXPECTED_WINDOWS = {
    "da_balanced_2025_utc_no_pv": ("2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    "mfrr_balanced_2025_delivery_no_pv": ("2024-12-31T23:00:00Z", "2025-12-31T23:00:00Z"),
    "afrr_balanced_2025_delivery_no_pv": ("2024-12-31T23:00:00Z", "2025-12-31T23:00:00Z"),
    "da_balanced_2025_delivery_pv500": ("2024-12-31T23:00:00Z", "2025-12-31T23:00:00Z"),
}

EXPECTED_METRICS = {
    "da_balanced_2025_utc_no_pv": {
        "total_eur": 115496.80517869153,
        "energy_net_eur": 115496.80517869153,
        "capacity_eur": 0.0,
        "pv_eur": 0.0,
    },
    "mfrr_balanced_2025_delivery_no_pv": {
        "total_eur": 94874.97865237418,
        "energy_net_eur": 59359.317526235245,
        "capacity_eur": 35515.66112613893,
        "pv_eur": 0.0,
    },
    "afrr_balanced_2025_delivery_no_pv": {
        "total_eur": 233306.71125765995,
        "energy_net_eur": 168238.7530225783,
        "capacity_eur": 65067.95823508165,
        "pv_eur": 0.0,
    },
    "da_balanced_2025_delivery_pv500": {
        "total_eur": 136405.7175238604,
        "energy_net_eur": 122484.08696261534,
        "capacity_eur": 0.0,
        "pv_eur": 13921.630561245065,
    },
}

AUTHORIZED_CASE_IDS = (
    "da_balanced_2025_utc_no_pv",
    "mfrr_balanced_2025_delivery_no_pv",
    "afrr_balanced_2025_delivery_no_pv",
    "da_balanced_2025_delivery_pv500",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_parity_index_matches_copied_metadata(reference_root: Path) -> None:
    index = json.loads((reference_root / "index.json").read_text(encoding="utf-8"))
    assert index["schema_version"] == "1.0.0"
    assert index["source"]["tag"] == "phs-mvp-0.1.0"
    assert index["source"]["commit"] == "82691dad676f85bfd345670fb8022de079ca5578"
    assert (
        index["published_data"]["manifest_sha256"]
        == "e13a1ab320201babf11aad8d047cb58079efa9e644a1a1b77fff16527a0a94a6"
    )
    assert index["published_data"]["data_vintage"] == "2026-08-14T17:18:21Z"
    assert "PHS-baseline parity evidence" in index["statement"]
    assert "forecasts" in index["statement"]
    assert "Watts.Happening" in index["statement"]

    cases = index["cases"]
    assert len(cases) == 4
    assert [case["case_id"] for case in cases] == list(AUTHORIZED_CASE_IDS)

    for case in cases:
        case_id = case["case_id"]
        path = reference_root / case["filename"]
        digest = _sha256(path)
        assert digest == EXPECTED_METADATA_HASHES[case_id]
        assert case["source_metadata_sha256"] == digest
        assert case["source_relative_path"] == EXPECTED_SOURCE_PATHS[case_id]
        assert (
            case["window"]["start_utc"],
            case["window"]["end_exclusive_utc"],
        ) == EXPECTED_WINDOWS[case_id]
        assert case["interval_count"] == 35040
        assert case["metrics"] == EXPECTED_METRICS[case_id]

        metadata = json.loads(path.read_text(encoding="utf-8"))
        summary = metadata["summary"]
        assert case["market"] == metadata["market"]
        assert case["dispatch_mode"] == metadata["dispatch_mode"]
        assert case["pv_capacity_kw"] == metadata["config"]["site"]["pv_ac_kw"]
        assert case["interval_count"] == metadata["window"]["n_timesteps"]
        assert case["metrics"]["total_eur"] == summary["total_revenue_eur"]
        assert case["metrics"]["energy_net_eur"] == summary["energy_net_eur"]
        assert case["metrics"]["capacity_eur"] == summary["capacity_revenue_eur"]
        assert case["metrics"]["pv_eur"] == summary["pv_revenue_eur"]
