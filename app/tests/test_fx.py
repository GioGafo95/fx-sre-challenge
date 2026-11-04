from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from fx.service import build_report
from fx.store import MemoryStore

TODAY = date(2026, 11, 4)


def seeded():
    s = MemoryStore()
    s.put_many({TODAY - timedelta(days=i): Decimal("18.0000") + Decimal(i) / 100 for i in range(0, 31)})
    return s


def test_report_windows():
    r = build_report(seeded(), TODAY)
    assert r["current"] == {"date": "2026-11-04", "rate": "18.0000"}
    assert len(r["history"]) == 10 and r["history"][0]["date"] == "2026-11-04"
    assert r["average"]["last_15_days"] == "18.0700"   # offsets 0..14 -> mean 0.07
    assert r["average"]["last_30_days"] == "18.1450"   # offsets 0..29 -> mean 0.145
    assert r["stale"] is False


def test_stale_and_empty():
    s = MemoryStore()
    assert build_report(s, TODAY)["available"] is False
    s.put_many({TODAY - timedelta(days=6): Decimal("17.5")})
    assert build_report(s, TODAY)["stale"] is True


def test_api_demo(monkeypatch):
    monkeypatch.setenv("FX_MODE", "demo")
    monkeypatch.delenv("TABLE_NAME", raising=False)
    from fx.main import app
    with TestClient(app) as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        r = c.get("/api/rates")
        assert r.status_code == 200 and "stale-if-error" in r.headers["cache-control"]
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.json()["source"] == "demo"
        assert c.get("/").status_code == 200
