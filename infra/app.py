#!/usr/bin/env python3
"""Entry point. Account/region come from the active AWS credentials, so the same code deploys to any account.

Region precedence: `-c region=...` > CDK_DEFAULT_REGION (profile region) > us-east-2.
"""
import os

import aws_cdk as cdk

from fx_infra.app_stack import AppStack
from fx_infra.data_stack import DataStack
from fx_infra.observability_stack import ObservabilityStack

app = cdk.App()


def ctx(key: str):
    return app.node.try_get_context(key) or None


env_name = ctx("env_name") or "prod"
env = cdk.Environment(
    account=os.getenv("CDK_DEFAULT_ACCOUNT"),
    region=ctx("region") or os.getenv("CDK_DEFAULT_REGION") or "us-east-2",
)
prefix = f"fx-{env_name}"

data = DataStack(app, f"{prefix}-data", env=env, termination_protection=True,
                 existing_secret_arn=ctx("banxico_token_secret_arn"))
web = AppStack(app, f"{prefix}-app", env=env, table=data.table, token_secret=data.token_secret,
               domain_name=ctx("domain_name"), certificate_arn=ctx("certificate_arn"))
obs = ObservabilityStack(app, f"{prefix}-observability", env=env, distribution=web.distribution,
                         api_fn=web.api_fn, ingest_fn=web.ingest_fn, dlq=web.dlq, table=data.table,
                         alert_email=ctx("alert_email"),
                         probe_rate_minutes=int(ctx("probe_rate_minutes") or 5))

for k, v in {"Project": "fx-rates", "Environment": env_name, "ManagedBy": "cdk"}.items():
    cdk.Tags.of(app).add(k, v)

app.synth()
