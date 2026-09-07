"""Deterministic UI test artifact trees. Does not run HiGHS or workers."""

from __future__ import annotations

import csv
import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from stepinbel.reporting import validate_market_comparison_artifacts, validate_run_artifacts
from stepinbel.reporting.artifacts import write_artifact_manifest
from stepinbel.reporting.comparison_artifacts import (
    comparison_rows_from_child_summaries,
    write_comparison_artifact_manifest,
    write_comparison_artifacts,
)
from stepinbel.workflows import (
    build_market_comparison_request,
    load_case_run_request,
    load_market_comparison_request,
    serialize_case_run_request,
    serialize_market_comparison_request,
)
from stepinbel.workflows.serialize import dumps_json

from ui.services.artifacts import result_record
from ui.services.form import default_live_form
from ui.services.jobs import (
    LAUNCH_LAUNCHED,
    atomic_write_json,
    iso_utc,
    job_paths,
    job_record,
    write_job_record,
)
from ui.services.paths import DEMO_COMPARISON_DIR, DEMO_IDENTITY, KIND_CASE, KIND_COMPARISON
from ui.services.snapshot import build_snapshot, snapshot_digest

JSON_SUFFIXES = {".json"}
TEXT_SUFFIXES = {".txt", ".log", ".csv", ".jsonl"}


class _ChildView:
    def __init__(self, directory: Path, request: object) -> None:
        self.directory = directory
        self.request = request


def _planned_job(
    outputs_root: Path,
    snapshot: dict[str, Any],
    job_id: str,
    *,
    kind: str,
    markets: list[str],
) -> dict[str, Any]:
    paths = job_paths(job_id, kind=kind, outputs_root=outputs_root)
    paths["staging_directory"].mkdir(parents=True, exist_ok=True)
    atomic_write_json(paths["configured_snapshot_path"], snapshot)
    atomic_write_json(paths["request_path"], {"run_id": job_id})
    record = job_record(
        job_id=job_id,
        kind=kind,
        markets=markets,
        snapshot_fingerprint=snapshot_digest(snapshot),
        launch_state=LAUNCH_LAUNCHED,
        launch_utc=iso_utc(),
        paths=paths,
        pid=4242,
        outputs_root=outputs_root,
    )
    write_job_record(paths["job_path"], record, outputs_root=outputs_root)
    return record


def live_result_record(record: Mapping[str, Any], snapshot: Mapping[str, Any]) -> dict[str, Any]:
    return result_record(
        source="live",
        kind=str(record["kind"]),
        job_id=str(record["job_id"]),
        output_directory=str(record["output_directory"]),
        markets=list(record["markets"]),
        period=snapshot["period"],
    )


def _replace_run_id_value(payload: object, old: str, new: str) -> object:
    if isinstance(payload, dict):
        replaced: dict[str, object] = {}
        for key, value in payload.items():
            if key == "run_id" and value == old:
                replaced[key] = new
            else:
                replaced[key] = _replace_run_id_value(value, old, new)
        return replaced
    if isinstance(payload, list):
        return [_replace_run_id_value(item, old, new) for item in payload]
    return payload


def _rewrite_identity_files(directory: Path, old_id: str, new_id: str) -> None:
    for path in directory.iterdir():
        if not path.is_file() or path.name == "artifact_manifest.json":
            continue
        suffix = path.suffix.lower()
        if suffix in JSON_SUFFIXES:
            payload = json.loads(path.read_text(encoding="utf-8"))
            path.write_text(
                json.dumps(_replace_run_id_value(payload, old_id, new_id), indent=2, ensure_ascii=False)
                + "\n",
                encoding="utf-8",
            )
            continue
        if path.name == "summary.csv":
            with path.open("r", encoding="utf-8", newline="") as handle:
                rows = list(csv.reader(handle))
            if len(rows) >= 2 and rows[1] and rows[1][0] == old_id:
                rows[1][0] = new_id
            with path.open("w", encoding="utf-8", newline="") as handle:
                csv.writer(handle).writerows(rows)
            continue
        if suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8")
            path.write_text(text.replace(old_id, new_id), encoding="utf-8")


def copy_one_case_tree(source: Path, destination: Path) -> None:
    if destination.exists():
        for child in list(destination.iterdir()):
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        for item in source.iterdir():
            target = destination / item.name
            if item.is_dir():
                shutil.copytree(item, target)
            else:
                shutil.copy2(item, target)
        return
    shutil.copytree(source, destination)


def rebind_one_case_run_id(directory: Path, new_run_id: str) -> None:
    request = load_case_run_request(directory / "run_request.json")
    old_id = request.run_id
    if old_id == new_run_id:
        write_artifact_manifest(directory, new_run_id)
        validate_run_artifacts(directory)
        return
    _rewrite_identity_files(directory, old_id, new_run_id)
    write_artifact_manifest(directory, new_run_id)
    validate_run_artifacts(directory)


def mismatched_one_case_live(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy Demo Day-ahead under a live job ID without rewriting stored run_id."""
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260906T210000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    copy_one_case_tree(DEMO_COMPARISON_DIR / "cases" / "da", Path(record["output_directory"]))
    return record, live_result_record(record, snapshot)


def identified_relocated_one_case_live(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Copy Demo Day-ahead, keep historical request output path, bind run_id to the job."""
    form = default_live_form()
    form["market_mfrr"] = False
    form["market_afrr"] = False
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260906T220000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_CASE, markets=["da"])
    output = Path(record["output_directory"])
    copy_one_case_tree(DEMO_COMPARISON_DIR / "cases" / "da", output)
    rebind_one_case_run_id(output, job_id)
    request = load_case_run_request(output / "run_request.json")
    if Path(request.output_directory).resolve() == output.resolve():
        raise AssertionError("relocated one-case fixture must keep the historical output path")
    if request.run_id != job_id:
        raise AssertionError("relocated one-case fixture must use the live job ID")
    return record, live_result_record(record, snapshot)


def mismatched_two_market_on_three_market_demo(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """Claim da+afrr while pointing at the complete three-market Demo tree."""
    form = default_live_form()
    form["market_mfrr"] = False
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260906T210100Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_COMPARISON, markets=["da", "afrr"])
    shutil.copytree(DEMO_COMPARISON_DIR, Path(record["output_directory"]), dirs_exist_ok=True)
    return record, live_result_record(record, snapshot)


def write_genuine_two_market_comparison(destination: Path, parent_run_id: str) -> None:
    """Assemble da+afrr children only and parent files that the public validator accepts."""
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    cases = destination / "cases"
    selected = ("da", "afrr")
    originals: dict[str, Any] = {}
    for market in selected:
        child_dir = cases / market
        copy_one_case_tree(DEMO_COMPARISON_DIR / "cases" / market, child_dir)
        originals[market] = load_case_run_request(child_dir / "run_request.json")
    first = originals["da"]
    request = build_market_comparison_request(
        {market: originals[market].config for market in selected},
        first.data_directory,
        destination,
        solver_options=first.solver_options,
        run_id=parent_run_id,
        created_at_utc=first.created_at_utc,
    )
    destination.joinpath("comparison_request.json").write_text(
        dumps_json(serialize_market_comparison_request(request)),
        encoding="utf-8",
    )
    case_views: dict[str, _ChildView] = {}
    summaries: dict[str, dict[str, Any]] = {}
    child_ids: dict[str, str] = {}
    for market, child_request in request.case_requests.items():
        child_dir = cases / market
        old_id = originals[market].run_id
        _rewrite_identity_files(child_dir, old_id, child_request.run_id)
        child_dir.joinpath("run_request.json").write_text(
            dumps_json(serialize_case_run_request(child_request)),
            encoding="utf-8",
        )
        write_artifact_manifest(child_dir, child_request.run_id)
        validate_run_artifacts(child_dir)
        case_views[market] = _ChildView(child_dir, child_request)
        summaries[market] = json.loads((child_dir / "summary.json").read_text(encoding="utf-8"))
        child_ids[market] = child_request.run_id
    rows, highest = comparison_rows_from_child_summaries(summaries, child_ids)
    write_comparison_artifacts(destination, request, case_views, rows, highest)
    status = json.loads((DEMO_COMPARISON_DIR / "run_status.json").read_text(encoding="utf-8"))
    status["run_id"] = parent_run_id
    destination.joinpath("run_status.json").write_text(
        json.dumps(status, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    events = (DEMO_COMPARISON_DIR / "run_events.jsonl").read_text(encoding="utf-8")
    destination.joinpath("run_events.jsonl").write_text(
        events.replace("demo-2025-all-markets-pv500", parent_run_id),
        encoding="utf-8",
    )
    log = (DEMO_COMPARISON_DIR / "run.log").read_text(encoding="utf-8")
    destination.joinpath("run.log").write_text(log, encoding="utf-8")
    write_comparison_artifact_manifest(destination, parent_run_id)
    validate_market_comparison_artifacts(destination)
    leftover = {path.name for path in (destination / "cases").iterdir()}
    if leftover != set(selected):
        raise AssertionError("genuine two-market fixture must contain only da and afrr children")


def genuine_two_market_live(tmp_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    form = default_live_form()
    form["market_mfrr"] = False
    snapshot = build_snapshot(form, demo=False)
    job_id = "stepinbel-20260906T230000Z-abcd1234"
    record = _planned_job(tmp_path, snapshot, job_id, kind=KIND_COMPARISON, markets=["da", "afrr"])
    write_genuine_two_market_comparison(Path(record["output_directory"]), job_id)
    return record, live_result_record(record, snapshot)


def relocated_demo_result(tmp_path: Path) -> dict[str, Any]:
    relocated = tmp_path / "relocated-demo"
    shutil.copytree(DEMO_COMPARISON_DIR, relocated)
    request = load_market_comparison_request(relocated / "comparison_request.json")
    first = next(iter(request.case_requests.values())).config
    return result_record(
        source="demo",
        kind=KIND_COMPARISON,
        job_id=DEMO_IDENTITY,
        output_directory=str(relocated.resolve()),
        markets=list(request.case_requests),
        period={
            "start_date": first.period.start_date.isoformat(),
            "end_date": first.period.end_date_inclusive.isoformat(),
        },
    )
