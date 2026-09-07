from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def data_root(repo_root: Path) -> Path:
    return repo_root / "data"


@pytest.fixture(scope="session")
def reference_root(repo_root: Path) -> Path:
    return repo_root / "reference" / "phs_mvp_0_1_0"
