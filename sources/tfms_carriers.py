import re
import time
from collections.abc import Callable, Mapping
from threading import Lock

# Each flight in a TFMS Flight Data message starts with a tag such as
# <fdm:fltdMessage acid="RPA4436" airline="RPA" major="AAL" ...>. Only these
# attributes are needed, and the feed cannot be filtered, so reading just the
# tags avoids parsing tens of megabytes of XML a minute.
FLIGHT_TAG = re.compile(r'<(?:\w+:)?fltdMessage\s([^>]*)>')
ATTRIBUTE = re.compile(r'\b(acid|major|sensitivity)="([^"]*)"')


def parse_major_carriers(payload: str | bytes) -> list[tuple[str, str]]:
    """(callsign, major carrier) for each flight in a TFMS Flight Data
    message. The major carrier is the airline a flight flies for, such as AAL
    for a Republic Airways flight sold as American Eagle."""
    if isinstance(payload, bytes):
        payload = payload.decode('utf-8', 'replace')
    carriers = []
    for tag in FLIGHT_TAG.finditer(payload):
        attributes = dict(ATTRIBUTE.findall(tag.group(1)))
        # Like the route feed, leave out flights the FAA marks sensitive.
        # Everything seen so far is "A".
        if attributes.get('sensitivity', 'A') != 'A':
            continue
        callsign = ' '.join(attributes.get('acid', '').split()).upper()
        major = attributes.get('major', '').strip().upper()
        if callsign and major:
            carriers.append((callsign, major))
    return carriers


class CallsignIndex[T]:
    """Values by callsign from the TFMS feed, such as each flight's major
    carrier.

    parse(payload) gives (callsign, value) pairs from a message. A value is
    dropped ttl_seconds after the feed last reported it. While two flights
    with the same callsign report different values, neither is shown, since
    there is no telling which is right.
    """

    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(
        self,
        parse: Callable[[str | bytes], list[tuple[str, T]]],
        ttl_seconds: int,
        clock=time.time,
    ):
        self._parse = parse
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        # callsign -> {value: when TFMS last reported it}
        self._values: dict[str, dict[T, float]] = {}
        self._last_cleanup_at = clock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._values)

    def handle_message(
        self, payload: str | bytes, headers: Mapping[str, object]
    ) -> None:
        now = self._clock()
        with self._lock:
            for callsign, value in self._parse(payload):
                self._values.setdefault(callsign, {})[value] = now

    def expire(self) -> int:
        """Drop values TFMS has not reported for ttl_seconds. Scans at most
        once a minute, and returns how many callsigns were dropped."""
        now = self._clock()
        if now - self._last_cleanup_at < self.CLEANUP_INTERVAL_SECONDS:
            return 0
        cutoff = now - self._ttl_seconds
        dropped = 0
        with self._lock:
            for callsign in list(self._values):
                reports = self._values[callsign]
                for value in [v for v, at in reports.items() if at <= cutoff]:
                    del reports[value]
                if not reports:
                    del self._values[callsign]
                    dropped += 1
            self._last_cleanup_at = now
        return dropped

    def lookup_many(self, callsigns: list[str | None]) -> dict[str, T]:
        """Value by callsign, for callsigns TFMS knows unambiguously."""
        cutoff = self._clock() - self._ttl_seconds
        results = {}
        with self._lock:
            for callsign in callsigns:
                callsign = ' '.join((callsign or '').split()).upper()
                current = [
                    value
                    for value, at in self._values.get(callsign, {}).items()
                    if at > cutoff
                ]
                if len(current) == 1:
                    results[callsign] = current[0]
        return results


class MajorCarrierIndex(CallsignIndex[str]):
    """Major carrier by callsign. TFMS sends updates for each airborne flight
    every few minutes, so an hour without one means the flight is over."""

    def __init__(self, ttl_seconds: int = 60 * 60, clock=time.time):
        super().__init__(parse_major_carriers, ttl_seconds, clock)
