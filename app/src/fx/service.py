"""Pure business logic. Windows are trailing CALENDAR days; Banxico publishes on business days only."""
from datetime import date, timedelta
from decimal import Decimal

STALE_AFTER_DAYS = 4  # tolerates a long weekend before flagging data as stale


def _avg(vals: list[Decimal]) -> str | None:
    return str((sum(vals) / len(vals)).quantize(Decimal("0.0001"))) if vals else None


def build_report(store, today: date, source: str = "banxico") -> dict:
    window = store.range(today - timedelta(days=30), today)
    if not window:
        return {"pair": "USD/MXN", "source": source, "available": False}
    ordered = sorted(window.items(), reverse=True)  # newest first
    latest_date, latest_rate = ordered[0]

    def since(days: int) -> list[Decimal]:
        return [r for d, r in window.items() if d > today - timedelta(days=days)]

    return {
        "pair": "USD/MXN",
        "source": source,
        "series": "SF43718",
        "available": True,
        "as_of": latest_date.isoformat(),
        "stale": (today - latest_date).days > STALE_AFTER_DAYS,
        "current": {"date": latest_date.isoformat(), "rate": str(latest_rate)},
        "average": {"last_15_days": _avg(since(15)), "last_30_days": _avg(since(30))},
        "history": [{"date": d.isoformat(), "rate": str(r)} for d, r in ordered
                    if d > today - timedelta(days=10)],
    }
