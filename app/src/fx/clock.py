from datetime import date, datetime, timedelta, timezone


def today_mx() -> date:
    """Mexico City date. Mexico abolished DST in 2022 (UTC-6 year-round), so the fixed
    offset is a safe fallback when the runtime has no tz database (e.g. minimal images)."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Mexico_City")).date()
    except Exception:  # noqa: BLE001
        return datetime.now(timezone(timedelta(hours=-6))).date()
