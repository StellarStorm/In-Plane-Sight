# In-Plane-Sight

In-Plane-Sight shows the aircraft currently flying near a chosen location. It uses live ADS-B position data. There are three browser views:

| Path    | View                        |
| ------- | --------------------------- |
| `/`     | LED-style information board |
| `/list` | List of aircraft            |
| `/map`  | Map with aircraft positions |

By default, aircraft positions come from adsb.lol, with adsb.fi as a fallback. No account or API key is needed.

## Getting Started

### Requirements

- Python 3.13 or newer
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
cp .env.example .env
```

At minimum, set your location and search radius:

```env
LAT=40.7128
LON=-74.0060
RADIUS_NM=20
```

| Setting            | Default           | Description                                   |
| ------------------ | ----------------- | --------------------------------------------- |
| `SOURCE`           | `internet`        | ADS-B source. `local` is not yet implemented. |
| `LAT`, `LON`       | New York City     | Center of the search area.                    |
| `RADIUS_NM`        | `20`              | Search radius in nautical miles.              |
| `REFRESH_SECONDS`  | `15`              | How often aircraft data is refreshed.         |
| `TIME_FORMAT`      | `24`              | `12` or `24` hour clock.                      |
| `FONT_THICKNESS`   | `normal`          | LED font weight: `normal` or `broad`.         |
| `FLICKER_ENABLED`  | `true`            | LED flicker effect.                           |
| `FLICKER_STRENGTH` | `0.025`           | Flicker amount, clamped to 0 to 0.1.          |
| `HOST`, `PORT`     | `0.0.0.0`, `8000` | Address the server listens on.                |
| `LOG_LEVEL`        | `INFO`            | Python logging level.                         |

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

You don't need route data. If it isn't configured, the app works normally and the origin and destination fields are `null`.

If it is enabled, each aircraft's origin and destination come from the FAA SWIM Flight Data Publication Service (FDPS) Flight FIXM feed, via the SWIM Cloud Distribution Service (SCDS). The routes are added to the `/aircraft` response and appear in the LED, list, and map views.

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

Add the subscription details to `.env`:

```env
SWIFT_ENABLED=true
SWIFT_HOST=tcps://ems1.swim.faa.gov:55443
SWIFT_QUEUE=your-queue-name
SWIFT_VPN=FDPS
SWIFT_USERNAME=your-subscription-username
SWIFT_PASSWORD=your-subscription-password
SWIFT_CA_DIR=/path/to/hashed/ca-dir
```

When `SWIFT_ENABLED=true`, every `SWIFT_*` setting is required, and the app will not start if any are missing. Keep `.env` out of version control.

`SWIFT_CA_DIR` must be a hashed directory of certificate authorities that can validate the broker's TLS certificate. The `ems1.swim.faa.gov` certificate currently chains to DigiCert Global Root G2. Put that root certificate in the directory, then run:

```sh
openssl rehash /path/to/hashed/ca-dir
```

On macOS, you can export the root from the System Roots keychain. On Linux, it is usually in the system CA bundle. Do not turn off certificate validation.

### Behaviour and Limitations

- The app reads from your persistent queue. Do not run another consumer, such as the FAA Jumpstart kit, on the same queue at the same time.
- Routes are stored in memory only. The store is empty after a restart and fills as messages arrive.
- A route expires four hours after it was last updated or looked up. Expired routes are removed every minute.
- SCDS does not provide aircraft positions. Positions always come from the ADS-B source.
- If a callsign matches more than one route, no route is shown.

## Data Use

The FAA states that SCDS data is for informational use only. It must not be used for operational or aviation safety decisions. Access is governed by the FAA Service Access Agreement, and you are responsible for keeping to its terms.
