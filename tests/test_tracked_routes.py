import json

import pytest

from sources.tracked_routes import TrackedRoutes


@pytest.fixture
def clock():
    return [1794760930.0]  # 2026-11-15 16:42:10 UTC


def make_store(path, clock, ttl_seconds=3600):
    return TrackedRoutes(path, ttl_seconds=ttl_seconds, clock=lambda: clock[0])


def test_routes_survive_a_restart_for_the_same_aircraft(tmp_path, clock):
    path = tmp_path / 'tracked-routes.json'
    make_store(path, clock).remember('dal 2206', 'AB23B5', 'KATL', 'KPDX')

    restarted = make_store(path, clock)
    assert restarted.get('DAL2206', 'ab23b5') == ('KATL', 'KPDX')
    assert restarted.get('DAL2206', 'a00001') is None  # another aircraft
    assert restarted.get('UAL1', 'ab23b5') is None


def test_the_file_has_one_route_per_line(tmp_path, clock):
    path = tmp_path / 'tracked-routes.json'
    store = make_store(path, clock)
    store.remember('UAL2', 'a00002', 'KEWR', 'KSFO')
    store.remember('DAL1', 'a00001', 'KATL', 'KPDX')
    assert path.read_text() == (
        '{\n'
        '  "routes": [\n'
        '    {"callsign": "DAL1", "icao": "a00001", "origin": "KATL", '
        '"destination": "KPDX", "saved_at": "2026-11-15T16:42:10Z"},\n'
        '    {"callsign": "UAL2", "icao": "a00002", "origin": "KEWR", '
        '"destination": "KSFO", "saved_at": "2026-11-15T16:42:10Z"}\n'
        '  ]\n'
        '}\n'
    )


def test_writes_only_when_a_route_is_new_or_changes(
    tmp_path, clock, monkeypatch
):
    store = make_store(tmp_path / 'tracked-routes.json', clock)
    writes = []
    save = store._save

    def counting_save(now):
        writes.append(now)
        save(now)

    monkeypatch.setattr(store, '_save', counting_save)
    store.remember('DAL1', 'a00001', 'KATL', 'KPDX')
    clock[0] += 15
    store.remember('DAL1', 'a00001', 'KATL', 'KPDX')
    assert len(writes) == 1
    store.remember('DAL1', 'a00001', 'KATL', 'KJAX')  # diverted
    assert len(writes) == 2
    assert store.get('DAL1', 'a00001') == ('KATL', 'KJAX')


def test_switching_flights_keeps_earlier_routes(tmp_path, clock):
    store = make_store(tmp_path / 'tracked-routes.json', clock)
    store.remember('DAL1', 'a00001', 'KATL', 'KPDX')
    store.remember('UAL2', 'a00002', 'KEWR', 'KSFO')
    assert store.get('DAL1', 'a00001') == ('KATL', 'KPDX')


def test_forget_removes_the_saved_route(tmp_path, clock):
    path = tmp_path / 'tracked-routes.json'
    store = make_store(path, clock)
    store.remember('DAL1', 'a00001', 'KATL', 'KPDX')
    store.remember('UAL2', 'a00002', 'KEWR', 'KSFO')
    store.forget('dal1')
    assert make_store(path, clock).get('DAL1', 'a00001') is None
    assert make_store(path, clock).get('UAL2', 'a00002') == ('KEWR', 'KSFO')


def test_forget_with_airports_removes_only_a_matching_route(tmp_path, clock):
    path = tmp_path / 'tracked-routes.json'
    store = make_store(path, clock)
    store.remember('SWA42', 'a00001', 'KPHL', 'KDEN')
    store.forget('SWA42', 'KDEN', 'KLAX')  # the next leg
    store.forget('SWA42', None, 'KLAX')
    assert make_store(path, clock).get('SWA42', 'a00001') == ('KPHL', 'KDEN')

    store.forget('SWA42', 'kphl', None)
    assert make_store(path, clock).get('SWA42', 'a00001') is None


def test_routes_expire_and_are_dropped_from_the_file(tmp_path, clock):
    path = tmp_path / 'tracked-routes.json'
    store = make_store(path, clock)
    store.remember('DAL1', 'a00001', 'KATL', 'KPDX')
    clock[0] += 3599
    assert store.get('DAL1', 'a00001') == ('KATL', 'KPDX')
    clock[0] += 1
    assert store.get('DAL1', 'a00001') is None
    assert make_store(path, clock).get('DAL1', 'a00001') is None

    store.remember('UAL2', 'a00002', 'KEWR', 'KSFO')
    callsigns = [r['callsign'] for r in json.loads(path.read_text())['routes']]
    assert callsigns == ['UAL2']


def test_saved_times_in_the_future_count_as_now(tmp_path, clock):
    path = tmp_path / 'tracked-routes.json'
    make_store(path, clock).remember('DAL1', 'a00001', 'KATL', 'KPDX')
    clock[0] -= 86400  # the clock went backwards
    store = make_store(path, clock)
    clock[0] += 3600
    assert store.get('DAL1', 'a00001') is None


def test_missing_or_damaged_files_start_empty(tmp_path, clock, caplog):
    assert make_store(tmp_path / 'missing.json', clock).get('A', 'b') is None
    damaged = tmp_path / 'damaged.json'
    damaged.write_text('{"routes": [{"callsign": "DAL1"')
    assert make_store(damaged, clock).get('DAL1', 'a00001') is None
    assert 'Ignoring saved routes' in caplog.text


def test_a_failed_write_keeps_the_route_in_memory(tmp_path, clock, caplog):
    blocked = tmp_path / 'blocked'
    blocked.mkdir()
    store = make_store(blocked, clock)
    store.remember('DAL1', 'a00001', 'KATL', 'KPDX')
    assert store.get('DAL1', 'a00001') == ('KATL', 'KPDX')
    assert 'Could not save routes' in caplog.text
