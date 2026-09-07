"""Public CLI entry. Argparse SystemExit is not caught."""

from __future__ import annotations

from collections.abc import Sequence

from stepinbel.cli.commands import dispatch
from stepinbel.cli.output import configure_stdio, emit_failure
from stepinbel.cli.parser import parse_args


def main(args: Sequence[str] | None = None) -> int:
    configure_stdio()
    try:
        namespace = parse_args(args)
        return dispatch(namespace)
    except SystemExit:
        raise
    except KeyboardInterrupt as exc:
        return emit_failure(exc)
    except Exception as exc:
        return emit_failure(exc)
