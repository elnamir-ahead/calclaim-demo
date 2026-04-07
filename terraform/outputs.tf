output "api_base_url" {
  description = "HTTP API base URL (append /docs, /claims/adjudicate, etc.)"
  value       = aws_apigatewayv2_stage.default.invoke_url
}

output "lambda_function_name" {
  value = aws_lambda_function.api.function_name
}

output "dynamodb_claims_table" {
  value = aws_dynamodb_table.claims.name
}

output "s3_audit_bucket" {
  value       = aws_s3_bucket.audit_archive.id
  description = "S3 bucket for immutable audit JSON archives (audit_logger)"
}

output "dynamodb_audit_table" {
  value = aws_dynamodb_table.audit.name
}

output "dynamodb_sessions_table" {
  value = aws_dynamodb_table.sessions.name
}

output "hitl_sns_topic_arn" {
  value = aws_sns_topic.hitl.arn
}
