"""Schema versions and accepted behavioural baseline for one-case runs."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping

from stepinbel.reporting.constants import (
    REQUIRED_ARTIFACTS,
    RUN_ARTIFACT_SCHEMA_VERSION,
    RUN_ARTIFACT_SCHEMA_VERSION_V2,
    RUN_ARTIFACT_SCHEMA_VERSION_V3,
)

CASE_RUN_REQUEST_SCHEMA_VERSION = 1
CASE_RUN_REQUEST_SCHEMA_VERSION_V2 = 2
CASE_RUN_REQUEST_SCHEMA_VERSION_V3 = 3
MARKET_COMPARISON_REQUEST_SCHEMA_VERSION = 1
MARKET_COMPARISON_REQUEST_SCHEMA_VERSION_V2 = 2
MARKET_COMPARISON_REQUEST_SCHEMA_VERSION_V3 = 3
ASSET_SWEEP_REQUEST_SCHEMA_VERSION = 1
ASSET_SWEEP_REQUEST_SCHEMA_VERSION_V2 = 2
ASSET_SWEEP_REQUEST_SCHEMA_VERSION_V3 = 3
RUN_STATUS_SCHEMA_VERSION = 1
RUN_EVENT_SCHEMA_VERSION = 1
SUPPORTED_CASE_SCHEMA_VERSIONS = frozenset({1, 2, 3})
SUPPORTED_COMPARISON_SCHEMA_VERSIONS = frozenset({1, 2, 3})
SUPPORTED_SWEEP_SCHEMA_VERSIONS = frozenset({1, 2, 3})
MAX_ASSET_SWEEP_CANDIDATES = 24

BEHAVIOURAL_BASELINE: Mapping[str, str] = MappingProxyType(
    {
        "project": "PHS",
        "tag": "phs-mvp-0.1.0",
        "commit": "82691dad676f85bfd345670fb8022de079ca5578",
    }
)

ELIA_METHODOLOGY: Mapping[str, Mapping[str, str]] = MappingProxyType(
    {
        "mfrr": MappingProxyType(
            {
                "filename": "mFRR_260925.pdf",
                "sha256": "AEEDCB9480E224ADA0879FF0761F847BAA65141086857594CD79A6CED14E6165",
                "statement": (
                    "Elia Watts.Happening methodology reference, not an exact "
                    "conformance claim."
                ),
            }
        ),
        "afrr": MappingProxyType(
            {
                "filename": "aFRR_260925.pdf",
                "sha256": "3481AC341B491417C7E355F159CBF19873403AF27C68618F3BF36B954A0AD9E9",
                "statement": (
                    "Elia Watts.Happening methodology reference, not an exact "
                    "conformance claim."
                ),
            }
        ),
    }
)

STAGES: tuple[str, ...] = (
    "validate_request",
    "validate_data",
    "load_data",
    "solve",
    "write_artifacts",
    "verify_artifacts",
)

COMPARISON_MARKETS: tuple[str, ...] = ("da", "mfrr", "afrr")


def comparison_stages(markets: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Parent stage sequence for the selected dedicated markets, in canonical order."""
    selected = tuple(market for market in COMPARISON_MARKETS if market in set(markets))
    return (
        "validate_request",
        "validate_data",
        *(f"execute_{market}" for market in selected),
        "aggregate",
        "verify_artifacts",
    )


COMPARISON_STAGES: tuple[str, ...] = comparison_stages(COMPARISON_MARKETS)
