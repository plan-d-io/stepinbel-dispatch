"""Display formatting for stored result values. No artifact I/O."""

from __future__ import annotations

import math

from collections.abc import Sequence

from ui.services.paths import MARKET_LABELS

RESULTS_DISPLAY_MARKETS: tuple[str, ...] = ("da", "afrr", "mfrr")
NET_ENERGY_REVENUE_LABEL = "Net energy revenue"

BALANCING_LABELS = {
    "balanced": "Balanced",
    "passive": "Passive",
}


def is_finite_number(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value))


def require_finite(value: object) -> float:
    if not is_finite_number(value):
        raise ValueError("number")
    return float(value)


def require_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        if is_finite_number(value) and float(value).is_integer():
            return int(value)
        raise ValueError("integer")
    return int(value)


def market_label(market: str) -> str:
    return MARKET_LABELS.get(market, market)


def display_market_keys(markets: Sequence[str]) -> list[str]:
    wanted = set(markets)
    return [item for item in RESULTS_DISPLAY_MARKETS if item in wanted]


def default_explorer_market(markets: Sequence[str]) -> str:
    keys = display_market_keys(markets)
    if not keys:
        raise ValueError("markets")
    return keys[0]


def markets_label(markets: Sequence[str]) -> str:
    return " · ".join(market_label(item) for item in display_market_keys(markets))


def format_eur(value: object) -> str:
    number = require_finite(value)
    sign = "-" if number < 0 else ""
    return f"EUR {sign}{abs(number):,.2f}"


def format_eur_amount(value: object) -> str:
    number = require_finite(value)
    sign = "-" if number < 0 else ""
    return f"{sign}{abs(number):,.2f}"


def format_below_highest(value: object) -> str:
    return f"{format_eur(abs(require_finite(value)))} below highest"


def format_mw(value: object) -> str:
    return f"{require_finite(value):,.3f} MW"


def format_mwh(value: object) -> str:
    return f"{require_finite(value):,.3f} MWh"


def format_hours(value: object) -> str:
    return f"{require_finite(value):,.3f} h"


def format_cycles(value: object) -> str:
    return f"{require_finite(value):,.2f}"


def format_count(value: object) -> str:
    return f"{require_int(value):,}"


def format_bid(value: object) -> str:
    return f"{require_finite(value):,.2f}"


def format_pv_capacity(pv_ac_kw: object) -> str:
    number = require_finite(pv_ac_kw)
    if number <= 0:
        return "Off"
    if number >= 1000:
        return format_mw(number / 1000.0)
    if number.is_integer():
        return f"{int(number):,} kW"
    return f"{number:,.3f} kW"


def format_pump_turbine(pump_mw: object, turbine_mw: object) -> str:
    return f"{require_finite(pump_mw):,.3f} / {require_finite(turbine_mw):,.3f} MW"


def format_grid(import_mw: object, export_mw: object) -> str:
    return (
        f"{require_finite(import_mw):,.3f} MW import / "
        f"{require_finite(export_mw):,.3f} MW export"
    )


def format_storage(*, storage_hours: object, e_max_mwh: object) -> str:
    reservoir = format_mwh(e_max_mwh)
    if storage_hours is None:
        return reservoir
    return f"{format_hours(storage_hours)} · {reservoir}"


def format_percent_fraction(value: object) -> str:
    number = require_finite(value)
    shown = number * 100.0
    if abs(shown - round(shown)) < 1e-9:
        return f"{int(round(shown))}%"
    return f"{shown:.1f}%"


def format_period(start_date: str, end_date: str) -> str:
    return f"Belgian delivery {start_date} to {end_date}"


def format_balancing(profile: object) -> str:
    if not isinstance(profile, str) or not profile.strip():
        raise ValueError("balancing")
    return BALANCING_LABELS.get(profile, profile.replace("_", " ").title())


def format_pv_self_share(
    *,
    self_consumed: object,
    available: object,
    pv_included: bool,
) -> str:
    available_n = require_finite(available)
    self_n = require_finite(self_consumed)
    if available_n < 0 or self_n < 0:
        raise ValueError("pv share")
    if not pv_included or available_n == 0:
        return "—"
    return f"{(self_n / available_n) * 100:.1f}%"
