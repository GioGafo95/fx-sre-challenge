"""Banxico SIE REST client. Series SF43718 = USD/MXN FIX exchange rate."""
import json
import urllib.request
from datetime import date, datetime
from decimal import Decimal

BASE = "https://www.banxico.org.mx/SieAPIRest/service/v1/series"
SERIES = "SF43718"


def fetch_range(token: str, start: date, end: date, series: str = SERIES,
                timeout: float = 8.0) -> dict[date, Decimal]:
    """Return {date: rate}. Raises on network/HTTP/shape errors so callers can retry/alarm."""
    url = f"{BASE}/{series}/datos/{start:%Y-%m-%d}/{end:%Y-%m-%d}"
    req = urllib.request.Request(url, headers={"Bmx-Token": token, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 (fixed https URL)
        payload = json.load(resp)
    out: dict[date, Decimal] = {}
    for row in payload["bmx"]["series"][0].get("datos", []):
        if row.get("dato") in (None, "N/E"):
            continue
        out[datetime.strptime(row["fecha"], "%d/%m/%Y").date()] = Decimal(row["dato"]).quantize(Decimal("0.0001"))
    return out
