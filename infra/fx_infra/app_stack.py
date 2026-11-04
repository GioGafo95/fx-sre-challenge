import os

from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Stack,
    aws_certificatemanager as acm, aws_cloudfront as cf, aws_cloudfront_origins as origins,
    aws_dynamodb as ddb, aws_ecr_assets as ecr_assets, aws_events as events,
    aws_events_targets as targets, aws_lambda as _lambda, aws_logs as logs,
    aws_s3 as s3, aws_s3_deployment as s3deploy, aws_secretsmanager as sm, aws_sqs as sqs,
)
from constructs import Construct

APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "app"))


class AppStack(Stack):
    """Compute + edge: ingestion Lambda, API container Lambda, S3 site, CloudFront."""

    def __init__(self, scope: Construct, cid: str, table: ddb.ITable, token_secret: sm.ISecret,
                 domain_name: str | None = None, certificate_arn: str | None = None, **kw) -> None:
        super().__init__(scope, cid, **kw)

        # ---------------------------------------------------------------- ingestion
        self.dlq = sqs.Queue(self, "IngestDlq", retention_period=Duration.days(14),
                             encryption=sqs.QueueEncryption.SQS_MANAGED, enforce_ssl=True)
        self.ingest_fn = _lambda.Function(
            self, "Ingest",
            runtime=_lambda.Runtime.PYTHON_3_12, architecture=_lambda.Architecture.ARM_64,
            handler="fx.ingest.handler",
            code=_lambda.Code.from_asset(os.path.join(APP_DIR, "src"),
                                         exclude=["**/__pycache__", "**/*.pyc"]),
            memory_size=256, timeout=Duration.seconds(60),
            environment={"TABLE_NAME": table.table_name, "TOKEN_SECRET_ARN": token_secret.secret_arn},
            tracing=_lambda.Tracing.ACTIVE,
            retry_attempts=2, dead_letter_queue=self.dlq,
            log_group=logs.LogGroup(self, "IngestLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        table.grant_write_data(self.ingest_fn)
        token_secret.grant_read(self.ingest_fn)

        # Times are UTC; Mexico City is UTC-6 all year (no DST since 2022).
        # Banxico publishes the FIX around 12:00 local: two weekday attempts after that + a daily catch-up at 06:00.
        events.Rule(self, "IngestWeekdays",
                    schedule=events.Schedule.cron(minute="30", hour="18,21", week_day="MON-FRI"),
                    targets=[targets.LambdaFunction(self.ingest_fn)])
        events.Rule(self, "IngestDailyCatchUp", schedule=events.Schedule.cron(minute="0", hour="12"),
                    targets=[targets.LambdaFunction(self.ingest_fn)])

        # ---------------------------------------------------------------- API (same Docker image as local/ECS)
        self.api_fn = _lambda.DockerImageFunction(
            self, "Api",
            code=_lambda.DockerImageCode.from_image_asset(APP_DIR, platform=ecr_assets.Platform.LINUX_ARM64),
            architecture=_lambda.Architecture.ARM_64,
            memory_size=512, timeout=Duration.seconds(10),
            environment={"TABLE_NAME": table.table_name, "SERVE_WEB": "0"},
            tracing=_lambda.Tracing.ACTIVE,
            log_group=logs.LogGroup(self, "ApiLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        table.grant_read_data(self.api_fn)  # least privilege: the API can only read

        # Private origin: IAM-authenticated Function URL that only CloudFront (via OAC) may invoke.
        # No API Gateway to pay for, and the origin cannot be hit directly.
        fn_url = self.api_fn.add_function_url(auth_type=_lambda.FunctionUrlAuthType.AWS_IAM)

        # ---------------------------------------------------------------- static site
        site = s3.Bucket(self, "Site", block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
                         encryption=s3.BucketEncryption.S3_MANAGED,
                         removal_policy=RemovalPolicy.DESTROY, auto_delete_objects=True)

        csp = ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
               "frame-ancestors 'none'")
        headers = cf.ResponseHeadersPolicy(
            self, "SecurityHeaders",
            security_headers_behavior=cf.ResponseSecurityHeadersBehavior(
                content_security_policy=cf.ResponseHeadersContentSecurityPolicy(
                    override=True, content_security_policy=csp),
                content_type_options=cf.ResponseHeadersContentTypeOptions(override=True),
                frame_options=cf.ResponseHeadersFrameOptions(frame_option=cf.HeadersFrameOption.DENY, override=True),
                referrer_policy=cf.ResponseHeadersReferrerPolicy(
                    referrer_policy=cf.HeadersReferrerPolicy.NO_REFERRER, override=True),
                strict_transport_security=cf.ResponseHeadersStrictTransportSecurity(
                    access_control_max_age=Duration.days(730), include_subdomains=True, override=True),
            ),
        )
        # Honours the API's Cache-Control (5 min + stale-while-revalidate/stale-if-error); bounded by min/max TTL.
        api_cache = cf.CachePolicy(self, "ApiCache", min_ttl=Duration.seconds(0),
                                   default_ttl=Duration.minutes(5), max_ttl=Duration.hours(1),
                                   enable_accept_encoding_gzip=True, enable_accept_encoding_brotli=True)

        extra = {}
        if domain_name and certificate_arn:  # certificate must live in us-east-1 (CloudFront requirement)
            extra = dict(domain_names=[domain_name],
                         certificate=acm.Certificate.from_certificate_arn(self, "Cert", certificate_arn),
                         minimum_protocol_version=cf.SecurityPolicyProtocol.TLS_V1_2_2021)

        self.distribution = cf.Distribution(
            self, "Cdn",
            default_root_object="index.html",
            price_class=cf.PriceClass.PRICE_CLASS_100,
            http_version=cf.HttpVersion.HTTP2_AND_3,
            default_behavior=cf.BehaviorOptions(
                origin=origins.S3BucketOrigin.with_origin_access_control(site),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                response_headers_policy=headers, compress=True),
            additional_behaviors={"/api/*": cf.BehaviorOptions(
                origin=origins.FunctionUrlOrigin.with_origin_access_control(fn_url),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cf.AllowedMethods.ALLOW_GET_HEAD,
                cache_policy=api_cache, response_headers_policy=headers, compress=True)},
            **extra,
        )

        s3deploy.BucketDeployment(
            self, "DeploySite", sources=[s3deploy.Source.asset(os.path.join(APP_DIR, "web"))],
            destination_bucket=site, distribution=self.distribution, distribution_paths=["/*"],
            cache_control=[s3deploy.CacheControl.max_age(Duration.minutes(5)),
                           s3deploy.CacheControl.must_revalidate()],
        )

        CfnOutput(self, "SiteUrl", value=f"https://{self.distribution.distribution_domain_name}")
        CfnOutput(self, "IngestFunctionName", value=self.ingest_fn.function_name)
