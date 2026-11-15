"""Climbing, cruising or descending, judged over the flight so far.

A single vertical-rate reading is misleading on the way down: an aircraft
held level at 10,000 ft for traffic is still coming in to land. So once a
real descent has begun, the phase stays "descending" until landing, unless
the aircraft climbs away (a go-around) or settles at a lower cruise level.
"""

from collections import deque
from dataclasses import dataclass, field

# Light aircraft climb and descend at only a few hundred feet per minute.
LEVEL_FPM = 300
# Judging the rate over this long stops turbulence flipping the phase.
RATE_WINDOW_SECONDS = 60
# Short level-offs during the climb, usually set by air traffic control.
CLIMB_LEVEL_SECONDS = 5 * 60
# Below the highest altitude of the flight by this much (or a third of it,
# for low flights) is a real descent, not a change of cruise level.
DESCENT_FT = 5000
GO_AROUND_FT = 500
# For flights that cruise above this, changes of cruise level happen above
# it, so descending below it is an approach.
CRUISE_FLOOR_FT = 18000
# A level segment this long after descending is a new cruise level, unless
# the flight is coming down from above CRUISE_FLOOR_FT and is now below it.
LOWER_CRUISE_SECONDS = 10 * 60
NEAR_DESTINATION_NM = 150
# ADS-B emitter categories for large and heavy aircraft, which cruise high.
AIRLINER_CATEGORIES = {'A3', 'A4', 'A5'}


@dataclass
class FlightPhase:
    value: str | None = None
    top_ft: int = 0
    approaching: bool = False
    lowest_ft: int = 0
    level_since: float | None = None
    readings: deque[tuple[float, int, int | None]] = field(
        default_factory=deque
    )

    def update(
        self,
        altitude_ft: int,
        vertical_rate_fpm: int | None,
        now: float,
        near_destination: bool = False,
        category: str | None = None,
    ) -> str:
        rate = self._average_rate(altitude_ft, vertical_rate_fpm, now)
        self.top_ft = max(self.top_ft, altitude_ft)
        self.lowest_ft = min(self.lowest_ft, altitude_ft)
        cruises_high = (
            self.top_ft >= CRUISE_FLOOR_FT or category in AIRLINER_CATEGORIES
        )

        if rate > LEVEL_FPM:
            self.level_since = None
            if self.approaching and altitude_ft - self.lowest_ft < GO_AROUND_FT:
                self.value = 'descending'
            else:
                self.approaching = False
                self.value = 'climbing'
        elif rate < -LEVEL_FPM:
            self.level_since = None
            if not self.approaching and (
                self.top_ft - altitude_ft >= min(DESCENT_FT, self.top_ft / 3)
                or (cruises_high and altitude_ft < CRUISE_FLOOR_FT)
                or near_destination
            ):
                self.approaching = True
                self.lowest_ft = altitude_ft
            self.value = 'descending'
        else:
            if self.level_since is None:
                self.level_since = now
            level_for = now - self.level_since
            if (
                self.approaching
                and not near_destination
                and level_for >= LOWER_CRUISE_SECONDS
                and (altitude_ft >= CRUISE_FLOOR_FT or not cruises_high)
            ):
                self.approaching = False
                self.top_ft = altitude_ft
            if self.approaching:
                self.value = 'descending'
            elif self.value == 'climbing' and level_for < CLIMB_LEVEL_SECONDS:
                self.value = 'climbing'
            else:
                self.value = 'cruising'
        return self.value

    def _average_rate(
        self, altitude_ft: int, reported_fpm: int | None, now: float
    ) -> float:
        """Feet per minute over about the last minute.

        Uses the change in altitude once a minute of readings is available,
        because reported rates (especially GPS rates) are sometimes stale.
        """
        self.readings.append((now, altitude_ft, reported_fpm))
        while now - self.readings[0][0] > RATE_WINDOW_SECONDS:
            self.readings.popleft()
        first_at, first_ft, _ = self.readings[0]
        span = now - first_at
        if span >= RATE_WINDOW_SECONDS * 3 / 4:
            return (altitude_ft - first_ft) * 60 / span
        reported = [rate for _, _, rate in self.readings if rate is not None]
        if reported:
            return sum(reported) / len(reported)
        return (altitude_ft - first_ft) * 60 / span if span else 0
