# Aligned with infrastructure/cdk/stacks/core_stack.py table shapes.

resource "aws_dynamodb_table" "claims" {
  name         = "calclaim-claims"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "claim_id"
  range_key    = "submitted_at"

  attribute {
    name = "claim_id"
    type = "S"
  }
  attribute {
    name = "submitted_at"
    type = "S"
  }
  attribute {
    name = "member_id"
    type = "S"
  }

  global_secondary_index {
    name            = "member_id-index"
    hash_key        = "member_id"
    range_key       = "submitted_at"
    projection_type = "ALL"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = { Name = "${local.name_prefix}-claims" }
}

resource "aws_dynamodb_table" "audit" {
  name         = "calclaim-audit-log"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "event_id"
  range_key    = "timestamp_utc"

  attribute {
    name = "event_id"
    type = "S"
  }
  attribute {
    name = "timestamp_utc"
    type = "S"
  }
  attribute {
    name = "claim_id"
    type = "S"
  }

  global_secondary_index {
    name            = "claim_id-index"
    hash_key        = "claim_id"
    range_key       = "timestamp_utc"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = { Name = "${local.name_prefix}-audit" }
}

resource "aws_dynamodb_table" "sessions" {
  name         = "calclaim-sessions"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "session_id"

  attribute {
    name = "session_id"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = { Name = "${local.name_prefix}-sessions" }
}
