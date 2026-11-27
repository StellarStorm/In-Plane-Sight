import re
import time
from collections.abc import Mapping
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


class MajorCarrierIndex:
    """Major carrier by callsign, from the TFMS feed.

    TFMS sends updates for each airborne flight every few minutes, so a
    carrier is dropped ttl_seconds after the feed last mentioned it. While two
    flights with the same callsign report different carriers, neither is
    shown, since there is no telling which is right.
    """

    CLEANUP_INTERVAL_SECONDS = 60

    def __init__(self, ttl_seconds: int = 60 * 60, clock=time.time):
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = Lock()
        # callsign -> {major carrier: when TFMS last reported it}
        self._carriers: dict[str, dict[str, float]] = {}
        self._last_cleanup_at = clock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._carriers)

    def handle_message(
        self, payload: str | bytes, headers: Mapping[str, object]
    ) -> None:
        now = self._clock()
        with self._lock:
            for callsign, major in parse_major_carriers(payload):
                self._carriers.setdefault(callsign, {})[major] = now

    def expire(self) -> int:
        """Drop carriers TFMS has not reported for ttl_seconds. Scans at most
        once a minute, and returns how many callsigns were dropped."""
        now = self._clock()
        if now - self._last_cleanup_at < self.CLEANUP_INTERVAL_SECONDS:
            return 0
        cutoff = now - self._ttl_seconds
        dropped = 0
        with self._lock:
            for callsign in list(self._carriers):
                reports = self._carriers[callsign]
                for major in [m for m, at in reports.items() if at <= cutoff]:
                    del reports[major]
                if not reports:
                    del self._carriers[callsign]
                    dropped += 1
            self._last_cleanup_at = now
        return dropped

    def lookup_many(self, callsigns: list[str | None]) -> dict[str, str]:
        """Major carrier by callsign, for callsigns TFMS knows unambiguously."""
        cutoff = self._clock() - self._ttl_seconds
        results = {}
        with self._lock:
            for callsign in callsigns:
                callsign = ' '.join((callsign or '').split()).upper()
                current = [
                    major
                    for major, at in self._carriers.get(callsign, {}).items()
                    if at > cutoff
                ]
                if len(current) == 1:
                    results[callsign] = current[0]
        return results
