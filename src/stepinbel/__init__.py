"""StepInBel package: versioned foundation for a later PHS-to-HiGHS port."""

from __future__ import annotations

from importlib.resources import files

__version__ = files(__package__).joinpath("VERSION").read_text(encoding="utf-8").strip()

__all__ = ["__version__"]
