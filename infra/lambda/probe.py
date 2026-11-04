"""Synthetic probe: measures what a real visitor gets, through CloudFront. Runs every few minutes.
Emits CloudWatch Embedded Metric Format lines (no PutMetricData calls, no extra IAM)."""
import json
import os
import time
import urllib.request

NAMESPACE = "FxRates/Probe"


def _emf(metrics: dict) -> None:
    print(json.dumps({"_aws": {"Timestamp": int(time.time() * 1000), "CloudWatchMetrics": [{
        "Namespace": NAMESPACE, "Dimensions": [[]],
        "Metrics": [{"Name": k, "Unit": "Milliseconds" if k == "LatencyMs" else "None"} for k in metrics]}]},
        **metrics}))


def handler(event=None, context=None):
    url = os.environ["TARGET_URL"]
    ok, stale, started = 0, 0, time.monotonic()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "fx-synthetic-probe/1"})
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 (https URL from our own stack)
            body = json.load(resp)
            # "Good" = HTTP 200 AND a well-formed payload with a current rate (an empty 200 is still an outage).
            ok = int(resp.status == 200 and body.get("available") is True and bool(body["current"]["rate"]))
            stale = int(bool(body.get("stale")))
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"level": "ERROR", "msg": "probe_failed", "error": type(exc).__name__}))
    _emf({"Availability": ok, "LatencyMs": round((time.monotonic() - started) * 1000), "DataStale": stale})
    return {"ok": ok}
