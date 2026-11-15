import pytest

import main
from sources.tracked_routes import TrackedRoutes


@pytest.fixture(autouse=True)
def tracked_routes(tmp_path, monkeypatch):
    """Keep tests away from the real saved routes in .in-plane-sight."""
    routes = TrackedRoutes(tmp_path / 'tracked-routes.json', ttl_seconds=3600)
    monkeypatch.setattr(main, 'tracked_routes', routes)
    return routes
