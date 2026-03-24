# GitHub Actions — Terraform deploy (policy-agent style)

The workflow [`.github/workflows/deploy-aws.yml`](workflows/deploy-aws.yml) deploys **CalcClaim** on every push to `main` or when you run it manually (**Actions → Build and Deploy to AWS (Terraform) → Run workflow**).

It mirrors [policy-agent](https://github.com/elnamir-ahead/policy-agent):

1. Build `terraform/build/lambda.zip` via `scripts/build_lambda.sh`
2. Ensure S3 bucket `calclaim-tfstate-<ACCOUNT_ID>` exists (remote Terraform state)
3. `terraform init` + `terraform apply` in `terraform/`

## Required GitHub secrets

| Secret | Description |
|--------|-------------|
| `AWS_ACCESS_KEY_ID` | IAM access key |
| `AWS_SECRET_ACCESS_KEY` | IAM secret key |

## Optional

| Secret | Description |
|--------|-------------|
| `LANGCHAIN_API_KEY` | Passed to Lambda as `LANGCHAIN_API_KEY` for LangSmith (same idea as local `.env`) |

## IAM permissions

The deploy user needs at least: Lambda, API Gateway v2, IAM (role/policy for Lambda), DynamoDB, SNS, S3 (state bucket + `mb`), CloudWatch Logs, Bedrock (invoke / agent / guardrail as you use). For a lab account, many teams use `AdministratorAccess`; for production, scope to least privilege.

## Conflict with CDK

This repo also has **`infrastructure/cdk/`**. Both paths create similarly named resources (e.g. DynamoDB `calclaim-claims`, Lambda `calclaim-api`). **Do not run Terraform apply in the same account/region as an existing CDK stack** unless you import or rename resources.

## Local deploy (same as CI)

```bash
export AWS_REGION=us-east-1
# aws configure  or  SSO
chmod +x scripts/build_lambda.sh scripts/deploy_terraform.sh
./scripts/deploy_terraform.sh
```

## Lambda package size

`pip install -r requirements.txt` into the zip can exceed AWS Lambda deployment limits. If `apply` fails on size, split dependencies into **Lambda layers** or trim optional packages (see `requirements-optional.txt`).

## OpenAPI

After deploy, open:

`$(terraform output -raw api_base_url)docs`
