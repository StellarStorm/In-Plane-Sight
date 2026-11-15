import pytest

from sources.flight_phase import FlightPhase
from sources.geo import distance_nm


class Flight:
    """Feeds a FlightPhase one reading every 15 seconds."""

    def __init__(self, near_destination=False, category=None):
        self.phase = FlightPhase()
        self.now = 0.0
        self.altitude = 0
        self.near_destination = near_destination
        self.category = category

    def fly(self, altitude_ft, rate_fpm, minutes=0.25):
        self.altitude = altitude_ft
        for _ in range(max(1, round(minutes * 4))):
            self.now += 15
            value = self.phase.update(
                altitude_ft,
                rate_fpm,
                self.now,
                self.near_destination,
                self.category,
            )
        return value

    def change(self, end_ft, rate_fpm, report_rate=True):
        """Climb or descend steadily. Returns the phases seen after the
        first minute, once the averaged rate has caught up."""
        seen = set()
        readings = 0
        while (end_ft - self.altitude) * rate_fpm > 0:
            altitude = round(self.altitude + rate_fpm / 4)
            value = self.fly(altitude, rate_fpm if report_rate else None)
            readings += 1
            if readings > 4:
                seen.add(value)
        return seen


def cruise_at(altitude_ft, **kwargs):
    flight = Flight(**kwargs)
    flight.change(altitude_ft, 2000)
    flight.fly(altitude_ft, 0, minutes=10)
    return flight


def test_short_level_offs_in_the_climb_stay_climbing():
    flight = Flight()
    assert flight.change(10000, 2000) == {'climbing'}
    assert flight.fly(10000, 0, minutes=4.5) == 'climbing'
    assert flight.change(36000, 2000) == {'climbing'}
    assert flight.fly(36000, 0, minutes=4.5) == 'climbing'
    assert flight.fly(36000, 0, minutes=2) == 'cruising'


def test_holding_on_the_way_down_stays_descending():
    flight = cruise_at(36000)
    assert flight.change(10000, -2000) == {'descending'}
    assert flight.fly(10000, 0, minutes=20) == 'descending'
    assert flight.fly(10300, 600) == 'descending'  # small climb in the hold
    assert flight.change(2000, -1000) == {'descending'}


def test_a_small_change_of_cruise_level_is_not_an_approach():
    flight = cruise_at(38000)
    assert flight.change(36000, -1000) == {'descending'}
    assert flight.fly(36000, 0, minutes=1.5) == 'cruising'


def test_a_long_level_segment_high_up_is_a_lower_cruise_level():
    flight = cruise_at(38000)
    flight.change(31000, -1500)
    assert flight.fly(31000, 0, minutes=10) == 'descending'
    assert flight.fly(31000, 0, minutes=2) == 'cruising'
    # The new cruise level is the reference for the real descent later.
    flight.change(28000, -1500)
    assert flight.fly(28000, 0, minutes=1.5) == 'cruising'
    flight.change(20000, -1500)
    assert flight.fly(20000, 0, minutes=1.5) == 'descending'


def test_go_around_climbs_away_from_the_approach():
    flight = cruise_at(36000)
    flight.change(300, -1500)
    assert flight.change(3000, 2000) == {'climbing'}
    assert flight.fly(3000, 0) == 'climbing'


def test_near_the_destination_a_high_hold_stays_descending():
    flight = cruise_at(36000, near_destination=True)
    flight.change(33000, -1500)  # less than DESCENT_FT
    assert flight.fly(33000, 0, minutes=20) == 'descending'


def test_airliner_picked_up_during_the_descent():
    flight = Flight(category='A3')
    flight.fly(10400, -1300)
    flight.change(9000, -1300)
    assert flight.fly(9000, 0, minutes=15) == 'descending'


def test_one_slow_reading_does_not_interrupt_a_descent():
    flight = Flight()
    flight.fly(10400, -1300)
    flight.fly(10200, -600)
    assert flight.fly(10100, -400) == 'descending'


def test_light_aircraft_slow_climb_and_small_altitude_change():
    flight = Flight(category='A1')
    assert flight.change(4500, 400) == {'climbing'}
    flight.fly(4500, 0, minutes=10)
    assert flight.change(3500, -500) == {'descending'}
    assert flight.fly(3500, 0, minutes=1.5) == 'cruising'


def test_light_aircraft_lower_cruise_after_a_big_descent():
    flight = cruise_at(6500, category='A1')
    flight.change(3500, -600)
    assert flight.fly(3500, 0, minutes=9) == 'descending'
    assert flight.fly(3500, 0, minutes=3) == 'cruising'


def test_light_aircraft_go_around():
    flight = cruise_at(4500, category='A1')
    flight.change(400, -500)
    assert 'climbing' in flight.change(1100, 700)


def test_missing_vertical_rate_comes_from_altitude_changes():
    flight = cruise_at(5000)
    assert flight.change(3000, -1000, report_rate=False) == {'descending'}


def test_a_stale_reported_rate_does_not_override_level_altitude():
    flight = cruise_at(2400, category='A1')
    assert flight.fly(2400, -640, minutes=2) == 'cruising'


def test_distance_nm():
    # Newark to San Francisco.
    assert distance_nm(40.69, -74.17, 37.62, -122.38) == pytest.approx(
        2225, abs=10
    )
