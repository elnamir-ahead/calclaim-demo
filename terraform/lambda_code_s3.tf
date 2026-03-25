# Lambda deployment package via S3 — avoids CreateFunction direct body limit (~70MB).
# Terraform uploads the zip to this bucket, then Lambda pulls from S3.

resource "aws_s3_bucket" "lambda_code" {
  bucket = "${var.project_name}-lambda-code-${data.aws_caller_identity.current.account_id}"

  tags = {
    Name = "${var.project_name}-lambda-code"
  }
}

resource "aws_s3_bucket_public_access_block" "lambda_code" {
  bucket = aws_s3_bucket.lambda_code.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "lambda_code" {
  bucket = aws_s3_bucket.lambda_code.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

data "aws_iam_policy_document" "lambda_code_bucket" {
  statement {
    sid = "AllowLambdaServiceGetObject"
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.lambda_code.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_s3_bucket_policy" "lambda_code" {
  bucket = aws_s3_bucket.lambda_code.id
  policy = data.aws_iam_policy_document.lambda_code_bucket.json
}

resource "aws_s3_object" "lambda_package" {
  bucket = aws_s3_bucket.lambda_code.id
  key    = "api/lambda.zip"
  source = "${path.module}/build/lambda.zip"
  etag   = filemd5("${path.module}/build/lambda.zip")

  depends_on = [
    aws_s3_bucket_public_access_block.lambda_code,
    aws_s3_bucket_policy.lambda_code,
  ]
}
