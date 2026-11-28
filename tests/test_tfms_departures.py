from datetime import datetime

import main
from sources.tfms_departures import DepartureIndex, parse_departures


def unix(text: str) -> float:
    return datetime.fromisoformat(text).timestamp()


def flight(
    ref: str,
    acid: str,
    etd_type: str | None,
    etd: str = '',
    msg_type: str = 'FlightTimes',
    trigger: str = 'FD_FLIGHT_MODIFY_MSG',
    status: str = 'PLANNED',
    route: tuple[str, str] = ('KSLC', 'KSAN'),
) -> str:
    """A flight element trimmed from a real TFMS R14 Flight Data message."""
    times = (
        f'<nxcm:etd etdType="{etd_type}" timeValue="{etd}"/>'
        if etd_type
        else ''
    )
    return (
        f'<fdm:fltdMessage acid="{acid}" airline="SWA" arrArpt="{route[1]}" '
        f'depArpt="{route[0]}" fdTrigger="{trigger}" flightRef="{ref}" '
        f'major="SWA" msgType="{msg_type}" sensitivity="A">'
        f'<fdm:ncsmFlightTimes><nxcm:flightStatusAndSpec>'
        f'<nxcm:flightStatus>{status}</nxcm:flightStatus>'
        f'</nxcm:flightStatusAndSpec>{times}'
        f'<nxcm:eta etaType="SCHEDULED" timeValue="2026-11-28T01:36:00Z"/>'
        f'</fdm:ncsmFlightTimes></fdm:fltdMessage>'
    )


def message(*flights: str) -> str:
    return (
        '<ds:tfmDataService xmlns:ds="urn:us:gov:dot:faa:atm:tfm:'
        'tfmdataservice"><fltdOutput>'
        + ''.join(flights)
        + '</fltdOutput></ds:tfmDataService>'
    )


class Clock:
    def __init__(self, now: str):
        self.now = unix(now)

    def __call__(self) -> float:
        return self.now


def test_parse_reads_each_flights_own_takeoff_time():
    departures = parse_departures(
        message(
            flight('1', 'SWA117', 'SCHEDULED', '2026-11-28T00:07:00Z'),
            flight('2', 'SWA118', None),
            flight('3', 'SWA119', 'ACTUAL', '2026-11-27T22:01:00Z'),
        )
    )

    assert [(ref, d.callsign, d.kind) for ref, d in departures] == [
        ('1', 'SWA117', 'planned'),
        ('3', 'SWA119', 'departed'),
    ]
    assert departures[0][1].time == unix('2026-11-28T00:07:00Z')
    assert departures[0][1].origin == 'KSLC'


def test_parse_skips_the_time_tfms_moves_to_now_for_late_flights():
    late = flight(
        '1',
        'SWA117',
        'PROPOSED',
        '2026-11-27T22:13:00Z',
        trigger='UPDATE_DEPARTURE_TIMEOUT',
    )
    assert parse_departures(message(late)) == []


def test_lookup_follows_the_latest_estimate_until_takeoff():
    index = DepartureIndex(clock=Clock('2026-11-27T23:00:00Z'))
    index.handle_message(
        message(flight('1', 'SWA117', 'SCHEDULED', '2026-11-28T00:07:00Z')),
        {},
    )
    index.handle_message(
        message(flight('1', 'SWA117', 'ESTIMATED', '2026-11-28T00:30:00Z')),
        {},
    )
    departure = index.lookup(' swa117 ')
    assert departure.kind == 'estimated'
    assert departure.time == unix('2026-11-28T00:30:00Z')

    index.handle_message(
        message(flight('1', 'SWA117', 'ACTUAL', '2026-11-28T00:34:00Z')),
        {},
    )
    # Later flight plan messages still carry the filed time.
    index.handle_message(
        message(flight('1', 'SWA117', 'PROPOSED', '2026-11-28T00:07:00Z')),
        {},
    )
    assert index.lookup('SWA117').kind == 'departed'
    assert index.lookup('SWA117').time == unix('2026-11-28T00:34:00Z')


def test_lookup_picks_the_leg_in_the_air_then_the_next_to_depart():
    clock = Clock('2026-11-27T23:00:00Z')
    index = DepartureIndex(clock=clock)
    index.handle_message(
        message(
            flight(
                '2',
                'SWA1982',
                'SCHEDULED',
                '2026-11-28T04:09:00Z',
                route=('KDAL', 'KOMA'),
            ),
            flight(
                '1',
                'SWA1982',
                'SCHEDULED',
                '2026-11-28T00:14:00Z',
                route=('KSFO', 'KDAL'),
            ),
        ),
        {},
    )
    assert index.lookup('SWA1982').origin == 'KSFO'

    clock.now = unix('2026-11-28T00:20:00Z')
    index.handle_message(
        message(flight('1', 'SWA1982', 'ACTUAL', '2026-11-28T00:18:00Z')),
        {},
    )
    assert index.lookup('SWA1982').kind == 'departed'

    index.handle_message(
        message(flight('1', 'SWA1982', None, msg_type='arrivalInformation')),
        {},
    )
    assert index.lookup('SWA1982').origin == 'KDAL'


def test_overdue_and_arrived_flights_are_dropped():
    clock = Clock('2026-11-27T23:00:00Z')
    index = DepartureIndex(clock=clock)
    index.handle_message(
        message(
            flight('1', 'SWA117', 'PROPOSED', '2026-11-27T22:00:00Z'),
            flight('2', 'SWA118', 'ACTUAL', '2026-11-27T21:00:00Z'),
            flight('2', 'SWA118', None, status='COMPLETED'),
        ),
        {},
    )
    assert index.lookup('SWA117').kind == 'planned'
    assert index.lookup('SWA118') is None

    clock.now = unix('2026-11-28T00:00:00Z')
    assert index.lookup('SWA117') is None
    assert index.expire() == 2
    assert len(index) == 0


def test_tracking_shows_the_departure_while_waiting(monkeypatch):
    index = DepartureIndex(clock=Clock('2026-11-27T23:00:00Z'))
    index.handle_message(
        message(flight('1', 'SWA117', 'SCHEDULED', '2026-11-28T00:07:00Z')),
        {},
    )
    monkeypatch.setattr(main, 'departure_index', index)
    state = {
        'flight': 'WN117',
        'callsign': 'SWA117',
        'status': 'waiting',
        'phase': None,
        'started_at': 0,
        'last_seen_at': None,
        'aircraft': None,
    }

    assert main.tracking_response(state)['departure'] == {
        'time': unix('2026-11-28T00:07:00Z'),
        'kind': 'planned',
        'origin': 'KSLC',
        'destination': 'KSAN',
        'origin_display': 'SLC',
        'destination_display': 'SAN',
    }

    monkeypatch.setattr(main, 'departure_index', None)
    assert main.tracking_response(state)['departure'] is None
