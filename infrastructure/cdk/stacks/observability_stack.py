"""
Observability stack — CloudWatch dashboards, alarms, and log metric filters.
Aligned with the AWS-native architecture diagram (LangSmith + CloudWatch).
"""

from aws_cdk import (
    Stack, Duration,
    aws_cloudwatch as cw,
    aws_cloudwatch_actions as cw_actions,
    aws_sns as sns,
    aws_logs as logs,
    aws_lambda as lambda_,
)
from constructs import Construct


class CalcClaimObservabilityStack(Stack):

    def __init__(self, scope: Construct, construct_id: str,
                 api_function: lambda_.Function,
                 **kwargs) -> None:
        tags = kwargs.pop("tags", {})
        super().__init__(scope, construct_id, **kwargs)
        for k, v in tags.items():
            self.tags.set_tag(k, v)

        # -----------------------------------------------------------
        # Log groups
        # -----------------------------------------------------------
        self.api_log_group = logs.LogGroup(
            self, "ApiLogGroup",
            log_group_name="/calclaim/api",
            retention=logs.RetentionDays.THREE_MONTHS,
        )
        self.audit_log_group = logs.LogGroup(
            self, "AuditLogGroup",
            log_group_name="/calclaim/audit",
            retention=logs.RetentionDays.SEVEN_YEARS,
        )

        # -----------------------------------------------------------
        # Metric filters
        # -----------------------------------------------------------
        hitl_metric = logs.MetricFilter(
            self, "HITLMetricFilter",
            log_group=self.api_log_group,
            metric_namespace="CalcClaim/Governance",
            metric_name="HITLTriggerCount",
            filter_pattern=logs.FilterPattern.literal("HITL TRIGGERED"),
            metric_value="1",
        )

        phi_metric = logs.MetricFilter(
            self, "PHIMetricFilter",
            log_group=self.api_log_group,
            metric_namespace="CalcClaim/Governance",
            metric_name="PHIDetectionCount",
            filter_pattern=logs.FilterPattern.literal("PII_SCRUB"),
            metric_value="1",
        )

        error_metric = logs.MetricFilter(
            self, "ErrorMetricFilter",
            log_group=self.api_log_group,
            metric_namespace="CalcClaim/Application",
            metric_name="ErrorCount",
            filter_pattern=logs.FilterPattern.literal("ERROR"),
            metric_value="1",
        )

        # -----------------------------------------------------------
        # Alarms
        # -----------------------------------------------------------
        alerts_topic = sns.Topic(self, "AlertsTopic",
            topic_name="calclaim-ops-alerts",
            display_name="CalcClaim Operations Alerts",
        )

        cw.Alarm(
            self, "HighErrorRateAlarm",
            alarm_name="calclaim-high-error-rate",
            alarm_description="Lambda error rate >5% over 5 minutes",
            metric=api_function.metric_errors(period=Duration.minutes(5)),
            threshold=5,
            evaluation_periods=2,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
            treat_missing_data=cw.TreatMissingData.NOT_BREACHING,
        ).add_alarm_action(cw_actions.SnsAction(alerts_topic))

        cw.Alarm(
            self, "HighLatencyAlarm",
            alarm_name="calclaim-high-latency",
            alarm_description="P99 latency >5s for adjudication Lambda",
            metric=api_function.metric_duration(
                period=Duration.minutes(5),
                statistic="p99",
            ),
            threshold=5000,
            evaluation_periods=3,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        ).add_alarm_action(cw_actions.SnsAction(alerts_topic))

        cw.Alarm(
            self, "HITLBacklogAlarm",
            alarm_name="calclaim-hitl-backlog",
            alarm_description="HITL trigger rate >20/hour — possible anomaly",
            metric=cw.Metric(
                namespace="CalcClaim/Governance",
                metric_name="HITLTriggerCount",
                period=Duration.hours(1),
                statistic="Sum",
            ),
            threshold=20,
            evaluation_periods=1,
            comparison_operator=cw.ComparisonOperator.GREATER_THAN_THRESHOLD,
        ).add_alarm_action(cw_actions.SnsAction(alerts_topic))

        # -----------------------------------------------------------
        # Dashboard
        # -----------------------------------------------------------
        dashboard = cw.Dashboard(self, "CalcClaimDashboard",
            dashboard_name="CalcClaim-Agentic-AI",
        )
        dashboard.add_widgets(
            cw.GraphWidget(
                title="Adjudication Throughput",
                left=[api_function.metric_invocations(period=Duration.minutes(5))],
                right=[api_function.metric_errors(period=Duration.minutes(5))],
                width=12,
            ),
            cw.GraphWidget(
                title="Latency (P50 / P99)",
                left=[
                    api_function.metric_duration(period=Duration.minutes(5), statistic="p50"),
                    api_function.metric_duration(period=Duration.minutes(5), statistic="p99"),
                ],
                width=12,
            ),
            cw.GraphWidget(
                title="Governance Events",
                left=[
                    cw.Metric(namespace="CalcClaim/Governance", metric_name="HITLTriggerCount",
                               period=Duration.hours(1), statistic="Sum"),
                    cw.Metric(namespace="CalcClaim/Governance", metric_name="PHIDetectionCount",
                               period=Duration.hours(1), statistic="Sum"),
                ],
                width=12,
            ),
            cw.SingleValueWidget(
                title="Active Concurrent Executions",
                metrics=[api_function.metric(
                    "ConcurrentExecutions",
                    period=Duration.minutes(1),
                    statistic="Maximum",
                )],
                width=6,
            ),
        )
