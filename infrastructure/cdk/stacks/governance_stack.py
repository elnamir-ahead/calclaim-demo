"""
Governance stack:
  - SNS topic for HITL review notifications
  - SQS queue for reviewer UI
  - Lambda for auto-rollback trigger
  - IAM policies for immutable audit enforcement
  - EventBridge rules for anomaly detection
"""

from aws_cdk import (
    Stack, Duration, CfnOutput,
    aws_sns as sns,
    aws_sqs as sqs,
    aws_sns_subscriptions as subs,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_lambda_python_alpha as lambda_python,
    aws_events as events,
    aws_events_targets as targets,
    aws_dynamodb as dynamodb,
)
from constructs import Construct


class CalcClaimGovernanceStack(Stack):

    def __init__(self, scope: Construct, construct_id: str,
                 audit_table: dynamodb.Table,
                 audit_bucket,
                 **kwargs) -> None:
        tags = kwargs.pop("tags", {})
        super().__init__(scope, construct_id, **kwargs)
        for k, v in tags.items():
            self.tags.set_tag(k, v)

        # -----------------------------------------------------------
        # HITL SNS topic + SQS queue
        # -----------------------------------------------------------
        self.hitl_topic = sns.Topic(
            self, "HITLTopic",
            topic_name="calclaim-hitl-review",
            display_name="CalcClaim HITL Review Requests",
        )

        self.hitl_queue = sqs.Queue(
            self, "HITLQueue",
            queue_name="calclaim-hitl-review.fifo",
            fifo=True,
            content_based_deduplication=True,
            visibility_timeout=Duration.minutes(10),
            retention_period=Duration.days(14),
            encryption=sqs.QueueEncryption.KMS_MANAGED,
        )

        # -----------------------------------------------------------
        # Auto-rollback Lambda (triggered on anomalous audit events)
        # -----------------------------------------------------------
        rollback_role = iam.Role(
            self, "RollbackRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        audit_table.grant_read_data(rollback_role)

        self.rollback_fn = lambda_.Function(
            self, "RollbackFn",
            function_name="calclaim-auto-rollback",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="index.handler",
            code=lambda_.Code.from_inline("""
import json, logging, os
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)
dynamo = boto3.resource('dynamodb')

def handler(event, context):
    '''
    Triggered by EventBridge when a suspicious pattern is detected
    in the audit log (e.g. bulk reversal, repeated errors, PHI leak flag).
    '''
    logger.info("Auto-rollback triggered: %s", json.dumps(event))
    detail = event.get('detail', {})
    claim_id = detail.get('claim_id', 'unknown')
    reason = detail.get('reason', 'anomaly detected')

    # In production: revert claim status, notify compliance team
    logger.warning(
        "ROLLBACK INITIATED | claim_id=%s | reason=%s", claim_id, reason
    )
    return {'statusCode': 200, 'claim_id': claim_id, 'action': 'rollback_initiated'}
"""),
            role=rollback_role,
            timeout=Duration.seconds(30),
        )

        # -----------------------------------------------------------
        # EventBridge rule: anomaly detection → rollback
        # -----------------------------------------------------------
        anomaly_rule = events.Rule(
            self, "AnomalyRule",
            rule_name="calclaim-anomaly-detection",
            description="Trigger rollback on PHI leak or bulk anomaly in audit log",
            event_pattern=events.EventPattern(
                source=["calclaim.governance"],
                detail_type=["AuditAnomaly"],
                detail={"severity": ["CRITICAL", "HIGH"]},
            ),
        )
        anomaly_rule.add_target(targets.LambdaFunction(self.rollback_fn))

        # -----------------------------------------------------------
        # IAM: deny DeleteItem on audit table (immutability enforcement)
        # -----------------------------------------------------------
        immutability_policy = iam.ManagedPolicy(
            self, "AuditImmutabilityPolicy",
            managed_policy_name="calclaim-audit-immutability",
            description="Prevents deletion or overwrite of audit log entries",
            statements=[
                iam.PolicyStatement(
                    effect=iam.Effect.DENY,
                    actions=["dynamodb:DeleteItem", "dynamodb:UpdateItem"],
                    resources=[audit_table.table_arn],
                    conditions={
                        "StringNotEquals": {
                            "aws:PrincipalArn": f"arn:aws:iam::{self.account}:role/calclaim-compliance-admin"
                        }
                    },
                )
            ],
        )

        CfnOutput(self, "HITLTopicArn", value=self.hitl_topic.topic_arn)
        CfnOutput(self, "HITLQueueUrl", value=self.hitl_queue.queue_url)
