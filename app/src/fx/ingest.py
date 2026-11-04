"""Scheduled ingestion (EventBridge -> Lambda). Idempotent upserts; raises on failure so retries/DLQ/alarms fire."""
import json
import os
import time
from datetime import timedelta

from . import banxico, store
from .clock import today_mx

_token_cache: str | None = None


def _token() -> str:
    global _token_cache
    if _token_cache is None:
        if os.environ.get("BANXICO_TOKEN"):
            _token_cache = os.environ["BANXICO_TOKEN"]
        else:
            import boto3
            sm = boto3.client("secretsmanager")
            _token_cache = sm.get_secret_value(SecretId=os.environ["TOKEN_SECRET_ARN"])["SecretString"]
    return _token_cache


def _emf(namespace: str, metrics: dict[str, float]) -> None:
    """CloudWatch Embedded Metric Format: metrics emitted via a log line, no API calls/cost."""
    print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
        "Namespace": namespace, "Dimensions": [[]],
        "Metrics": [{"Name": k, "Unit": "Count" if k == "IngestedRecords" else "None"} for k in metrics]}]},
        **metrics}))


def handler(event=None, context=None):
    today = today_mx()
    rows = banxico.fetch_range(_token(), today - timedelta(days=45), today)
    if not rows:
        raise RuntimeError("Banxico returned no data")
    store.from_env().put_many(rows)
    age = (today - max(rows)).days
    _emf("FxRates", {"IngestedRecords": len(rows), "LatestDataAgeDays": age})
    return {"ingested": len(rows), "latest": max(rows).isoformat()}
