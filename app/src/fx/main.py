import logging
import os
import random
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import banxico, store as store_mod
from .clock import today_mx
from .service import build_report

log = logging.getLogger("fx")
WEB_DIR = Path(os.environ.get("WEB_DIR", Path(__file__).resolve().parents[2] / "web"))
# CloudFront honours stale-while-revalidate / stale-if-error: the API keeps answering during origin trouble.
CACHE = "public, max-age=300, stale-while-revalidate=3600, stale-if-error=86400"


def _seed_demo(st) -> None:
    """Deterministic synthetic data for local development ONLY (FX_MODE=demo)."""
    rnd, rate, today, rows = random.Random(7), Decimal("17.80"), today_mx(), {}
    for i in range(45, -1, -1):
        d = today - timedelta(days=i)
        rate += Decimal(rnd.uniform(-0.12, 0.12)).quantize(Decimal("0.0001"))
        if d.weekday() < 5:
            rows[d] = rate.quantize(Decimal("0.0001"))
    st.put_many(rows)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = store_mod.from_env()
    app.state.source = "banxico"
    mode = os.environ.get("FX_MODE", "")
    if mode == "demo":
        _seed_demo(app.state.store)
        app.state.source = "demo"
    elif mode == "live":  # local dev: pull straight from Banxico into memory
        t = today_mx()
        app.state.store.put_many(banxico.fetch_range(os.environ["BANXICO_TOKEN"], t - timedelta(days=45), t))
    yield


app = FastAPI(title="USD/MXN rates", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp: Response = await call_next(request)
    resp.headers.update({
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
        "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'",
    })
    return resp


@app.get("/healthz", include_in_schema=False)
def healthz():
    return {"status": "ok"}  # liveness only: never depends on Banxico or the database


@app.get("/api/rates")
def rates(request: Request):
    try:
        report = build_report(request.app.state.store, today_mx(), request.app.state.source)
    except Exception:  # noqa: BLE001 - never leak internals; the failure is logged and alarmed on
        log.exception("store_read_failed")
        return JSONResponse({"error": "temporarily_unavailable"}, status_code=503,
                            headers={"Cache-Control": "no-store"})
    if not report["available"]:
        return JSONResponse(report, status_code=503, headers={"Cache-Control": "no-store"})
    return JSONResponse(report, headers={"Cache-Control": CACHE})


if os.environ.get("SERVE_WEB", "1") == "1" and WEB_DIR.exists():
    # Mounted last so the /api and /healthz routes above take precedence.
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
