# Immutable audit archive (application JSON). WORM/Object Lock can be added later (CDK parity).
resource "aws_s3_bucket" "audit_archive" {
  bucket = "${var.project_name}-audit-archive-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-audit-archive"
  }
}

resource "aws_s3_bucket_public_access_block" "audit_archive" {
  bucket = aws_s3_bucket.audit_archive.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "audit_archive" {
  bucket = aws_s3_bucket.audit_archive.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_iam_role_policy" "audit_s3" {
  name = "audit-s3-archive"
  role = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["s3:PutObject"]
      Resource = "${aws_s3_bucket.audit_archive.arn}/*"
    }]
  })
}
