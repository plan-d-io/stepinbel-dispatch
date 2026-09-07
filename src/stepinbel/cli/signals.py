"""Cooperative SIGINT handling. Does not interrupt HiGHS."""

from __future__ import annotations

import signal
from types import FrameType
from typing import Callable


class CancellationFlag:
    """Side-effect-free cancellation latch for workflow checkpoints."""

    def __init__(self) -> None:
        self._requested = False

    def request(self) -> None:
        self._requested = True

    def __call__(self) -> bool:
        return self._requested


def install_sigint(flag: CancellationFlag) -> object | None:
    if not hasattr(signal, "SIGINT"):
        return None

    def handler(_signum: int, _frame: FrameType | None) -> None:
        flag.request()

    try:
        previous = signal.getsignal(signal.SIGINT)
        signal.signal(signal.SIGINT, handler)
    except (OSError, ValueError, AttributeError):
        return None
    return previous


def restore_sigint(previous: object | None) -> None:
    if previous is None or not hasattr(signal, "SIGINT"):
        return
    try:
        signal.signal(signal.SIGINT, previous)  # type: ignore[arg-type]
    except (OSError, ValueError, AttributeError):
        return


def run_with_cancellation(body: Callable[[Callable[[], bool]], int]) -> int:
    flag = CancellationFlag()
    previous = install_sigint(flag)
    try:
        return body(flag)
    finally:
        restore_sigint(previous)
