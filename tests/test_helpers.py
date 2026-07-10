import json
from datetime import date, datetime

import main


def test_serialize_value_converts_dates():
    assert main.serialize_value(date(2026, 7, 10)) == "2026-07-10"
    assert main.serialize_value(datetime(2026, 7, 10, 12, 30, 5)) == "2026-07-10T12:30:05"
    assert main.serialize_value("value") == "value"


def test_terminal_list_row_maps_public_shape():
    row = {
        "tid": "T1",
        "mid": "MID1",
        "hardware_model": "Desk2600",
        "software_version": "12.4.0",
        "enabled": 1,
        "last_call_stamp": datetime(2026, 7, 10, 12, 0, 0),
    }

    assert main.terminal_list_row(row) == {
        "tid": "T1",
        "mid": "MID1",
        "hardware_model": "Desk2600",
        "software_version": "12.4.0",
        "enabled": True,
        "last_call": "2026-07-10T12:00:00",
    }


def test_cache_helpers_hit_miss_and_clear(fake_redis):
    assert main.cache_key("terminals") == "tms:terminals"
    assert main.get_cached_json(fake_redis, "missing") is None
    assert main.get_cached_json(fake_redis, "tms:one") == {"ok": True}

    main.set_cached_json(fake_redis, "tms:new", {"value": 1}, ttl=30)
    assert json.loads(fake_redis.store["tms:new"]) == {"value": 1}
    assert fake_redis.ttls["tms:new"] == 30

    main.clear_cache(fake_redis)
    assert "tms:one" not in fake_redis.store
    assert "tms:new" not in fake_redis.store
    assert "other:two" in fake_redis.store


def test_cache_read_failure_returns_none(fake_redis):
    fake_redis.raise_on_get = True

    assert main.get_cached_json(fake_redis, "tms:any") is None
