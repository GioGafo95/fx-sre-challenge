import os

from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Stack,
    aws_cloudfront as cf, aws_cloudwatch as cw, aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as ddb, aws_events as events, aws_events_targets as targets,
    aws_lambda as _lambda, aws_logs as logs, aws_sns as sns, aws_sns_subscriptions as subs, aws_sqs as sqs,
)
from constructs import Construct

# ------------------------------------------------------------------ SLO definitions (single source of truth)
AVAILABILITY_SLO = 0.999                 # 99.9% over 30 days -> error budget 0.1% (~43 min/month)
ERROR_BUDGET = 1 - AVAILABILITY_SLO
FAST_BURN, SLOW_BURN = 14.4, 6.0         # Google SRE workbook: 2% of the 30-day budget in 1h / 5% in 6h
LATENCY_P95_MS = 800                     # p95 as seen by the probe, through CloudFront


class ObservabilityStack(Stack):
    def __init__(self, scope: Construct, cid: str, distribution: cf.IDistribution, api_fn: _lambda.IFunction,
                 ingest_fn: _lambda.IFunction, dlq: sqs.IQueue, table: ddb.ITable,
                 alert_email: str | None, probe_rate_minutes: int = 5, **kw) -> None:
        super().__init__(scope, cid, **kw)

        topic = sns.Topic(self, "Alerts", enforce_ssl=True)
        if alert_email:
            topic.add_subscription(subs.EmailSubscription(alert_email))
        notify = cw_actions.SnsAction(topic)

        # ---------------------------------------------------------------- synthetic probe (the SLI source)
        probe = _lambda.Function(
            self, "Probe", runtime=_lambda.Runtime.PYTHON_3_12, architecture=_lambda.Architecture.ARM_64,
            handler="probe.handler", code=_lambda.Code.from_asset(os.path.join(os.path.dirname(__file__), "..", "lambda")),
            timeout=Duration.seconds(15), memory_size=128,
            environment={"TARGET_URL": f"https://{distribution.distribution_domain_name}/api/rates"},
            log_group=logs.LogGroup(self, "ProbeLogs", retention=logs.RetentionDays.ONE_MONTH,
                                    removal_policy=RemovalPolicy.DESTROY),
        )
        events.Rule(self, "ProbeSchedule", schedule=events.Schedule.rate(Duration.minutes(probe_rate_minutes)),
                    targets=[targets.LambdaFunction(probe)])

        def pm(name: str, stat: str = "Average", period: Duration = Duration.minutes(5)) -> cw.Metric:
            return cw.Metric(namespace="FxRates/Probe", metric_name=name, statistic=stat, period=period)

        # ---------------------------------------------------------------- availability SLO: multi-window burn-rate alerts
        def err_ratio(period: Duration) -> cw.MathExpression:
            return cw.MathExpression(expression="1 - avail", period=period,
                                     using_metrics={"avail": pm("Availability", period=period)})

        def burn_alarm(cid_: str, period: Duration, burn: float) -> cw.Alarm:
            return err_ratio(period).create_alarm(
                self, cid_, threshold=ERROR_BUDGET * burn, evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.BREACHING,  # no probe data = we are blind = page
                alarm_description=f"Error ratio above {burn}x the {ERROR_BUDGET:.3%} budget burn rate")

        fast = cw.CompositeAlarm(self, "SloFastBurn", alarm_rule=cw.AlarmRule.all_of(
            burn_alarm("FastBurn1h", Duration.hours(1), FAST_BURN), burn_alarm("FastBurn5m", Duration.minutes(5), FAST_BURN)),
            alarm_description="PAGE: 2% of the monthly error budget burned in the last hour")
        slow = cw.CompositeAlarm(self, "SloSlowBurn", alarm_rule=cw.AlarmRule.all_of(
            burn_alarm("SlowBurn6h", Duration.hours(6), SLOW_BURN), burn_alarm("SlowBurn30m", Duration.minutes(30), SLOW_BURN)),
            alarm_description="TICKET: 5% of the monthly error budget burned in 6 hours")

        # ---------------------------------------------------------------- other SLO / health alarms
        latency = cw.Alarm(self, "LatencyP95", metric=pm("LatencyMs", "p95"), threshold=LATENCY_P95_MS,
                           evaluation_periods=3, datapoints_to_alarm=3,
                           treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                           alarm_description=f"Probe p95 latency above {LATENCY_P95_MS} ms for 15 minutes")
        stale = cw.Alarm(self, "DataStale", metric=pm("DataStale", "Maximum", Duration.hours(1)), threshold=1,
                         evaluation_periods=3, datapoints_to_alarm=3,
                         comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                         treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                         alarm_description="Rates older than 4 days for 3 hours: ingestion or Banxico problem")
        dlq_alarm = cw.Alarm(self, "IngestDlqNotEmpty",
                             metric=dlq.metric_approximate_number_of_messages_visible(period=Duration.minutes(5)),
                             threshold=1, evaluation_periods=1, treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                             comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                             alarm_description="Ingestion failed after all retries")
        api_errors = cw.Alarm(self, "ApiLambdaErrors", metric=api_fn.metric_errors(period=Duration.minutes(5)),
                              threshold=5, evaluation_periods=2, datapoints_to_alarm=2,
                              treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                              alarm_description="API Lambda returning errors (origin-side view)")
        api_throttles = cw.Alarm(self, "ApiLambdaThrottles", metric=api_fn.metric_throttles(period=Duration.minutes(5)),
                                 threshold=1, evaluation_periods=1, treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
                                 comparison_operator=cw.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
                                 alarm_description="API Lambda throttled: check account concurrency quota")

        for a in (fast, slow, latency, stale, dlq_alarm, api_errors, api_throttles):
            a.add_alarm_action(notify)
            a.add_ok_action(notify)

        # ---------------------------------------------------------------- dashboard
        ingest_age = cw.Metric(namespace="FxRates", metric_name="LatestDataAgeDays", statistic="Maximum",
                               period=Duration.hours(1))
        dash = cw.Dashboard(self, "Dashboard", dashboard_name=f"{Stack.of(self).stack_name}-slo",
                            default_interval=Duration.days(7))
        dash.add_widgets(
            cw.AlarmStatusWidget(title="Alarms", alarms=[fast, slow, latency, stale, dlq_alarm, api_errors, api_throttles],
                                 width=24, height=3),
            cw.GraphWidget(title=f"Availability SLI (SLO {AVAILABILITY_SLO:.1%})", width=12,
                           left=[pm("Availability", period=Duration.hours(1))], left_y_axis=cw.YAxisProps(min=0, max=1),
                           left_annotations=[cw.HorizontalAnnotation(value=AVAILABILITY_SLO, label="SLO")]),
            cw.GraphWidget(title="Error ratio vs burn thresholds", width=12, left=[err_ratio(Duration.hours(1))],
                           left_annotations=[cw.HorizontalAnnotation(value=ERROR_BUDGET * FAST_BURN, label="fast burn"),
                                             cw.HorizontalAnnotation(value=ERROR_BUDGET * SLOW_BURN, label="slow burn")]),
            cw.GraphWidget(title="Latency seen by probe (ms)", width=12,
                           left=[pm("LatencyMs", s) for s in ("p50", "p95", "p99")]),
            cw.GraphWidget(title="Data age (days) and stale flag", width=12,
                           left=[ingest_age], right=[pm("DataStale", "Maximum", Duration.hours(1))]),
            cw.GraphWidget(title="API Lambda", width=8, left=[api_fn.metric_invocations(), api_fn.metric_errors(),
                                                              api_fn.metric_throttles()],
                           right=[api_fn.metric_duration(statistic="p95")]),
            cw.GraphWidget(title="Ingest Lambda", width=8, left=[ingest_fn.metric_invocations(), ingest_fn.metric_errors()]),
            cw.GraphWidget(title="Ingest DLQ depth", width=8,
                           left=[dlq.metric_approximate_number_of_messages_visible()]),
        )

        CfnOutput(self, "AlertTopicArn", value=topic.topic_arn)
        CfnOutput(self, "DashboardName", value=dash.dashboard_name)
