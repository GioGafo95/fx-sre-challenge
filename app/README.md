# USD/MXN rates: application (API + frontend)

Official FIX rate (Banxico series SF43718). One container image serves the API and the web page.

## Endpoints
| Path | Purpose |
|---|---|
| `GET /` | Frontend |
| `GET /api/rates` | Current rate, 15/30-day averages, last 10 days, `stale` flag |
| `GET /healthz` | Liveness only (never touches Banxico or the DB) |

Windows are trailing calendar days (Banxico publishes business days only). `stale=true` when the newest rate is more than 4 days old.

## Run locally (no AWS, no token)
```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
PYTHONPATH=src pytest -q
FX_MODE=demo PYTHONPATH=src uvicorn fx.main:app --port 8080   # synthetic data, labelled "demo"
# real data: FX_MODE=live BANXICO_TOKEN=<token> ...            # token is free: banxico.org.mx/SieAPIRest
```

## Container image
CDK builds the image (linux/arm64, cheaper on Lambda) and pushes it to the bootstrap ECR repository during deploy, so
there is nothing to push by hand. To try the image locally:
```bash
docker build -t fx-rates .
docker run --rm -p 8080:8080 -e FX_MODE=demo fx-rates
```

## Configuration (environment variables)
`TABLE_NAME` (DynamoDB table; absent = in-memory), `TOKEN_SECRET_ARN` (Secrets Manager, ingestion only),
`FX_MODE` (`demo`/`live`, local only), `SERVE_WEB` (default `1`).
