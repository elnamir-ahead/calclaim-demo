"""
API stack — Lambda (FastAPI + Mangum) + API Gateway HTTP API.
"""

import os
from aws_cdk import (
    Stack, Duration, CfnOutput,
    aws_lambda as lambda_,
    aws_apigatewayv2 as apigw,
    aws_apigatewayv2_integrations as integrations,
    aws_apigatewayv2_authorizers as apigw_auth,
    aws_iam as iam,
    aws_ssm as ssm,
    aws_dynamodb as dynamodb,
    aws_sns as sns,
)
from constructs import Construct
from typing import Optional


class CalcClaimAPIStack(Stack):

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        claims_table: dynamodb.Table,
        audit_table: dynamodb.Table,
        session_table: dynamodb.Table,
        guardrail_id_param: ssm.StringParameter,
        hitl_topic: sns.Topic,
        jwt_issuer: Optional[str] = None,
        jwt_audience: Optional[str] = None,
        **kwargs,
    ) -> None:
        tags = kwargs.pop("tags", {})
        super().__init__(scope, construct_id, **kwargs)
        for k, v in tags.items():
            self.tags.set_tag(k, v)

        # -----------------------------------------------------------
        # Lambda execution role
        # -----------------------------------------------------------
        lambda_role = iam.Role(
            self, "LambdaRole",
            role_name="calclaim-api-lambda-role",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "AWSXRayDaemonWriteAccess"
                ),
            ],
        )

        # Bedrock permissions
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=[
                "bedrock:InvokeModel",
                "bedrock:InvokeModelWithResponseStream",
                "bedrock:ApplyGuardrail",
                "bedrock:InvokeAgent",
            ],
            resources=["*"],
        ))

        # DynamoDB permissions
        for table in [claims_table, audit_table, session_table]:
            table.grant_read_write_data(lambda_role)

        # SNS (HITL)
        hitl_topic.grant_publish(lambda_role)

        # SSM (read config params)
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["ssm:GetParameter", "ssm:GetParameters"],
            resources=[f"arn:aws:ssm:{self.region}:{self.account}:parameter/calclaim/*"],
        ))

        # CloudWatch Logs (audit)
        lambda_role.add_to_policy(iam.PolicyStatement(
            actions=["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:/calclaim/*"],
        ))

        # -----------------------------------------------------------
        # Lambda function
        # -----------------------------------------------------------
        self.lambda_function = lambda_.Function(
            self, "CalcClaimAPI",
            function_name="calclaim-api",
            description="CalcClaim API — LangGraph + Bedrock adjudication",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="lambda.handler.handler",
            code=lambda_.Code.from_asset(
                "../../",
                bundling={
                    "image": lambda_.Runtime.PYTHON_3_12.bundling_image,
                    "command": [
                        "bash", "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -r . /asset-output",
                    ],
                },
            ),
            role=lambda_role,
            timeout=Duration.seconds(120),
            memory_size=1024,
            tracing=lambda_.Tracing.ACTIVE,
            environment={
                "AWS_REGION": self.region,
                "BEDROCK_REGION": self.region,
                "ENVIRONMENT": "demo",
                "DEMO_MODE": "false",
                "LOG_LEVEL": "INFO",
                "LOG_FORMAT": "text",
                "REQUIRE_AUTH": "false",
                "TRUST_API_GATEWAY_AUTH": "true" if (jwt_issuer and jwt_audience) else "false",
                "DYNAMODB_CLAIMS_TABLE": claims_table.table_name,
                "DYNAMODB_AUDIT_TABLE": audit_table.table_name,
                "DYNAMODB_SESSION_TABLE": session_table.table_name,
                "HITL_SNS_TOPIC_ARN": hitl_topic.topic_arn,
                "LANGCHAIN_TRACING_V2": "true",
                "LANGCHAIN_PROJECT": "calclaim-demo",
                "ENABLE_CLOUDWATCH_EMF": "true",
            },
        )

        # -----------------------------------------------------------
        # API Gateway HTTP API
        # -----------------------------------------------------------
        self.http_api = apigw.HttpApi(
            self, "CalcClaimHttpApi",
            api_name="calclaim-api",
            description="CalcClaim adjudication API",
            cors_preflight=apigw.CorsPreflightOptions(
                allow_origins=["*"],
                allow_methods=[apigw.CorsHttpMethod.ANY],
                allow_headers=["*"],
            ),
        )

        integration = integrations.HttpLambdaIntegration(
            "LambdaIntegration",
            self.lambda_function,
        )

        self.http_api.add_routes(
            path="/health",
            methods=[apigw.HttpMethod.GET],
            integration=integration,
        )

        if jwt_issuer and jwt_audience:
            jwt_authorizer = apigw_auth.HttpJwtAuthorizer(
                self,
                "ApiJwtAuthorizer",
                jwt_audience=[jwt_audience],
                jwt_issuer=jwt_issuer,
            )
            self.http_api.add_routes(
                path="/{proxy+}",
                methods=[apigw.HttpMethod.ANY],
                integration=integration,
                authorizer=jwt_authorizer,
            )
        else:
            self.http_api.add_routes(
                path="/{proxy+}",
                methods=[apigw.HttpMethod.ANY],
                integration=integration,
            )

        CfnOutput(self, "ApiEndpoint", value=self.http_api.url or "")
        CfnOutput(self, "LambdaArn", value=self.lambda_function.function_arn)
