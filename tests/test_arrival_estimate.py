from datetime import datetime, timezone

from sources.airport_codes import airport_location
from sources.arrival_estimate import ArrivalTime, resolve_arrival

NOW = datetime(2026, 11, 12, 3, 30, tzinfo=timezone.utc)
TEB = airport_location('KTEB')
CMH = airport_location('KCMH')
EWR = airport_location('KEWR')
ORD = airport_location('KORD')
NEAR_TEB = (40.95, -74.3)


def resolve(**overrides):
    values = {
        'arrival_time': '2026-11-12T04:55:00Z',
        'arrival_estimated': True,
        'departure_time': '2026-11-12T03:25:00Z',
        'origin': TEB,
        'destination': CMH,
        'position': NEAR_TEB,
        'ground_speed_kt': 344,
        'category': 'A2',
        'now': NOW,
    }
    values.update(overrides)
    return resolve_arrival(**values)


def test_plausible_faa_estimate_is_kept():
    assert resolve() == ArrivalTime('2026-11-12T04:55:00Z', estimated=True)


def test_actual_arrival_time_is_always_kept():
    result = resolve(arrival_time='2026-11-12T12:26:00Z', arrival_estimated=False)
    assert result == ArrivalTime('2026-11-12T12:26:00Z', estimated=False)


def test_estimate_far_too_long_for_distance_is_recalculated():
    # Seen live: N822VJ Teterboro to Columbus, 9 hours.
    result = resolve(arrival_time='2026-11-12T12:26:00Z')

    assert result.calculated is True
    assert result.estimated is True
    calculated = datetime.fromisoformat(result.time.replace('Z', '+00:00'))
    assert NOW < calculated < datetime(2026, 11, 12, 5, 0, tzinfo=timezone.utc)


def test_estimate_a_day_late_is_recalculated():
    # Seen live: CNS228, right time of day but the next day's date.
    result = resolve(arrival_time='2026-11-13T04:55:00Z')
    assert result.calculated is True


def test_airline_estimate_too_long_is_recalculated():
    # Seen live: UAL2333 Newark to Chicago O'Hare, 6.5 hours.
    result = resolve(
        arrival_time='2026-11-12T09:54:00Z',
        departure_time='2026-11-12T03:26:00Z',
        origin=EWR,
        destination=ORD,
        position=(40.8, -74.6),
        ground_speed_kt=195,
        category='A3',
    )
    assert result.calculated is True


def test_slow_light_aircraft_keeps_a_long_but_real_estimate():
    # About 3 hours for Teterboro to Columbus at 110-150 kt is normal.
    result = resolve(
        arrival_time='2026-11-12T06:25:00Z',
        ground_speed_kt=140,
        category='A1',
    )
    assert result == ArrivalTime('2026-11-12T06:25:00Z', estimated=True)


def test_estimate_before_departure_is_rejected():
    result = resolve(arrival_time='2026-11-12T03:00:00Z')
    assert result.calculated is True


def test_stale_estimate_while_far_from_destination_is_rejected():
    result = resolve(
        arrival_time='2026-11-12T03:10:00Z',
        departure_time='2026-11-12T02:00:00Z',
    )
    assert result.calculated is True


def test_past_estimate_near_destination_is_kept():
    result = resolve(
        arrival_time='2026-11-12T03:10:00Z',
        departure_time='2026-11-12T02:00:00Z',
        position=(40.0, -82.85),
    )
    assert result == ArrivalTime('2026-11-12T03:10:00Z', estimated=True)


def test_implausible_estimate_without_speed_is_dropped():
    result = resolve(arrival_time='2026-11-12T12:26:00Z', ground_speed_kt=None)
    assert result == ArrivalTime(None, estimated=False)


def test_missing_estimate_is_calculated_when_possible():
    result = resolve(arrival_time=None)
    assert result.calculated is True


def test_unknown_airports_keep_the_faa_estimate():
    result = resolve(
        arrival_time='2026-11-12T12:26:00Z',
        origin=None,
        destination=None,
    )
    assert result == ArrivalTime('2026-11-12T12:26:00Z', estimated=True)
