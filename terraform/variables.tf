variable "aws_region" {
  description = "AWS region for deployment"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefix for some resource names"
  type        = string
  default     = "calclaim-demo"
}

variable "lambda_timeout_seconds" {
  type    = number
  default = 120
}

variable "lambda_memory_mb" {
  type    = number
  default = 1024
}

variable "langchain_api_key" {
  description = "Optional LangSmith API key (sensitive)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "langchain_tracing_v2" {
  description = "Enable LangSmith tracing when API key is set"
  type        = string
  default     = "true"
}

variable "bedrock_guardrail_id" {
  description = "Optional Bedrock Guardrail ID (empty = skip)"
  type        = string
  default     = ""
}

variable "cors_allow_origins" {
  description = "API Gateway CORS allow_origins (use specific hosts in prod)"
  type        = list(string)
  default     = ["*"]
}

variable "log_format" {
  description = "Lambda LOG_FORMAT: text or json (structured logs)"
  type        = string
  default     = "text"

  validation {
    condition     = contains(["text", "json"], var.log_format)
    error_message = "log_format must be text or json."
  }
}

variable "enable_jwt_authorizer" {
  description = "When true, API Gateway validates JWT; Lambda sets TRUST_API_GATEWAY_AUTH"
  type        = bool
  default     = false
}

variable "jwt_issuer" {
  description = "JWT issuer URL (e.g. Cognito https://cognito-idp.region.amazonaws.com/poolid)"
  type        = string
  default     = ""
}

variable "jwt_audience" {
  description = "JWT audiences accepted by API Gateway authorizer"
  type        = list(string)
  default     = []
}

variable "opa_server_url" {
  description = "Optional OPA HTTP base (e.g. http://opa:8181) — set USE_OPA true in Lambda env via use_opa"
  type        = string
  default     = ""
}

variable "use_opa" {
  description = "When true and opa_server_url set, Lambda evaluates policies via OPA"
  type        = bool
  default     = false
}
