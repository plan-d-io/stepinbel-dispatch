"""Public reporting and artifact-validation boundary. Do not import from top-level stepinbel."""

from stepinbel.reporting.artifacts import validate_run_artifacts
from stepinbel.reporting.comparison_artifacts import validate_market_comparison_artifacts
from stepinbel.reporting.comparison_report import render_market_comparison_report
from stepinbel.reporting.constants import (
    ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION,
    MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION,
    RUN_ARTIFACT_SCHEMA_VERSION,
)
from stepinbel.reporting.io import ArtifactError
from stepinbel.reporting.periods import build_period_summaries
from stepinbel.reporting.report import render_run_report
from stepinbel.reporting.sweep_artifacts import validate_asset_sweep_artifacts
from stepinbel.reporting.sweep_report import render_asset_sweep_report

__all__ = [
    "RUN_ARTIFACT_SCHEMA_VERSION",
    "ArtifactError",
    "build_period_summaries",
    "render_run_report",
    "validate_run_artifacts",
    "MARKET_COMPARISON_ARTIFACT_SCHEMA_VERSION",
    "render_market_comparison_report",
    "validate_market_comparison_artifacts",
    "ASSET_SWEEP_ARTIFACT_SCHEMA_VERSION",
    "render_asset_sweep_report",
    "validate_asset_sweep_artifacts",
]
