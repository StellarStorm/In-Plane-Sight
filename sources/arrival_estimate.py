"""Sanity-check FAA arrival estimates and calculate a replacement when needed.

FDPS track messages carry one arrival estimate per flight. It is right for most
flights, but a few percent are hours (or exactly a day) off, mostly business
and private flights. When an estimate is implausible for the distance and the
kind of aircraft, it is replaced with one calculated from the aircraft's
current position and ground speed, or dropped if that is not possible.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

EARTH_RADIUS_NM = 3440.065

# Rough cruise speeds (kt) by ADS-B emitter category, used only to decide
# whether a whole-flight duration is believable. Deliberately on the slow side.
CRUISE_SPEED_BY_CATEGORY = {
    'A1': 110,  # light, under 15,500 lb
    'A2': 200,  # small, 15,500 to 75,000 lb
    'A3': 350,  # large
    'A4': 350,  # high-vortex large (e.g. B757)
    'A5': 380,  # heavy
    'A6': 350,  # high performance
    'A7': 90,  # rotorcraft
}
DEFAULT_CRUISE_SPEED = 200

# A flight may take up to this multiple of the distance-based time, plus the
# allowance, before its estimate is treated as wrong. Covers headwinds,
# holding and indirect routings.
DURATION_FACTOR = 2.0
DURATION_ALLOWANCE = timedelta(minutes=45)

# An estimate this far in the past is stale if the aircraft is still away
# from its destination.
STALE_AFTER = timedelta(minutes=15)
NEAR_DESTINATION_NM = 30

# Calculated estimates need a meaningful ground speed, plus time to descend,
# approach and land.
MIN_GROUND_SPEED_KT = 60
APPROACH_ALLOWANCE = timedelta(minutes=5)


@dataclass(frozen=True)
class ArrivalTime:
    time: str | None
    estimated: bool
    calculated: bool = False


def distance_nm(start: tuple[float, float], end: tuple[float, float]) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (*start, *end))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return EARTH_RADIUS_NM * 2 * math.asin(math.sqrt(h))


def resolve_arrival(
    *,
    arrival_time: str | None,
    arrival_estimated: bool,
    departure_time: str | None,
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    position: tuple[float, float] | None,
    ground_speed_kt: float | None,
    category: str | None,
    now: datetime,
) -> ArrivalTime:
    """Return the arrival time to show: the FAA's, a calculated one, or none."""
    if arrival_time and not arrival_estimated:
        return ArrivalTime(arrival_time, estimated=False)

    arrival = _parse(arrival_time)
    if arrival is not None and _is_plausible(
        arrival=arrival,
        departure=_parse(departure_time),
        origin=origin,
        destination=destination,
        position=position,
        ground_speed_kt=ground_speed_kt,
        category=category,
        now=now,
    ):
        return ArrivalTime(arrival_time, estimated=True)

    calculated = _calculate(destination, position, ground_speed_kt, now)
    if calculated is not None:
        return ArrivalTime(_format(calculated), estimated=True, calculated=True)
    return ArrivalTime(None, estimated=False)


def _is_plausible(
    *,
    arrival: datetime,
    departure: datetime | None,
    origin: tuple[float, float] | None,
    destination: tuple[float, float] | None,
    position: tuple[float, float] | None,
    ground_speed_kt: float | None,
    category: str | None,
    now: datetime,
) -> bool:
    if departure is not None and arrival <= departure:
        return False

    if (
        arrival < now - STALE_AFTER
        and destination is not None
        and position is not None
        and distance_nm(position, destination) > NEAR_DESTINATION_NM
    ):
        return False

    if departure is not None and origin is not None and destination is not None:
        cruise = _cruise_speed(category, ground_speed_kt)
        expected = timedelta(hours=distance_nm(origin, destination) / cruise)
        if arrival - departure > expected * DURATION_FACTOR + DURATION_ALLOWANCE:
            return False

    return True


def _cruise_speed(category: str | None, ground_speed_kt: float | None) -> float:
    speed = CRUISE_SPEED_BY_CATEGORY.get((category or '').strip().upper())
    if speed is not None:
        return speed
    if ground_speed_kt and ground_speed_kt >= MIN_GROUND_SPEED_KT:
        return min(ground_speed_kt, DEFAULT_CRUISE_SPEED)
    return DEFAULT_CRUISE_SPEED


def _calculate(
    destination: tuple[float, float] | None,
    position: tuple[float, float] | None,
    ground_speed_kt: float | None,
    now: datetime,
) -> datetime | None:
    if (
        destination is None
        or position is None
        or not ground_speed_kt
        or ground_speed_kt < MIN_GROUND_SPEED_KT
    ):
        return None
    remaining = timedelta(hours=distance_nm(position, destination) / ground_speed_kt)
    return now + remaining + APPROACH_ALLOWANCE


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _format(value: datetime) -> str:
    # Whole minutes, like the FAA's times.
    rounded = (value + timedelta(seconds=30)).replace(second=0, microsecond=0)
    return rounded.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
