import os

from aws_cdk import (
    CfnOutput, Duration, RemovalPolicy, Stack,
    aws_cloudfront as cf, aws_cloudwatch as cw, aws_cloudwatch_actions as cw_actions,
    aws_dynamodb as ddb, aws_events as events, aws_events_targets as targets,
    aws_lambda as _lambda, aws_logs as logs, aws_sns as sns, aws_sns_subscriptions as subs, aws_sqs as sqs,
)
from constructs import Construct

# ------------------------------------------------------------------ SLO definitions (single source of truth)
# Every SLI is a ratio of good probes (or runs) to all probes (or runs) over 30 days; the budget is 1 - SLO.
AVAILABILITY_SLO = 0.999                 # 99.9% -> budget 0.1%: about 43 failed 1-minute probes per month
ERROR_BUDGET = 1 - AVAILABILITY_SLO
LATENCY_THRESHOLD_MS = 800               # a successful probe is "fast" when its round trip is at most this
LATENCY_SLO = 0.99                       # 99% of successful probes are fast -> budget 1%
FRESHNESS_SLO = 0.99                     # 99% of probes see data no older than 4 days -> budget 1%
INGEST_SLO = 0.99                        # 99% of ingestion runs succeed (do not end in the DLQ) -> budget 1%
FAST_BURN, SLOW_BURN = 14.4, 6.0         # Google SRE workbook: 2% of the 30-day budget in 1h / 5% in 6h
# With 1-minute probes, a single failure already exceeds the 1-hour fast-burn ratio (1/60 > 1.44%), so a page also
# needs this many consecutive failed probes. One blip never pages; a real outage pages after about 3 minutes.
CONSECUTIVE_FAILURES_TO_PAGE = 3
PROBE_RATE_MINUTES = 1                   # the alerting maths above assumes one probe per minute


class ObservabilityStack(Stack):
    def __init__(self, scope: Construct, cid: str, distribution: cf.IDistribution, api_fn: _lambda.IFunction,
                 ingest_fn: _lambda.IFunction, dlq: sqs.IQueue, table: ddb.ITable,
                 alert_email: str | None, probe_rate_minutes: int = PROBE_RATE_MINUTES, **kw) -> None:
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
            environment={"TARGET_URL": f"https://{distribution.distribution_domain_name}/api/rates",
                         "LATENCY_THRESHOLD_MS": str(LATENCY_THRESHOLD_MS)},
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

        consecutive = cw.Alarm(
            self, "ProbeConsecutiveFailures", metric=pm("Availability", "Maximum", Duration.minutes(1)),
            threshold=1, evaluation_periods=CONSECUTIVE_FAILURES_TO_PAGE, datapoints_to_alarm=CONSECUTIVE_FAILURES_TO_PAGE,
            comparison_operator=cw.ComparisonOperator.LESS_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.BREACHING,
            alarm_description=f"{CONSECUTIVE_FAILURES_TO_PAGE} consecutive failed probes")
        fast = cw.CompositeAlarm(self, "SloFastBurn", alarm_rule=cw.AlarmRule.all_of(
            burn_alarm("FastBurn1h", Duration.hours(1), FAST_BURN), burn_alarm("FastBurn5m", Duration.minutes(5), FAST_BURN),
            consecutive),
            alarm_description="PAGE: 2% of the monthly error budget burned in the last hour, "
                              f"and {CONSECUTIVE_FAILURES_TO_PAGE}+ consecutive failed probes")
        slow = cw.CompositeAlarm(self, "SloSlowBurn", alarm_rule=cw.AlarmRule.all_of(
            burn_alarm("SlowBurn6h", Duration.hours(6), SLOW_BURN), burn_alarm("SlowBurn30m", Duration.minutes(30), SLOW_BURN)),
            alarm_description="TICKET: 5% of the monthly error budget burned in 6 hours")

        # ---------------------------------------------------------------- other SLO / health alarms
        # ---------------------------------------------------------------- latency SLO: slow-burn ticket
        def slow_ratio(period: Duration) -> cw.MathExpression:
            return cw.MathExpression(expression="1 - fast", period=period,
                                     using_metrics={"fast": pm("LatencyGood", period=period)})

        def latency_alarm(cid_: str, period: Duration) -> cw.Alarm:
            return slow_ratio(period).create_alarm(
                self, cid_, threshold=(1 - LATENCY_SLO) * SLOW_BURN, evaluation_periods=1,
                comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
                treat_missing_data=cw.TreatMissingData.NOT_BREACHING,  # no successful probes: availability alerts
                alarm_description=f"Share of probes slower than {LATENCY_THRESHOLD_MS} ms above {SLOW_BURN}x budget")

        latency = cw.CompositeAlarm(self, "LatencySloBurn", alarm_rule=cw.AlarmRule.all_of(
            latency_alarm("LatencyBurn6h", Duration.hours(6)), latency_alarm("LatencyBurn30m", Duration.minutes(30))),
            alarm_description=f"TICKET: 5% of the monthly latency budget (probes over {LATENCY_THRESHOLD_MS} ms) "
                              "burned in 6 hours")
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

        # 30-day SLI and remaining error budget per SLO (budget remaining = 1 - spent / allowed; below 0 = exhausted)
        ingest_ok = cw.Metric(namespace="FxRates", metric_name="IngestedRecords", statistic="SampleCount",
                              period=Duration.days(1))         # emitted once per successful run
        ingest_dlq = dlq.metric("NumberOfMessagesSent", statistic="Sum", period=Duration.days(1))
        slis = {  # name: (SLO, expression for the good ratio, metrics it uses)
            "Availability": (AVAILABILITY_SLO, "avail", {"avail": pm("Availability", period=Duration.days(1))}),
            "Latency": (LATENCY_SLO, "fast", {"fast": pm("LatencyGood", period=Duration.days(1))}),
            "Freshness": (FRESHNESS_SLO, "1 - stale", {"stale": pm("DataStale", period=Duration.days(1))}),
            "Ingestion": (INGEST_SLO, "ok / (ok + FILL(dlq, 0))", {"ok": ingest_ok, "dlq": ingest_dlq}),
        }

        def budget_widget(name: str, slo: float, expr: str, metrics: dict) -> cw.SingleValueWidget:
            return cw.SingleValueWidget(
                title=f"{name}: SLO {slo:.1%} (30 days)", width=6, height=4, start="-P30D", set_period_to_time_range=True,
                full_precision=False, metrics=[
                    cw.MathExpression(expression=expr, using_metrics=metrics, label="SLI", period=Duration.days(1)),
                    cw.MathExpression(expression=f"1 - (1 - ({expr})) / {1 - slo:.6f}", using_metrics=metrics,
                                      label="Budget remaining", period=Duration.days(1))])
        dash = cw.Dashboard(self, "Dashboard", dashboard_name=f"{Stack.of(self).stack_name}-slo",
                            default_interval=Duration.days(7))
        dash.add_widgets(
            cw.AlarmStatusWidget(title="Alarms", alarms=[fast, slow, latency, stale, dlq_alarm, api_errors, api_throttles],
                                 width=24, height=3),
            *[budget_widget(n, *v) for n, v in slis.items()],
            cw.GraphWidget(title=f"Availability SLI (SLO {AVAILABILITY_SLO:.1%})", width=12,
                           left=[pm("Availability", period=Duration.hours(1))], left_y_axis=cw.YAxisProps(min=0, max=1),
                           left_annotations=[cw.HorizontalAnnotation(value=AVAILABILITY_SLO, label="SLO")]),
            cw.GraphWidget(title="Error ratio vs burn thresholds", width=12, left=[err_ratio(Duration.hours(1))],
                           left_annotations=[cw.HorizontalAnnotation(value=ERROR_BUDGET * FAST_BURN, label="fast burn"),
                                             cw.HorizontalAnnotation(value=ERROR_BUDGET * SLOW_BURN, label="slow burn")]),
            cw.GraphWidget(title=f"Latency seen by probe (ms), threshold {LATENCY_THRESHOLD_MS} ms", width=12,
                           left=[pm("LatencyMs", s) for s in ("p50", "p95", "p99")],
                           left_annotations=[cw.HorizontalAnnotation(value=LATENCY_THRESHOLD_MS, label="threshold")]),
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
