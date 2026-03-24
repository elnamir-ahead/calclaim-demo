"""
Bedrock stack — Guardrail + AgentCore agent configuration.
"""

import json
from aws_cdk import (
    Stack, CfnOutput,
    aws_bedrock as bedrock,
    aws_ssm as ssm,
    aws_iam as iam,
)
from constructs import Construct


class CalcClaimBedrockStack(Stack):

    def __init__(self, scope: Construct, construct_id: str,
                 audit_bucket, **kwargs) -> None:
        tags = kwargs.pop("tags", {})
        super().__init__(scope, construct_id, **kwargs)
        for k, v in tags.items():
            self.tags.set_tag(k, v)

        # -----------------------------------------------------------
        # Bedrock Guardrail — PII detection + content filter
        # -----------------------------------------------------------
        self.guardrail = bedrock.CfnGuardrail(
            self, "CalcClaimGuardrail",
            name="calclaim-phi-guardrail",
            description="Prevent PHI/PII leakage in CalcClaim agent responses",
            blocked_input_messaging=(
                "This request contains sensitive information that cannot be processed directly. "
                "Please remove PHI/PII and resubmit."
            ),
            blocked_outputs_messaging=(
                "The response contained sensitive health information and has been redacted."
            ),
            sensitive_information_policy_config=bedrock.CfnGuardrail.SensitiveInformationPolicyConfigProperty(
                pii_entities_config=[
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="US_SOCIAL_SECURITY_NUMBER", action="ANONYMIZE"
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="EMAIL", action="ANONYMIZE"
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="PHONE", action="ANONYMIZE"
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="NAME", action="ANONYMIZE"
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="ADDRESS", action="ANONYMIZE"
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="DATE_TIME", action="ANONYMIZE"
                    ),
                    bedrock.CfnGuardrail.PiiEntityConfigProperty(
                        type="CREDIT_DEBIT_CARD_NUMBER", action="BLOCK"
                    ),
                ],
            ),
            content_policy_config=bedrock.CfnGuardrail.ContentPolicyConfigProperty(
                filters_config=[
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="HATE", input_strength="HIGH", output_strength="HIGH"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="INSULTS", input_strength="MEDIUM", output_strength="MEDIUM"
                    ),
                    bedrock.CfnGuardrail.ContentFilterConfigProperty(
                        type="MISCONDUCT", input_strength="HIGH", output_strength="HIGH"
                    ),
                ]
            ),
            topic_policy_config=bedrock.CfnGuardrail.TopicPolicyConfigProperty(
                topics_config=[
                    bedrock.CfnGuardrail.TopicConfigProperty(
                        name="financial-advice",
                        definition=(
                            "Any requests for personal financial or investment advice "
                            "outside of pharmacy benefit claim processing."
                        ),
                        type="DENY",
                        examples=["Should I invest in pharma stocks?"],
                    ),
                ]
            ),
        )

        # -----------------------------------------------------------
        # AgentCore IAM role
        # -----------------------------------------------------------
        self.agentcore_role = iam.Role(
            self, "AgentCoreRole",
            role_name="calclaim-agentcore-role",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="IAM role for CalcClaim Bedrock AgentCore",
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonBedrockAgentCorePolicy"
                )
            ],
        )
        self.agentcore_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
            resources=[
                f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-3-5-sonnet*",
                f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-3-haiku*",
            ],
        ))
        self.agentcore_role.add_to_policy(iam.PolicyStatement(
            actions=["bedrock:ApplyGuardrail"],
            resources=[self.guardrail.attr_guardrail_arn],
        ))
        audit_bucket.grant_put(self.agentcore_role)

        # -----------------------------------------------------------
        # AgentCore Agent definition (CalcClaim adjudication agent)
        # -----------------------------------------------------------
        self.agentcore_agent = bedrock.CfnAgent(
            self, "CalcClaimAgent",
            agent_name="calclaim-adjudication-agent",
            description="CalcClaim adjudication agent — processes pharmacy benefit claims",
            foundation_model="anthropic.claude-3-5-sonnet-20241022-v2:0",
            agent_resource_role_arn=self.agentcore_role.role_arn,
            instruction=(
                "You are the CalcClaim adjudication agent for Navitus Health Solutions. "
                "Process pharmacy benefit management (PBM) claims by checking member eligibility, "
                "formulary coverage, prior authorization status, and DUR alerts. "
                "Apply NCRX workflow rules. Never include PHI in your responses."
            ),
            guardrail_configuration=bedrock.CfnAgent.GuardrailConfigurationProperty(
                guardrail_identifier=self.guardrail.attr_guardrail_id,
                guardrail_version="DRAFT",
            ),
            idle_session_ttl_in_seconds=900,
            auto_prepare=True,
        )

        # -----------------------------------------------------------
        # SSM parameters (referenced by Lambda env vars)
        # -----------------------------------------------------------
        self.guardrail_id_param = ssm.StringParameter(
            self, "GuardrailIdParam",
            parameter_name="/calclaim/bedrock/guardrail_id",
            string_value=self.guardrail.attr_guardrail_id,
        )
        ssm.StringParameter(
            self, "AgentCoreIdParam",
            parameter_name="/calclaim/agentcore/agent_id",
            string_value=self.agentcore_agent.attr_agent_id,
        )

        # -----------------------------------------------------------
        # Outputs
        # -----------------------------------------------------------
        CfnOutput(self, "GuardrailId", value=self.guardrail.attr_guardrail_id)
        CfnOutput(self, "AgentCoreId", value=self.agentcore_agent.attr_agent_id)
