# In-Plane-Sight

Show the aircraft flying overhead, updating in real time.

![main page](imgs/main.png)

There are three browser views: the main page (shown above), a list (below), and
a live map.

| Path    | View                        |
| ------- | --------------------------- |
| `/`     | LED-style information board |
| `/list` | List of aircraft            |
| `/map`  | Map with aircraft positions |

![list](imgs/list.png)

<img src="imgs/phone.png" width="250" alt="Description">

It also looks great on a phone!

By default, aircraft positions come from adsb.fi, with adsb.lol as a fallback. No account or API key is needed.

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
| `source`           | `"internet"`      | ADS-B source. `local` is not yet implemented. |
| `refresh_seconds`  | `15`              | How often aircraft data is refreshed.         |
| `time_format`      | `"24"`            | `"12"` or `"24"` hour clock.                  |
| `font_thickness`   | `"normal"`        | LED font weight: `normal` or `broad`.         |
| `aircraft_type_format` | `"code"`      | `code` (e.g. `B738`) or `description` (e.g. `BOEING 737-800`). |
| `flicker_enabled`  | `true`            | LED flicker effect.                           |
| `flicker_strength` | `0.1`             | Flicker amount, from 0 to 0.4.                |
| `host`, `port`     | `0.0.0.0`, `8000` | Address the server listens on.                |
| `log_level`        | `"INFO"`          | Python logging level.                         |

The app will not start if `config.toml` contains an unknown setting or an invalid value. The error message lists each problem by setting name.

#### Overriding settings with environment variables

Any setting can be overridden at run time with an environment variable. Use the `IPS_` prefix and upper case:

```sh
IPS_RADIUS_NM=30 IPS_TIME_FORMAT=12 IPS_PORT=9000 python main.py
```

This also works for the `[swift]` settings, for example `IPS_SWIFT_PASSWORD` or `IPS_SWIFT_CA_DIR`.

Environment variables take precedence over `config.toml`.

### Run

```sh
source .venv/bin/activate
python main.py
```

Open `http://localhost:8000/` in a browser.

### Tests

```sh
uv run pytest
```

## Route Data (Optional)

Route data is not required for the flight pages to be displayed. All information except the origin and destination airports will be visible.

However, route info is really cool! Getting access takes a few minutes, but is mostly painless. Each aircraft's origin and destination come from the FAA SWIM Flight Data Publication Service (FDPS) Flight FIXM feed, via the SWIM Cloud Distribution Service (SCDS). The routes are added to the `/aircraft` response and appear in the LED, list, and map views. This is something you will have to sign up for yourself, but it's free and takes only a few minutes to get approval.

### Signing Up for the Feed

1. Create an account on the SWIFT Portal at <https://portal.swim.faa.gov>.
2. Create a new SCDS subscription and choose the FDPS Flight data service. You can add message filters at this step.
3. Sign the Service Access Agreement when the portal asks for it. The subscription is not provisioned until the agreement is in place.
4. When the subscription is active, open its details in the portal and copy the connection settings: host, message VPN, queue name, username and password.

The FAA's current instructions are at <https://www.faa.gov/air_traffic/technology/swim/swift-portal-on-boarding-instructions>. For portal or connection problems, see <https://support.swim.faa.gov>.

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
```

Route data is enabled whenever the `[swift]` section is present. `queue`, `username` and `password` are required. If any are missing or empty, the app still starts, but with route data turned off, and it logs a warning listing what to fill in. `host` and `vpn` default to the values shown.

You can leave `password` out of the file and set it with the `IPS_SWIFT_PASSWORD` environment variable instead.

The broker's certificate is checked against the Mozilla root certificates installed with the app, so there is nothing to set up. If your network inspects TLS traffic with its own root certificate, set `ca_dir` in `[swift]` to a folder holding that certificate as a `.pem` file.

### Behaviour and Limitations

- The app reads from your persistent queue. Do not run another consumer, such as the FAA Jumpstart kit, on the same queue at the same time.
- Routes are stored in memory only. The store is empty after a restart and fills as messages arrive.
- A route expires four hours after it was last updated or looked up. Expired routes are removed every minute.
- SCDS does not provide aircraft positions. Positions always come from the ADS-B source.
- If a callsign matches more than one route, no route is shown.

## Data Use

The FAA states that SCDS data is for informational use only. It must not be used for operational or aviation safety decisions. Access is governed by the FAA Service Access Agreement, and you are responsible for keeping to its terms.
