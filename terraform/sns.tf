resource "aws_sns_topic" "hitl" {
  name = "${var.project_name}-hitl-review"

  tags = {
    Name = "${local.name_prefix}-hitl"
  }
}
