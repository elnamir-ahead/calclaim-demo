resource "aws_cloudwatch_log_group" "lambda_api" {
  name              = "/aws/lambda/calclaim-api"
  retention_in_days = var.lambda_log_retention_days
}

resource "aws_lambda_function" "api" {
  function_name    = "calclaim-api"
  role             = aws_iam_role.lambda.arn
  handler          = "lambda.handler.handler"
  s3_bucket        = aws_s3_bucket.lambda_code.id
  s3_key           = aws_s3_object.lambda_package.key
  source_code_hash = filebase64sha256("${path.module}/build/lambda.zip")
  runtime          = "python3.12"
  timeout          = var.lambda_timeout_seconds
  memory_size      = var.lambda_memory_mb

  tracing_config {
    mode = var.enable_xray_tracing ? "Active" : "PassThrough"
  }

  environment {
    variables = merge(
      {
        # AWS_REGION is reserved — Lambda sets it automatically; do not pass it here.
        BEDROCK_REGION             = var.aws_region
        ENVIRONMENT                = "prod"
        DEMO_MODE                  = "false"
        LOG_LEVEL                  = "INFO"
        LOG_FORMAT                 = var.log_format
        TRUST_API_GATEWAY_AUTH     = var.enable_jwt_authorizer ? "true" : "false"
        REQUIRE_AUTH               = "false"
        DYNAMODB_CLAIMS_TABLE      = aws_dynamodb_table.claims.name
        DYNAMODB_AUDIT_TABLE       = aws_dynamodb_table.audit.name
        DYNAMODB_SESSION_TABLE     = aws_dynamodb_table.sessions.name
        HITL_SNS_TOPIC_ARN         = aws_sns_topic.hitl.arn
        LANGCHAIN_PROJECT          = var.project_name
        LANGCHAIN_ENDPOINT         = "https://api.smith.langchain.com"
        LANGCHAIN_TRACING_V2       = var.langchain_tracing_v2
        BEDROCK_GUARDRAIL_ID       = var.bedrock_guardrail_id
        BEDROCK_GUARDRAIL_VERSION  = "DRAFT"
        USE_OPA                    = var.use_opa ? "true" : "false"
        OPA_SERVER_URL             = var.opa_server_url
        CORS_ALLOW_ORIGINS         = length(var.cors_allow_origins) == 1 && var.cors_allow_origins[0] == "*" ? "*" : join(",", var.cors_allow_origins)
        ENABLE_CLOUDWATCH_EMF      = "true"
        USE_AGENTCORE              = var.use_agentcore
        AGENTCORE_AGENT_ID         = var.agentcore_agent_id
        AGENTCORE_AGENT_ALIAS_ID   = var.agentcore_agent_alias_id
        CALCLAIM_MCP_URL           = var.calclaim_mcp_url
        USE_MCP_TOOLS              = var.use_mcp_tools
      },
      var.langchain_api_key != "" ? { LANGCHAIN_API_KEY = var.langchain_api_key } : {}
    )
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic,
    aws_cloudwatch_log_group.lambda_api,
    aws_cloudwatch_log_group.audit,
    aws_s3_object.lambda_package,
  ]
}
