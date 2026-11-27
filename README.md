# In-Plane-Sight

Show the aircraft flying overhead, updating in real time.

![main page](imgs/main.png)

There are four browser views: the main page (shown above), a list (below), a
live map, and a page for tracking one flight.

| Path     | View                        |
| -------- | --------------------------- |
| `/`      | LED-style information board |
| `/list`  | List of aircraft            |
| `/map`   | Map with aircraft positions |
| `/track` | Track one flight            |

![list](imgs/list.png)

<img src="imgs/phone.png" width="250" alt="The LED board on a phone">

It also looks great on a phone!

By default, aircraft positions come from adsb.fi, with adsb.lol as a fallback. No account or API key is needed. Aircraft on the ground are not shown.

## Getting Started

### Requirements

- [uv](https://docs.astral.sh/uv/)

### Install

```sh
git clone https://github.com/StellarStorm/In-Plane-Sight.git
cd In-Plane-Sight
uv sync
```

### Configure

Copy the example configuration and edit it:

```sh
cp config.example.toml config.toml
```

At minimum, set your location and search radius:

```toml
lat = 40.7128
lon = -74.0060
radius_nm = 20
```

Every setting is optional. Without a `config.toml`, the defaults below are used.

| Setting            | Default           | Description                                   |
| ------------------ | ----------------- | --------------------------------------------- |
| `lat`, `lon`       | New York City     | Center of the search area.                    |
| `radius_nm`        | `20`              | Search radius in nautical miles.              |
| `source`           | `"internet"`      | ADS-B source: `internet` (adsb.fi, then adsb.lol) or `local` (see below). |
| `local_url`        | `"http://localhost:8080/data/aircraft.json"` | dump1090/readsb `aircraft.json` used when `source = "local"`. |
| `refresh_seconds`  | `15`              | How often aircraft data is refreshed.         |
| `time_format`      | `"24"`            | `"12"` or `"24"` hour clock.                  |
| `font_thickness`   | `"normal"`        | LED font weight: `normal` or `broad`.         |
| `aircraft_type_format` | `"code"`      | `code` (e.g. `B738`) or `description` (e.g. `BOEING 737-800`). |
| `flicker_enabled`  | `true`            | LED flicker effect.                           |
| `flicker_strength` | `0.1`             | Flicker amount, from 0 to 0.4.                |
| `host`, `port`     | `0.0.0.0`, `8000` | Address the server listens on.                |
| `log_level`        | `"INFO"`          | Python logging level, for every part of the app. |

The app will not start if `config.toml` contains an unknown setting or an invalid value. The error message lists each problem by setting name.

#### Overriding settings with environment variables

Any setting can be overridden at run time with an environment variable. Use the `IPS_` prefix and upper case:

```sh
IPS_RADIUS_NM=30 IPS_TIME_FORMAT=12 IPS_PORT=9000 python main.py
```

This also works for the `[swift]` and `[tfms]` settings, for example `IPS_SWIFT_PASSWORD`, `IPS_SWIFT_CA_DIR` or `IPS_TFMS_PASSWORD`.

Environment variables take precedence over `config.toml`. A misspelled `IPS_` variable stops the app with an error, just like a misspelled setting in the file.

To read the configuration from somewhere other than `config.toml` next to `main.py`, set `IN_PLANE_SIGHT_CONFIG` to the file's path.

#### Using a receiver of your own

With `source = "local"`, aircraft come from a dump1090-fa or readsb receiver's `aircraft.json` at `local_url`. The receiver reports everything it hears, so `radius_nm` does not limit it, and the aircraft type is shown only if the receiver is set up with an aircraft database.

### Run

```sh
source .venv/bin/activate
python main.py
```

Open `http://localhost:8000/` in a browser.

## Tracking a Flight

The board can follow one flight anywhere in the world instead of the aircraft overhead. Open `/track` on any device on the same network (a phone works well), enter the flight, and every display switches to it on its next refresh. Anyone who can open the page can start or stop tracking.

- Enter a flight number (`UA1234`), callsign (`UAL1234`) or tail number (`N12345`). Flight numbers from common airlines are converted to the callsign the aircraft broadcasts. Regional flights often broadcast the operating airline's callsign instead, such as `ENY3456` for an American Eagle flight, so enter that if the flight is not found. A tail number only works for aircraft that broadcast it as their callsign, which is mostly private aircraft; airliners broadcast the flight's callsign instead.
- Opening `/?track=UA1234` also starts tracking. This is handy for a bookmark or a kiosk start-up script.
- The board shows the flight with its status (waiting, on the ground, climbing, cruising, descending, signal lost or arrived). `/list` keeps showing the aircraft overhead.
- Climbing, cruising and descending describe the stage of the flight, not just the moment. Short level-offs during the climb still show as climbing. Once the descent has begun, level segments on the way down, such as holding for traffic, still show as descending. A descent has begun when the aircraft is 5,000 ft (or, for low flights, a third of its highest altitude) below its highest altitude; for airliners and other flights that have cruised above 18,000 ft, as soon as it descends below 18,000 ft; or, when route data shows it within 150 nm of its destination, as soon as it starts descending. A level segment lasting 10 minutes or more counts as a new cruise level instead, unless the flight is within 150 nm of its destination, or is an airliner or other flight that has cruised above 18,000 ft and is now level below 18,000 ft.
- `/map` shows only the tracked flight and follows it. A solid line shows the path it has flown since tracking started. The server records this path even while no page is open, so opening or reloading the map shows all of it. The free ADS-B services have no history, so the path before tracking started can't be shown. With route data enabled, dashed lines join the origin to the start of the path and the aircraft to its destination.
- Before the flight appears on ADS-B (for example, before departure), the board shows it as waiting.
- Only one flight is tracked at a time. Starting a new one replaces it.
- After a restart the board always starts as the normal board; start tracking again by hand. With route data enabled, a flight's route is remembered so it still shows when you do (see [Behaviour and Limitations](#behaviour-and-limitations)).

Tracking ends on its own, so a kiosk never stays stuck on an old flight. These settings control when:

```toml
[tracking]
arrived_minutes = 5  # stay on screen this long after landing
lost_minutes = 30    # give up after this long without ADS-B
max_hours = 24       # never track longer than this
```

Losing the signal while descending within 5 nm of an airport, and less than 1,500 ft above it, counts as a landing, because ADS-B coverage is often poor near the ground. Losing it anywhere else counts as signal lost, so a low flight that flies out of coverage is not mistaken for a landing.

Tracked flights are always looked up on adsb.fi (falling back to adsb.lol), even when `source = "local"`, because a local receiver only sees nearby aircraft.

## Route Data (Optional)

Route data is not required for the flight pages to be displayed. All information except the origin and destination airports will be visible.

However, route info is really cool! Getting access takes a few minutes, but is mostly painless. Each aircraft's origin and destination come from the FAA SWIM Flight Data Publication Service (FDPS) Flight FIXM feed, via the SWIM Cloud Distribution Service (SCDS). The routes are added to the `/aircraft` response and appear in the LED, list, and map views. This is something you will have to sign up for yourself, but it's free and takes only a few minutes to get approval.

### Signing Up for the Feed

1. Create an account on the SWIFT Portal at <https://portal.swim.faa.gov>.
2. Create a new SCDS subscription and choose the FDPS Flight data service. You can add message filters at this step (see [Choosing Message Filters](#choosing-message-filters)).
3. Sign the Service Access Agreement when the portal asks for it. The subscription is not provisioned until the agreement is in place.
4. When the subscription is active, open its details in the portal and copy the connection settings: host, message VPN, queue name, username and password.

The FAA's current instructions are at <https://www.faa.gov/air_traffic/technology/swim/swift-portal-on-boarding-instructions>. For portal or connection problems, see <https://support.swim.faa.gov>.

### Choosing Message Filters

The app only needs each flight's origin and destination, which do not change during a flight. Track messages repeat every few seconds for every airborne flight and use far more CPU than the app needs. These message types arrive only a few times per flight, so they can be subscribed for every source facility (`ALL`):

| Message type                    | Why                                       |
| ------------------------------- | ----------------------------------------- |
| Departure Information           | Adds the route when the flight takes off. |
| Flight Arrival Information      | Removes the route when the flight lands.  |
| Cancellation Information        | Removes cancelled flights.                |
| Flight Plan Update Information  | Picks up diversions and route changes.    |
| Flight Amendment Information    | Picks up diversions and route changes.    |

A flight that took off before the app started has no route until it lands (unless it took off while the app was stopped, since the queue keeps those messages), except a flight you were tracking (see below). To cover overhead flights straight after a restart, you can also add track messages for just the en route centres around you (for example `ZNY` near New York). `Track Messages - 1 Min Update` is much lighter than `Track Messages - ALL`.

### Enabling Route Data

Install the optional dependency:

```sh
uv sync --extra swift
```

Add a `[swift]` section with the subscription details at the end of `config.toml`:

```toml
[swift]
host = "tcps://ems1.swim.faa.gov:55443"
vpn = "FDPS"
queue = "your-queue-name"
username = "your-subscription-username"
password = "your-subscription-password"
# route_ttl_hours = 20
```

Route data is enabled whenever the `[swift]` section is present and the `swift` extra is installed; if the extra is missing, the app logs an error and runs without route data. `queue`, `username` and `password` are required. If any are missing or empty, the app still starts, but with route data turned off, and it logs a warning listing what to fill in. `host` and `vpn` default to the values shown.

You can leave `password` out of the file and set it with the `IPS_SWIFT_PASSWORD` environment variable instead.

The broker's certificate is checked against the Mozilla root certificates installed with the app, so there is nothing to set up. If your network inspects TLS traffic with its own root certificate, set `ca_dir` in `[swift]` to a folder holding that certificate as a `.pem` file.

### Behaviour and Limitations

- The app reads from your persistent queue. Do not run another consumer, such as the FAA Jumpstart kit, on the same queue at the same time.
- Routes are stored in memory only. The queue is persistent, though, so while the app is stopped the FAA's messages wait in it (up to its size limit) and are read when the app starts again, so flights that departed while it was down get their routes back. Those messages count as new when they are read. Flights near you get their routes back within seconds if you subscribe to track messages for your local centres.
- The one exception is a flight you track: its route is saved to `.in-plane-sight/tracked-routes.json`, so if the app restarts mid-flight and you track it again, its route still shows even though the FAA will not send it again until the flight lands. The board does not resume tracking by itself after a restart. A saved route is used only when the FAA has no route for the flight, and only for the same aircraft (by its ADS-B address), since airlines reuse flight numbers. It is deleted when the FAA reports that flight landed or cancelled (whether or not it is still being tracked; another flight with the same number does not count), or `route_ttl_hours` after it was saved, and it is not used while route data is turned off. The file is written only when a tracked flight's route is first known or changes, so it causes almost no disk writes.
- A route expires `route_ttl_hours` (default 20) after the app last read a message giving the flight's origin and destination. Looking a route up does not extend it. Expired routes are removed every minute. If you subscribe only to departure messages, keep this longer than the longest flight that may pass overhead; otherwise its route expires before it arrives.
- With `log_level = "DEBUG"`, each `/aircraft` request logs how many aircraft had a known route and how many routes are stored.
- SCDS does not provide aircraft positions. Positions always come from the ADS-B source.
- If a callsign matches more than one route, no route is shown.

## Regional Airline Data (Optional)

Regional airlines fly under their own callsigns. Some, such as Envoy (`ENY`), fly for only one airline and always show that airline's logo. Others, such as Republic Airways (`RPA`) and SkyWest (`SKW`), fly for United, Delta and American, so the callsign alone cannot tell which logo to show. The FAA's TFMS Flight Data feed reports the airline each flight flies for, and the app uses it to show the right logo.

1. On the SWIFT Portal (see [Signing Up for the Feed](#signing-up-for-the-feed)), create another SCDS subscription and choose TFMS, then the R14 Flight Data service.
2. When it is active, copy its queue name, username and password, and add them in a `[tfms]` section in `config.toml`:

```toml
[tfms]
host = "tcps://ems1.swim.faa.gov:55443"
vpn = "TFMS"
queue = "your-tfms-queue-name"
username = "your-subscription-username"
password = "your-subscription-password"
```

It needs the same `swift` extra as route data, and works like the `[swift]` section: `queue`, `username` and `password` are required, `host` and `vpn` default to the values shown, `ca_dir` is supported, and the password can be set with `IPS_TFMS_PASSWORD` instead.

- When the feed says a flight flies for another airline, the flight shows that airline's logo, never the operator's own, even for operators with a logo of their own (a Wheels Up flight flown for Delta shows Delta's). The FAA also reports codes that are not airlines, such as `XAA` or `XXX` for private flights, so a code with no icon keeps the operator's own logo (or the generic one). A flight the feed has not reported in the last hour, or reports under two different airlines at once, keeps its usual logo (or the generic one), so a wrong logo is never guessed.
- The TFMS feed cannot be filtered, so the app receives every flight in the US: up to about 1 MB/s of XML at busy times. Both feeds are compressed, which cuts that to roughly 0.2 MB/s, and the app reads only a few attributes from each message rather than parsing the XML, so it uses little CPU. It still adds up to a few hundred GB a month, which may matter on a metered connection.
- Do not run another consumer on the same queue at the same time.

## Data Use

The FAA states that SCDS data is for informational use only. It must not be used for operational or aviation safety decisions. Access is governed by the FAA Service Access Agreement, and you are responsible for keeping to its terms.
