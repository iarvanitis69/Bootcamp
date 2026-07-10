import json
import sys
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parents[1] / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import main


class FakeScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class FakeResult:
    def __init__(self, rows=None, scalar_value=None):
        self.rows = rows or []
        self.scalar_value = scalar_value

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self.rows)

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None

    def scalar(self):
        return self.scalar_value

    def scalars(self):
        return FakeScalarResult(self.rows)


class FakeSession:
    def __init__(self):
        self.results = []
        self.calls = []
        self.commit_count = 0
        self.rollback_count = 0

    def queue(self, *results):
        self.results.extend(results)

    def execute(self, query, params=None):
        self.calls.append((str(query), params or {}))
        if not self.results:
            raise AssertionError(f"Unexpected query: {query}")
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def commit(self):
        self.commit_count += 1

    def rollback(self):
        self.rollback_count += 1

    def remove(self):
        pass


class FakeRedis:
    instances = []

    def __init__(self, *args, **kwargs):
        self.store = {}
        self.ttls = {}
        self.deleted = []
        self.raise_on_get = False
        FakeRedis.instances.append(self)

    def get(self, key):
        if self.raise_on_get:
            raise RuntimeError("redis down")
        return self.store.get(key)

    def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

    def scan_iter(self, match):
        prefix = match.rstrip("*")
        return [key for key in self.store if key.startswith(prefix)]

    def delete(self, *keys):
        self.deleted.extend(keys)
        for key in keys:
            self.store.pop(key, None)
            self.ttls.pop(key, None)

    def ping(self):
        return True


@pytest.fixture
def fake_session(monkeypatch):
    session = FakeSession()
    monkeypatch.setattr(main.db, "session", session)
    return session


@pytest.fixture
def client(monkeypatch, fake_session):
    monkeypatch.setenv("MYSQL_USER", "user")
    monkeypatch.setenv("MYSQL_PASSWORD", "pass")
    monkeypatch.setenv("MYSQL_DATABASE", "tms")
    monkeypatch.setattr(main, "ensure_schema", lambda: None)
    FakeRedis.instances.clear()
    monkeypatch.setattr(main.redis, "Redis", FakeRedis)
    app = main.create_app()
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def fake_redis():
    redis_client = FakeRedis()
    redis_client.store["tms:one"] = json.dumps({"ok": True})
    redis_client.store["other:two"] = json.dumps({"ok": False})
    return redis_client
