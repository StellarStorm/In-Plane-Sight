import os
from pathlib import Path

# Isolate the tests from the developer's own config.toml and IPS_* variables.
# This must happen before `main` is imported, since it loads the config.
for name in list(os.environ):
    if name.upper().startswith('IPS_'):
        del os.environ[name]
os.environ['IN_PLANE_SIGHT_CONFIG'] = str(
    Path(__file__).resolve().parent / 'no-such-config.toml'
)

import pytest

import main
from sources.tracked_routes import TrackedRoutes


@pytest.fixture(autouse=True)
def tracked_routes(tmp_path, monkeypatch):
    """Keep tests away from the real saved routes in .in-plane-sight."""
    routes = TrackedRoutes(tmp_path / 'tracked-routes.json', ttl_seconds=3600)
    monkeypatch.setattr(main, 'tracked_routes', routes)
    return routes
