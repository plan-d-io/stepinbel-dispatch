"""Check a Windows source installation without running an optimization."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA_DIRECTORY = ROOT / "data"
DEMO_DIRECTORY = ROOT / "ui" / "demo_artifacts" / "stepinbel_2025_all_markets_pv500"
DEMO_IDENTITY = "demo-2025-all-markets-pv500"
APP_PATH = ROOT / "ui" / "app.py"


def _package_file(module) -> Path:
    return Path(module.__file__).resolve()


def main() -> int:
    failures: list[str] = []

    if sys.version_info[:2] != (3, 13):
        failures.append(f"Python 3.13 is required; found {sys.version.split()[0]}")
    if struct.calcsize("P") * 8 != 64:
        failures.append("64-bit Python is required")

    try:
        import highspy
        import plotly
        import pyarrow
        import stepinbel
        import streamlit
        from ui.version import UI_VERSION

        print(f"StepInBel: {stepinbel.__version__}")
        print(f"Front end: {UI_VERSION}")
        print(f"Streamlit: {streamlit.__version__}")
        print(f"PyArrow: {pyarrow.__version__}")
        print(f"Plotly: {plotly.__version__}")
        print(f"HiGHS: {importlib.metadata.version('highspy')}")
        _ = highspy
    except Exception as exc:  # pragma: no cover - exercised by installation failures
        failures.append(f"Required package import failed: {exc}")
        print("\nInstallation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    venv_root = (ROOT / ".venv").resolve()
    package_path = _package_file(stepinbel)
    if venv_root.is_dir() and venv_root not in package_path.parents:
        failures.append(
            "stepinbel must import from this tree's .venv, not another Python environment"
        )
    else:
        print(f"Installed package: {package_path}")

    if importlib.util.find_spec("gurobipy") is None:
        print("Gurobi: not installed (not required)")
    else:
        print("Gurobi: installed separately (not used)")

    try:
        from stepinbel.data import open_published_bundle

        bundle = open_published_bundle(DATA_DIRECTORY)
        print(f"Published data: {bundle.root}")
        print(f"Manifest SHA-256: {bundle.manifest_sha256}")
    except Exception as exc:
        failures.append(f"Published market-data check failed: {exc}")

    try:
        from stepinbel.reporting import validate_market_comparison_artifacts
        from stepinbel.workflows import load_market_comparison_request

        mapping = validate_market_comparison_artifacts(DEMO_DIRECTORY)
        if not mapping:
            raise RuntimeError("demonstration artifact mapping is empty")
        request = load_market_comparison_request(DEMO_DIRECTORY / "comparison_request.json")
        if request.run_id != DEMO_IDENTITY:
            raise RuntimeError("demonstration identity does not match the committed Demo")
        print(f"Saved demonstration: {request.run_id}")
    except Exception as exc:
        failures.append(f"Saved demonstration check failed: {exc}")

    if not APP_PATH.is_file():
        failures.append("The Streamlit entry point is missing: ui/app.py")

    if failures:
        print("\nInstallation check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("Installation check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
