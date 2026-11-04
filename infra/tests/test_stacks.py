import os
import sys

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from fx_infra.app_stack import AppStack  # noqa: E402
from fx_infra.data_stack import DataStack  # noqa: E402
from fx_infra.observability_stack import ObservabilityStack  # noqa: E402

ENV = cdk.Environment(account="111111111111", region="us-east-2")


def build():
    app = cdk.App()
    data = DataStack(app, "d", env=ENV)
    web = AppStack(app, "a", env=ENV, table=data.table, token_secret=data.token_secret)
    obs = ObservabilityStack(app, "o", env=ENV, distribution=web.distribution, api_fn=web.api_fn,
                             ingest_fn=web.ingest_fn, dlq=web.dlq, table=data.table, alert_email="a@b.co")
    return Template.from_stack(data), Template.from_stack(web), Template.from_stack(obs)


def test_all():
    d, a, o = build()
    d.has_resource_properties("AWS::DynamoDB::Table", {
        "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
        "DeletionProtectionEnabled": True, "BillingMode": "PAY_PER_REQUEST"})
    a.has_resource_properties("AWS::Lambda::Url", {"AuthType": "AWS_IAM"})          # origin is private
    a.resource_count_is("AWS::CloudFront::OriginAccessControl", 2)                  # S3 + Lambda URL
    a.has_resource_properties("AWS::S3::Bucket", {"PublicAccessBlockConfiguration": Match.object_like(
        {"BlockPublicAcls": True, "RestrictPublicBuckets": True})})
    a.has_resource_properties("AWS::Lambda::Function", {"Architectures": ["arm64"]})
    o.resource_count_is("AWS::CloudWatch::CompositeAlarm", 2)                        # fast + slow burn
    o.has_resource_properties("AWS::SNS::Subscription", {"Protocol": "email"})
