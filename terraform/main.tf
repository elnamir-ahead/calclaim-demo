terraform {
  required_version = ">= 1.5"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  # bucket / key / region: pass via `terraform init -backend-config=...` (matches policy-agent pattern)
  backend "s3" {}
}

provider "aws" {
  region = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix = "calclaim"
}

check "jwt_authorizer_config" {
  assert {
    condition = !var.enable_jwt_authorizer || (
      var.jwt_issuer != "" && length(var.jwt_audience) > 0
    )
    error_message = "When enable_jwt_authorizer is true, set jwt_issuer and a non-empty jwt_audience list."
  }
}
