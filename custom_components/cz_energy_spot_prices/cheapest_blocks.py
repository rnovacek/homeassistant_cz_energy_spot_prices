from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final
from zoneinfo import ZoneInfo

from attr import dataclass

from .const import (
    SearchObjective,
    PriceType,
    SearchType,
    SpotRateIntervalType,
)

logger = logging.getLogger(__name__)

# Tolerance for floating-point interval validation
_INTERVAL_TOLERANCE: Final = 0.001


@dataclass(frozen=True, slots=True)
class PriceBlockSearch:
    """Validated runtime representation of one price-block search."""

    id: str
    name: str
    type: SearchType
    length_hours: float
    price_type: PriceType = PriceType.SPOT
    objective: SearchObjective = SearchObjective.LOWEST
    start_time: time | None = None
    end_time: time | None = None
    legacy: bool = False
    config_subentry_id: str | None = None

    @property
    def legacy_block_length(self) -> int | None:
        """Return the released integer block length represented by this search."""
        return legacy_block_length(self.length_hours) if self.legacy else None

    @classmethod
    def from_mapping(
        cls,
        search: dict[str, Any],
        *,
        interval: SpotRateIntervalType,
    ) -> PriceBlockSearch | None:
        """Parse persisted search data, rejecting invalid definitions."""
        try:
            search_id = str(search["id"]).strip()
            name = str(search["name"]).strip()
            search_type = SearchType(search["type"])
            length_hours = float(search["length_hours"])
        except (KeyError, TypeError, ValueError):
            return None

        try:
            price_type = PriceType(search.get("price_type", PriceType.SPOT))
        except (TypeError, ValueError):
            price_type = PriceType.SPOT
        try:
            objective = SearchObjective(search.get("objective", SearchObjective.LOWEST))
        except (TypeError, ValueError):
            objective = SearchObjective.LOWEST

        if not search_id or not name or length_hours <= 0:
            return None
        interval_seconds = 900 if interval == SpotRateIntervalType.QuarterHour else 3600
        try:
            compute_required_intervals(length_hours, interval_seconds)
        except ValueError:
            return None

        start: time | None = None
        end: time | None = None
        if search_type == SearchType.FIXED:
            try:
                start = _parse_time(str(search["start_time"]))
                end = _parse_time(str(search["end_time"]))
            except (KeyError, TypeError, ValueError):
                return None
            if start == end or _fixed_window_duration_hours(start, end) < length_hours:
                return None
        elif length_hours > 24:
            return None

        return cls(
            id=search_id,
            name=name,
            type=search_type,
            length_hours=length_hours,
            price_type=price_type,
            objective=objective,
            start_time=start,
            end_time=end,
            legacy=search.get("legacy") is True,
            config_subentry_id=search.get("config_subentry_id"),
        )


def legacy_block_searches(legacy: Any) -> list[dict[str, Any]]:
    """Convert released comma-separated block lengths into search definitions."""
    if not legacy:
        return []

    import uuid

    searches: list[dict[str, Any]] = []
    seen_lengths: list[float] = []
    for part in str(legacy).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            length = int(part)
        except ValueError:
            logger.warning("Ignoring invalid legacy cheapest block length %r", part)
            continue
        if not 1 <= length <= 23:
            logger.warning("Ignoring unsupported legacy cheapest block length %r", part)
            continue
        if length in seen_lengths:
            continue
        seen_lengths.append(length)

        # Today search
        searches.append(
            {
                "id": str(uuid.uuid4()),
                "name": f"Today {length:g}h",
                "type": SearchType.TODAY,
                "length_hours": length,
                "legacy": True,
            }
        )
        # Tomorrow search
        searches.append(
            {
                "id": str(uuid.uuid4()),
                "name": f"Tomorrow {length:g}h",
                "type": SearchType.TOMORROW,
                "length_hours": length,
                "legacy": True,
            }
        )

    return searches


def legacy_block_length(value: Any) -> int | None:
    """Return a supported released block length without truncating fractions."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not number.is_integer() or not 1 <= number <= 23:
        return None
    return int(number)


def format_search_subentry_title(search: dict[str, Any]) -> str:
    """Return a concise summary suitable for the config subentry headline."""
    name = str(search.get("name", "Price block")).strip() or "Price block"
    try:
        search_type = SearchType(search.get("type"))
    except (TypeError, ValueError):
        search_type = None
    if search_type == SearchType.FIXED:
        period = f"{search.get('start_time', '?')}–{search.get('end_time', '?')}"
    elif search_type == SearchType.TODAY:
        period = "Today"
    elif search_type == SearchType.TOMORROW:
        period = "Tomorrow"
    else:
        period = "Unknown period"

    objective = (
        "Highest"
        if search.get("objective", SearchObjective.LOWEST) == SearchObjective.HIGHEST
        else "Lowest"
    )
    price_type = str(search.get("price_type", "spot")).title()
    try:
        raw_duration = search.get("length_hours")
        if isinstance(raw_duration, bool) or not isinstance(
            raw_duration, (int, float, str)
        ):
            raise TypeError
        duration = f"{float(raw_duration):g} h"
    except (TypeError, ValueError):
        duration = "? h"
    return f"{name} · {period} · {objective} {price_type} · {duration}"


def compute_required_intervals(length_hours: float, interval_seconds: int) -> int:
    """Return number of consecutive intervals needed for the given length."""
    required = length_hours * 3600 / interval_seconds
    rounded = round(required)
    if abs(required - rounded) > _INTERVAL_TOLERANCE:
        raise ValueError(
            f"length_hours {length_hours} is not compatible with "
            f"interval {interval_seconds}s"
        )
    return int(rounded)


def validate_search_definition(
    search: dict[str, Any],
    existing_searches: list[dict[str, Any]] | None = None,
    editing_id: str | None = None,
    interval: SpotRateIntervalType = SpotRateIntervalType.Hour,
) -> dict[str, str]:
    """Validate a single search definition. Returns dict of field errors."""
    errors: dict[str, str] = {}

    name = str(search.get("name", "")).strip()
    if not name:
        errors["name"] = "required"
    elif existing_searches:
        for existing in existing_searches:
            if editing_id and existing.get("id") == editing_id:
                continue
            if str(existing.get("name", "")).strip().lower() == name.lower():
                errors["name"] = "duplicate_name"
                break

    length_hours = search.get("length_hours")
    parsed_length_hours: float | None = None
    try:
        parsed_length_hours = float(length_hours)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        errors["length_hours"] = "invalid_number"
    else:
        if parsed_length_hours <= 0:
            errors["length_hours"] = "must_be_positive"
        else:
            interval_seconds = (
                15 * 60 if interval == SpotRateIntervalType.QuarterHour else 60 * 60
            )
            try:
                compute_required_intervals(parsed_length_hours, interval_seconds)
            except ValueError:
                errors["length_hours"] = "incompatible_interval"

    search_type = search.get("type")
    if (
        search_type in (SearchType.TODAY, SearchType.TOMORROW)
        and parsed_length_hours
        and parsed_length_hours > 24
    ):
        errors["length_hours"] = "longer_than_window"

    if search_type == SearchType.FIXED:
        start_time = search.get("start_time")
        end_time = search.get("end_time")
        if not start_time:
            errors["start_time"] = "required"
        if not end_time:
            errors["end_time"] = "required"
        if start_time and end_time and start_time == end_time:
            errors["end_time"] = "start_equals_end"

        if (
            start_time
            and end_time
            and start_time != end_time
            and parsed_length_hours
            and parsed_length_hours > 0
        ):
            try:
                st = _parse_time(str(start_time))
                et = _parse_time(str(end_time))
            except ValueError:
                pass
            else:
                duration = _fixed_window_duration_hours(st, et)
                if st == et:
                    errors["end_time"] = "start_equals_end"
                elif duration < parsed_length_hours:
                    errors["length_hours"] = "longer_than_window"

    return errors


def resolve_search_window(
    search: PriceBlockSearch,
    now_local: datetime,
    available_end: datetime | None,
    available_start: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Convert a search definition into an absolute start/end window."""
    search_type = search.type
    zoneinfo = now_local.tzinfo
    if zoneinfo is None:
        zoneinfo = ZoneInfo("UTC")

    if search_type == SearchType.TODAY:
        start = datetime.combine(now_local.date(), time(0, 0), tzinfo=zoneinfo)
        end = start + timedelta(days=1)

    elif search_type == SearchType.TOMORROW:
        start = datetime.combine(
            now_local.date() + timedelta(days=1), time(0, 0), tzinfo=zoneinfo
        )
        end = start + timedelta(days=1)

    elif search_type == SearchType.FIXED:
        start_time = search.start_time
        end_time = search.end_time
        if start_time is None or end_time is None:
            return None
        start = datetime.combine(now_local.date(), start_time, tzinfo=zoneinfo)
        end = datetime.combine(now_local.date(), end_time, tzinfo=zoneinfo)
        if end <= start and now_local < end:
            start -= timedelta(days=1)
        elif end <= start:
            end += timedelta(days=1)
        # Roll to next occurrence if window has fully passed
        if end <= now_local:
            start += timedelta(days=1)
            end += timedelta(days=1)

        # Prefer the current or next occurrence, but fall back to the most
        # recent complete occurrence while future prices are not published.
        if available_start is not None or available_end is not None:
            for days_back in range(4):
                candidate_start = start - timedelta(days=days_back)
                candidate_end = end - timedelta(days=days_back)
                if (available_start is None or candidate_start >= available_start) and (
                    available_end is None or candidate_end <= available_end
                ):
                    return candidate_start, candidate_end
            return None

    else:
        logger.debug("Unknown search type: %s", search_type)
        return None

    if (
        end <= start
        or (available_start is not None and start < available_start)
        or (available_end is not None and end > available_end)
    ):
        return None

    return start, end


def find_price_block(
    intervals: list[tuple[datetime, Decimal]],
    window_start: datetime,
    window_end: datetime,
    length_hours: float,
    objective: SearchObjective = SearchObjective.LOWEST,
    *,
    interval_seconds: int | None = None,
    require_complete_window: bool = False,
) -> dict[str, Any] | None:
    """Find the lowest- or highest-priced consecutive block inside the window.

    intervals: list of (utc_dt, price) sorted by utc_dt
    Returns dict with start, end, prices, total, average or None.
    """
    if not intervals:
        return None

    # Determine interval size from data
    interval_seconds = interval_seconds or _infer_interval_seconds(intervals)
    if interval_seconds is None:
        return None

    interval_delta = timedelta(seconds=interval_seconds)
    if require_complete_window:
        covering = [
            dt
            for dt, _price in intervals
            if dt < window_end and dt + interval_delta > window_start
        ]
        if (
            not covering
            or covering[0] > window_start
            or covering[-1] + interval_delta < window_end
            or any(
                following - previous != interval_delta
                for previous, following in zip(covering, covering[1:])
            )
        ):
            return None

    try:
        required = compute_required_intervals(length_hours, interval_seconds)
    except ValueError:
        logger.debug(
            "length_hours %.3f incompatible with interval %ds",
            length_hours,
            interval_seconds,
        )
        return None

    # Filter intervals fully inside window
    filtered = [
        (dt, price)
        for dt, price in intervals
        if window_start <= dt and dt + timedelta(seconds=interval_seconds) <= window_end
    ]

    if len(filtered) < required:
        return None

    selected_sum: Decimal | None = None
    selected_start: datetime | None = None
    selected_end: datetime | None = None
    selected_prices: list[Decimal] | None = None

    for start_index in range(len(filtered) - required + 1):
        candidate = filtered[start_index : start_index + required]
        if any(
            following[0] - previous[0] != interval_delta
            for previous, following in zip(candidate, candidate[1:])
        ):
            continue
        window_prices = [price for _dt, price in candidate]
        window_sum = sum(window_prices, start=Decimal(0))
        is_better = selected_sum is None or (
            window_sum < selected_sum
            if objective == SearchObjective.LOWEST
            else window_sum > selected_sum
        )
        if is_better:
            selected_sum = window_sum
            selected_start = candidate[0][0]
            selected_end = candidate[-1][0] + interval_delta
            selected_prices = window_prices

    if (
        selected_start is None
        or selected_end is None
        or selected_prices is None
        or selected_sum is None
    ):
        return None

    return {
        "start": selected_start,
        "end": selected_end,
        "prices": selected_prices,
        "total": selected_sum,
        "average": selected_sum / len(selected_prices),
    }


def _infer_interval_seconds(
    intervals: list[tuple[datetime, Decimal]],
) -> int | None:
    """Infer interval size in seconds from first two intervals."""
    if len(intervals) < 2:
        return None
    delta = int((intervals[1][0] - intervals[0][0]).total_seconds())
    return delta if delta > 0 else None


def _parse_time(value: str) -> time:
    """Parse a local ISO time without discarding seconds."""
    parsed = time.fromisoformat(value.strip())
    if parsed.tzinfo is not None:
        raise ValueError("Search times must be local times without a UTC offset")
    return parsed


def _fixed_window_duration_hours(start: time, end: time) -> float:
    """Return duration in hours for a time window (handles cross-midnight)."""
    day = datetime.min.date()
    duration = datetime.combine(day, end) - datetime.combine(day, start)
    if duration <= timedelta(0):
        duration += timedelta(days=1)
    return duration.total_seconds() / 3600
