"""Repository paths used by UI services. Never stored in session state."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIRECTORY = REPO_ROOT / "src"
DATA_DIRECTORY = REPO_ROOT / "data"
OUTPUTS_DIRECTORY = REPO_ROOT / "outputs"
UI_JOBS_DIRNAME = ".ui-jobs"
DEMO_COMPARISON_DIR = (
    REPO_ROOT / "ui" / "demo_artifacts" / "stepinbel_2025_all_markets_pv500"
)
DEMO_COMPARISON_REQUEST = DEMO_COMPARISON_DIR / "comparison_request.json"
DEMO_IDENTITY = "demo-2025-all-markets-pv500"

KIND_CASE = "case"
KIND_COMPARISON = "comparison"
JOB_QUERY_KEY = "job"
JOB_JSON_NAME = "job.json"
SNAPSHOT_JSON_NAME = "configured_snapshot.json"
CASE_REQUEST_NAME = "case_request.json"
COMPARISON_REQUEST_NAME = "comparison_request.json"
WORKER_CONSOLE_NAME = "worker_console.log"
STATUS_FILENAME = "run_status.json"
EVENTS_FILENAME = "run_events.jsonl"
LOG_FILENAME = "run.log"

CANONICAL_MARKETS: tuple[str, ...] = ("da", "mfrr", "afrr")
MARKET_LABELS = {
    "da": "Day-ahead",
    "mfrr": "mFRR",
    "afrr": "aFRR",
}


def default_outputs_root() -> Path:
    return OUTPUTS_DIRECTORY


def default_staging_root(outputs_root: Path | None = None) -> Path:
    root = Path(outputs_root) if outputs_root is not None else default_outputs_root()
    return root / UI_JOBS_DIRNAME


def request_filename(kind: str) -> str:
    if kind == KIND_CASE:
        return CASE_REQUEST_NAME
    if kind == KIND_COMPARISON:
        return COMPARISON_REQUEST_NAME
    raise ValueError("unsupported job kind")
