"""Map public core exceptions to concise Configure/Review messages."""

from __future__ import annotations

from stepinbel.config import ConfigError
from stepinbel.data import DataAccessError, DataBundleError
from stepinbel.reporting import ArtifactError
from stepinbel.workflows import RunRequestError


def user_facing_error(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    first = text.splitlines()[0].strip()
    if isinstance(exc, ConfigError):
        return first
    if isinstance(exc, DataBundleError):
        return "Published market data could not be opened. Check the data directory."
    if isinstance(exc, DataAccessError):
        if "not fully covered" in first:
            return "The selected period is not fully covered for the chosen markets or PV."
        return first
    if isinstance(exc, RunRequestError):
        return first
    if isinstance(exc, ArtifactError):
        return "The stored artifacts are incomplete or incompatible."
    return first
