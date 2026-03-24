"""
Core stack — DynamoDB tables and S3 buckets (WORM audit archive).
"""

from aws_cdk import (
    Stack, RemovalPolicy, Duration,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
)
from constructs import Construct


class CalcClaimCoreStack(Stack):

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        tags = kwargs.pop("tags", {})
        super().__init__(scope, construct_id, **kwargs)
        for k, v in tags.items():
            self.tags.set_tag(k, v)

        # -----------------------------------------------------------
        # DynamoDB: Claims
        # -----------------------------------------------------------
        self.claims_table = dynamodb.Table(
            self, "ClaimsTable",
            table_name="calclaim-claims",
            partition_key=dynamodb.Attribute(name="claim_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="submitted_at", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
        self.claims_table.add_global_secondary_index(
            index_name="member_id-index",
            partition_key=dynamodb.Attribute(name="member_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="submitted_at", type=dynamodb.AttributeType.STRING),
        )

        # -----------------------------------------------------------
        # DynamoDB: Audit Log (immutable via IAM conditions)
        # -----------------------------------------------------------
        self.audit_table = dynamodb.Table(
            self, "AuditTable",
            table_name="calclaim-audit-log",
            partition_key=dynamodb.Attribute(name="event_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp_utc", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.RETAIN,
            time_to_live_attribute="ttl",
        )
        self.audit_table.add_global_secondary_index(
            index_name="claim_id-index",
            partition_key=dynamodb.Attribute(name="claim_id", type=dynamodb.AttributeType.STRING),
            sort_key=dynamodb.Attribute(name="timestamp_utc", type=dynamodb.AttributeType.STRING),
        )

        # -----------------------------------------------------------
        # DynamoDB: Session state (LangGraph checkpointer)
        # -----------------------------------------------------------
        self.session_table = dynamodb.Table(
            self, "SessionTable",
            table_name="calclaim-sessions",
            partition_key=dynamodb.Attribute(name="session_id", type=dynamodb.AttributeType.STRING),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            time_to_live_attribute="ttl",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # -----------------------------------------------------------
        # S3: Immutable audit archive (Object Lock WORM)
        # -----------------------------------------------------------
        self.audit_bucket = s3.Bucket(
            self, "AuditBucket",
            bucket_name=f"calclaim-audit-archive-{self.account}",
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            object_lock_enabled=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="archive-after-90-days",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(90),
                        )
                    ],
                )
            ],
        )
        # WORM default retention: 7 years (HIPAA compliance)
        self.audit_bucket.add_object_lock_configuration(
            object_lock_enabled=True,
            default_retention=s3.ObjectLockRetention(
                mode=s3.ObjectLockMode.COMPLIANCE,
                duration=s3.ObjectLockRetention.days(2555),
            ),
        )

        # -----------------------------------------------------------
        # S3: Data lake (claims, formulary data)
        # -----------------------------------------------------------
        self.data_bucket = s3.Bucket(
            self, "DataBucket",
            bucket_name=f"calclaim-data-lake-{self.account}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            versioned=True,
            removal_policy=RemovalPolicy.RETAIN,
        )
