from __future__ import annotations

from stepinbel.reporting import render_asset_sweep_report
from stepinbel.reporting.constants import ASSET_SWEEP_HIGHEST_WORDING, ASSET_SWEEP_INTERPRETATION
from stepinbel.workflows import build_asset_sweep_request, execute_asset_sweep
from tests.workflow_helpers import afrr_config, da_config, mfrr_config, two_asset_candidates, utc


def test_da_report_contains_required_statements(data_root, tmp_path) -> None:
    request = build_asset_sweep_request(
        da_config(),
        two_asset_candidates(),
        data_root,
        tmp_path / "da",
        run_id="sweep-rep-da",
        created_at_utc=utc(2026, 1, 1, 12, 0),
    )
    run = execute_asset_sweep(request)
    text = (run.directory / "report.txt").read_text(encoding="utf-8")
    rebuilt = render_asset_sweep_report(
        run.request,
        run.rows,
        highest_revenue_candidate_id=run.highest_revenue_candidate_id,
        resolved_start_utc=run.request.case_requests["small"].config.period.to_utc_bounds()[0],
        resolved_end_exclusive_utc=run.request.case_requests["small"].config.period.to_utc_bounds()[1],
        solver_name=run.case_runs["small"].result.solver.solver_name,
        solver_version=run.case_runs["small"].result.solver.solver_version,
    )
    assert text == rebuilt
    assert f"Sweep run ID: {request.run_id}" in text
    assert "Dedicated market: da" in text
    assert "Candidate count: 2" in text
    assert "Import limit mode:" in text
    assert ASSET_SWEEP_HIGHEST_WORDING in text
    assert ASSET_SWEEP_INTERPRETATION in text
    assert "not an investment recommendation" in text
    assert "not additive" in text
    assert "FCR" not in text
    assert "Watts.Happening-conformance claims" in text
    assert "HiGHS" in text
    assert "Day-ahead has no applicable Elia conformance reference" in text


def test_balancing_reports_include_elia_reference(data_root, tmp_path) -> None:
    mfrr = execute_asset_sweep(
        build_asset_sweep_request(
            mfrr_config(),
            two_asset_candidates(),
            data_root,
            tmp_path / "mfrr",
            run_id="sweep-rep-mfrr",
            created_at_utc=utc(2026, 1, 1, 12, 0),
        )
    )
    text = (mfrr.directory / "report.txt").read_text(encoding="utf-8")
    assert "mFRR_260925.pdf" in text
    afrr = execute_asset_sweep(
        build_asset_sweep_request(
            afrr_config(),
            two_asset_candidates(),
            data_root,
            tmp_path / "afrr",
            run_id="sweep-rep-afrr",
            created_at_utc=utc(2026, 1, 1, 12, 0),
        )
    )
    text = (afrr.directory / "report.txt").read_text(encoding="utf-8")
    assert "aFRR_260925.pdf" in text
    assert "not an investment recommendation" in text
