from datetime import datetime, timedelta

import main
from conftest import FakeRedis, FakeResult


def test_health_ok(client, fake_session):
    fake_session.queue(FakeResult())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.get_json()["checks"] == {"database": "ok", "redis": "ok"}


def test_list_terminals_uses_30_second_cache(client, fake_session):
    fake_session.queue(
        FakeResult(
            [
                {
                    "tid": "T0101001",
                    "mid": "MID000101",
                    "hardware_model": "Desk2600",
                    "software_version": "12.4.0",
                    "enabled": 1,
                    "last_call_stamp": datetime(2026, 7, 10, 12, 0, 0),
                }
            ]
        )
    )

    first = client.get("/terminals")
    second = client.get("/terminals")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.get_json() == second.get_json()
    redis_client = FakeRedis.instances[-1]
    assert redis_client.ttls["tms:terminals:list:enabled:None"] == 30
    assert len(fake_session.calls) == 1


def test_list_terminals_rejects_invalid_enabled(client):
    response = client.get("/terminals?enabled=yes")

    assert response.status_code == 400
    assert response.get_json() == {"error": "enabled must be true or false"}


def test_terminal_details_404(client, fake_session):
    fake_session.queue(FakeResult([]))

    response = client.get("/terminals/NOPE")

    assert response.status_code == 404
    assert response.get_json() == {"error": "terminal not found"}


def test_flag_requires_scenario_number(client):
    response = client.post("/terminals/T0101001/flag", json={})

    assert response.status_code == 400
    assert response.get_json() == {"error": "scenario_number is required"}


def test_flag_updates_terminal_and_clears_cache(client, fake_session):
    redis_client = FakeRedis.instances[-1]
    redis_client.store["tms:terminals:list:enabled:None"] = "[]"
    fake_session.queue(FakeResult([{"scenario_number": "0"}]), FakeResult())

    response = client.post("/terminals/T0101001/flag", json={"scenario_number": "5"})

    assert response.status_code == 200
    assert response.get_json() == {"tid": "T0101001", "scenario_number": "5"}
    assert fake_session.commit_count == 1
    assert "tms:terminals:list:enabled:None" in redis_client.deleted


def test_decommission_conflict_for_disabled_terminal(client, fake_session):
    fake_session.queue(FakeResult([{"enabled": 0}]))

    response = client.post("/terminals/T0101001/decommission")

    assert response.status_code == 409
    assert response.get_json() == {"error": "terminal already decommissioned"}
    assert fake_session.rollback_count == 1


def test_templates_list_and_detail(client, fake_session):
    fake_session.queue(
        FakeResult(
            [
                {
                    "id": 1,
                    "template_name": "Restaurant Template",
                    "hardware_model": "Desk2600",
                    "hardware_family": "Android",
                }
            ]
        ),
        FakeResult(
            [
                {
                    "id": 1,
                    "template_name": "Restaurant Template",
                    "hardware_model": "Desk2600",
                    "hardware_family": "Android",
                }
            ]
        ),
    )

    list_response = client.get("/templates")
    detail_response = client.get("/templates/1")

    assert list_response.status_code == 200
    assert detail_response.status_code == 200
    assert detail_response.get_json()["template_name"] == "Restaurant Template"


def test_create_terminal_from_template_success(client, fake_session):
    fake_session.queue(
        FakeResult([{"id": 1, "hardware_model": "Desk2600", "hardware_family": "Android"}]),
        FakeResult([{"id": 1, "mid": "MID000101"}]),
        FakeResult(["T0101001", "T0101006"]),
        FakeResult(),
    )

    response = client.post("/terminals/from-template", json={"template_id": 1, "mid": "MID000101"})

    assert response.status_code == 201
    assert response.get_json() == {"tid": "T0101007"}
    assert fake_session.commit_count == 1


def test_create_terminal_from_template_missing_template(client, fake_session):
    fake_session.queue(FakeResult([]))

    response = client.post("/terminals/from-template", json={"template_id": 999, "mid": "MID000101"})

    assert response.status_code == 404
    assert response.get_json() == {"error": "template not found"}


def test_statistics_by_state_uses_pandas_and_cache(client, fake_session):
    fake_session.queue(FakeResult([{"enabled": 1}, {"enabled": 1}, {"enabled": 0}]))

    response = client.get("/statistics/by-state")

    assert response.status_code == 200
    body = response.get_json()
    assert body["active"] == 2
    assert body["inactive"] == 1
    assert body["total"] == 3
    assert "generated_at" in body
    assert FakeRedis.instances[-1].ttls["tms:statistics:by-state"] == 60


def test_statistics_by_hardware_groups_with_pandas(client, fake_session):
    fake_session.queue(
        FakeResult(
            [
                {"hardware_model": "Desk2600"},
                {"hardware_model": "Desk2600"},
                {"hardware_model": "A920"},
            ]
        )
    )

    response = client.get("/statistics/by-hardware")

    assert response.status_code == 200
    assert response.get_json()["data"] == [
        {"hardware_model": "Desk2600", "count": 2},
        {"hardware_model": "A920", "count": 1},
    ]


def test_idle_distribution_counts_all_buckets(client, fake_session, monkeypatch):
    class FixedTimestamp(main.pd.Timestamp):
        @classmethod
        def now(cls, tz=None):
            return main.pd.Timestamp("2026-07-10T12:00:00")

    monkeypatch.setattr(main.pd, "Timestamp", FixedTimestamp)
    fake_session.queue(
        FakeResult(
            [
                {"last_call_stamp": datetime(2026, 7, 10, 8, 0, 0)},
                {"last_call_stamp": datetime(2026, 7, 8, 8, 0, 0)},
                {"last_call_stamp": datetime(2026, 6, 20, 8, 0, 0)},
                {"last_call_stamp": datetime(2026, 5, 1, 8, 0, 0)},
                {"last_call_stamp": datetime(2026, 1, 1, 8, 0, 0)},
                {"last_call_stamp": None},
            ]
        )
    )

    response = client.get("/statistics/idle-distribution")

    assert response.status_code == 200
    assert response.get_json()["data"] == [
        {"range": "Σήμερα", "count": 1},
        {"range": "1-7 μέρες", "count": 1},
        {"range": "8-30 μέρες", "count": 1},
        {"range": "31-90 μέρες", "count": 1},
        {"range": "90+ μέρες", "count": 2},
    ]
