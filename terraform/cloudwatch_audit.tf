# SIEM audit stream used by src/governance/audit_logger.py (AUDIT_LOG_GROUP = "/calclaim/audit").
# Without this group, DEMO_MODE=false causes CreateLogStream to fail on adjudication.

resource "aws_cloudwatch_log_group" "audit" {
  name              = "/calclaim/audit"
  retention_in_days = var.lambda_log_retention_days
}
