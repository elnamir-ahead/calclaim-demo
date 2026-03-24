#!/usr/bin/env python3
"""
CalcClaim CDK App — provisions all AWS infrastructure.

Usage:
  cd infrastructure/cdk
  cdk deploy --all
"""

import aws_cdk as cdk
from stacks.core_stack import CalcClaimCoreStack
from stacks.bedrock_stack import CalcClaimBedrockStack
from stacks.api_stack import CalcClaimAPIStack
from stacks.governance_stack import CalcClaimGovernanceStack
from stacks.observability_stack import CalcClaimObservabilityStack

app = cdk.App()

env = cdk.Environment(
    account=app.node.try_get_context("account") or "123456789012",
    region=app.node.try_get_context("region") or "us-east-1",
)

tags = {
    "Project": "calclaim-demo",
    "Environment": "demo",
    "Owner": "navitus-engineering",
    "CostCenter": "PBM-AI",
}

core = CalcClaimCoreStack(app, "CalcClaimCore", env=env, tags=tags)

bedrock = CalcClaimBedrockStack(app, "CalcClaimBedrock",
    audit_bucket=core.audit_bucket,
    env=env,
    tags=tags,
)

governance = CalcClaimGovernanceStack(app, "CalcClaimGovernance",
    audit_table=core.audit_table,
    audit_bucket=core.audit_bucket,
    env=env,
    tags=tags,
)

jwt_issuer = app.node.try_get_context("jwtIssuer")
jwt_audience = app.node.try_get_context("jwtAudience")

api = CalcClaimAPIStack(app, "CalcClaimAPI",
    claims_table=core.claims_table,
    audit_table=core.audit_table,
    session_table=core.session_table,
    guardrail_id=bedrock.guardrail_id_param,
    hitl_topic=governance.hitl_topic,
    jwt_issuer=jwt_issuer if jwt_issuer else None,
    jwt_audience=jwt_audience if jwt_audience else None,
    env=env,
    tags=tags,
)

observability = CalcClaimObservabilityStack(app, "CalcClaimObservability",
    api_function=api.lambda_function,
    env=env,
    tags=tags,
)

app.synth()
