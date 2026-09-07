"""Belgian calendar-week windows for the Data explorer. No artifact I/O."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

from ui.services.result_format import require_int

BRUSSELS = ZoneInfo("Europe/Brussels")
INTERVAL = timedelta(minutes=15)
DEMO_DEFAULT_WEEK_ID = "2025-W20"
COMPLETE_ORDINARY_ROWS = 672
COMPLETE_SPRING_ROWS = 668
COMPLETE_AUTUMN_ROWS = 676


def parse_utc(value: object) -> datetime:
    if isinstance(value, datetime):
        moment = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        moment = datetime.fromisoformat(text)
    else:
        raise ValueError("utc")
    if moment.tzinfo is None:
        raise ValueError("utc")
    return moment.astimezone(timezone.utc)


def expected_row_count(start_utc: datetime, end_utc: datetime) -> int:
    delta = end_utc - start_utc
    seconds = delta.total_seconds()
    if seconds <= 0 or seconds % INTERVAL.total_seconds() != 0:
        raise ValueError("window")
    return int(seconds // INTERVAL.total_seconds())


def week_id(iso_year: int, iso_week: int) -> str:
    return f"{iso_year}-W{iso_week:02d}"


def parse_week_id(value: object) -> tuple[int, int]:
    if not isinstance(value, str) or len(value) < 7 or "-W" not in value:
        raise ValueError("week")
    year_text, week_text = value.split("-W", 1)
    year = require_int(int(year_text)) if year_text.isdigit() else None
    week = require_int(int(week_text)) if week_text.isdigit() else None
    if year is None or week is None or week < 1 or week > 53:
        raise ValueError("week")
    return year, week


def _monday_start(local: datetime) -> datetime:
    day = local.date() - timedelta(days=local.weekday())
    return datetime(day.year, day.month, day.day, tzinfo=BRUSSELS)


def _dst_kind(*, partial: bool, rows: int) -> str:
    if partial:
        return "partial"
    if rows == COMPLETE_SPRING_ROWS:
        return "spring"
    if rows == COMPLETE_AUTUMN_ROWS:
        return "autumn"
    return "ordinary"


def week_label(week: Mapping[str, Any]) -> str:
    suffix = " · partial" if week["partial"] else ""
    return (
        f"Week {week['iso_week']} ({week['iso_year']}) · "
        f"{week['start_local_date']} to {week['end_local_date']} · "
        f"{week['expected_rows']} quarter-hours{suffix}"
    )


def week_count_copy(week: Mapping[str, Any]) -> str:
    rows = int(week["expected_rows"])
    if week["partial"]:
        return (
            f"This partial week contains {rows} quarter-hours "
            "within the selected simulation period."
        )
    if rows == COMPLETE_SPRING_ROWS:
        return (
            "This complete week contains 668 quarter-hours "
            "because the clock moves forward in Belgium."
        )
    if rows == COMPLETE_AUTUMN_ROWS:
        return (
            "This complete week contains 676 quarter-hours "
            "because the clock moves back in Belgium."
        )
    return f"This complete week contains {rows} quarter-hours."


def enumerate_weeks(start_utc: object, end_exclusive_utc: object) -> list[dict[str, Any]]:
    start = parse_utc(start_utc)
    end = parse_utc(end_exclusive_utc)
    if end <= start:
        raise ValueError("window")
    local_start = start.astimezone(BRUSSELS)
    cursor = _monday_start(local_start)
    weeks: list[dict[str, Any]] = []
    while cursor.astimezone(timezone.utc) < end:
        raw_start = cursor.astimezone(timezone.utc)
        raw_end = (cursor + timedelta(days=7)).astimezone(timezone.utc)
        clipped_start = max(raw_start, start)
        clipped_end = min(raw_end, end)
        cursor = cursor + timedelta(days=7)
        if clipped_start >= clipped_end:
            continue
        rows = expected_row_count(clipped_start, clipped_end)
        iso = raw_start.astimezone(BRUSSELS).isocalendar()
        iso_year = int(iso.year)
        iso_week = int(iso.week)
        last_local = (clipped_end - INTERVAL).astimezone(BRUSSELS)
        partial = clipped_start > raw_start or clipped_end < raw_end
        week = {
            "week_id": week_id(iso_year, iso_week),
            "iso_year": iso_year,
            "iso_week": iso_week,
            "start_utc": clipped_start.isoformat(),
            "end_utc": clipped_end.isoformat(),
            "start_local_date": clipped_start.astimezone(BRUSSELS).date().isoformat(),
            "end_local_date": last_local.date().isoformat(),
            "expected_rows": rows,
            "partial": partial,
            "dst_kind": _dst_kind(partial=partial, rows=rows),
        }
        week["label"] = week_label(week)
        weeks.append(week)
    if not weeks:
        raise ValueError("weeks")
    return weeks


def default_week_id(weeks: Sequence[Mapping[str, Any]], *, demo: bool) -> str:
    if not weeks:
        raise ValueError("weeks")
    if demo:
        for week in weeks:
            if week["week_id"] == DEMO_DEFAULT_WEEK_ID and not week["partial"]:
                return str(week["week_id"])
    for week in weeks:
        if not week["partial"]:
            return str(week["week_id"])
    return str(weeks[0]["week_id"])


def find_week(weeks: Sequence[Mapping[str, Any]], week_id_value: str) -> dict[str, Any]:
    for week in weeks:
        if week["week_id"] == week_id_value:
            return dict(week)
    raise ValueError("week")


def shared_week_ids(weeks_by_market: Mapping[str, Sequence[Mapping[str, Any]]]) -> list[str]:
    ids: list[str] | None = None
    for weeks in weeks_by_market.values():
        current = [str(week["week_id"]) for week in weeks]
        ids = current if ids is None else [item for item in ids if item in current]
    return list(ids or ())


def local_hover_label(moment: datetime) -> str:
    local = moment.astimezone(BRUSSELS)
    zone = local.tzname() or local.strftime("%z")
    return f"{local.strftime('%Y-%m-%d %H:%M')} {zone}"


def week_axis(timestamps: Sequence[datetime]) -> dict[str, Any]:
    if not timestamps:
        raise ValueError("axis")
    hover = [local_hover_label(item) for item in timestamps]
    tick_vals: list[int] = []
    tick_text: list[str] = []
    for index, moment in enumerate(timestamps):
        local = moment.astimezone(BRUSSELS)
        if index == 0 or (local.hour == 0 and local.minute == 0):
            if index != 0 and tick_vals and tick_vals[-1] == index:
                continue
            tick_vals.append(index)
            tick_text.append(local.strftime("%Y-%m-%d"))
    if 0 not in tick_vals:
        tick_vals.insert(0, 0)
        tick_text.insert(0, timestamps[0].astimezone(BRUSSELS).strftime("%Y-%m-%d"))
    return {
        "x_index": list(range(len(timestamps))),
        "hover_labels": hover,
        "tick_vals": tick_vals,
        "tick_text": tick_text,
    }
