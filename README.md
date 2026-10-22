# In-Plane-Sight

An informational display of aircraft currently overhead, using live ADS-B position data.

## Optional FAA SWIFT Routes

Origin and destination data can optionally be enriched from an FAA SWIFT/SCDS Flight FIXM queue. Without SWIFT configuration, the app runs as before and route fields are `null`. When enabled, route fields are included in the shared `/aircraft` response and appear in the LED, list, and map views.

Install the optional consumer dependency with:

```sh
uv sync --extra swift
```

Set the following values in the project `.env` file using the connection details from the SWIFT subscription. Keep the subscription username and password private:

```env
SWIFT_ENABLED=true
SWIFT_HOST=tcps://ems1.swim.faa.gov:55443
SWIFT_QUEUE=your-queue-name
SWIFT_VPN=FDPS
SWIFT_USERNAME=your-subscription-username
SWIFT_PASSWORD=your-subscription-password
SWIFT_CA_DIR=/path/to/hashed/solace-ca
ROUTE_CACHE_PATH=~/.in-plane-sight/routes.sqlite3
```

`SWIFT_CA_DIR` must contain the certificate authorities needed to validate the FAA broker's TLS certificate. Keep certificate validation enabled. The subscriber consumes the configured persistent queue; do not run the Jumpstart consumer against the same exclusive queue at the same time.

For the current `ems1.swim.faa.gov` endpoint, the server certificate chains to DigiCert Global Root G2. Include that root in the hashed CA directory and run `openssl rehash` after adding it. On macOS, it can be exported from the System Roots keychain; on Linux, use the system CA bundle or add the root to the configured directory.

The route index retains active routes for up to 4 hours and is shared by all views. SCDS does not provide ADS-B position data, so existing ADS-B providers continue to supply aircraft positions. Some FIXM messages may not match a currently visible aircraft; ambiguous callsigns are left without a route rather than guessed.

FAA SCDS data is for informational, non-operational use only and must not be used for aviation safety or operational decisions. SCDS is provided under the FAA Service Access Agreement; users are responsible for maintaining that agreement and observing its terms.
