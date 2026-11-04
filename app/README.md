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

## Build and push to ECR (repeat per account/region)
```bash
export AWS_PROFILE=<profile> AWS_REGION=<region>
ACCT=$(aws sts get-caller-identity --query Account --output text)
REPO=fx-rates
aws ecr describe-repositories --repository-names $REPO >/dev/null 2>&1 || \
  aws ecr create-repository --repository-name $REPO \
    --image-scanning-configuration scanOnPush=true --image-tag-mutability IMMUTABLE
aws ecr get-login-password | docker login --username AWS --password-stdin $ACCT.dkr.ecr.$AWS_REGION.amazonaws.com
docker buildx build --platform linux/arm64 -t $ACCT.dkr.ecr.$AWS_REGION.amazonaws.com/$REPO:$(git rev-parse --short HEAD) --push .
```
arm64 (Graviton) is cheaper on Lambda and Fargate. The CDK stack will manage the repository itself; the commands above are for manual testing.

## Configuration (environment variables)
`TABLE_NAME` (DynamoDB table; absent = in-memory), `TOKEN_SECRET_ARN` (Secrets Manager, ingestion only),
`FX_MODE` (`demo`/`live`, local only), `SERVE_WEB` (default `1`).
