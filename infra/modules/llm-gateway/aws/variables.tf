# AWS skeleton: same variable contract, ZERO resources, never applied.
# Every behavior is not_applied/documented-only (see docs/gap-analysis-aws.md).
# Provider-free on purpose: nothing here needs an AWS account to validate.
variable "environment" {
  type    = string
  default = "dev"
}
variable "tags" {
  type    = map(string)
  default = {}
}
variable "upstream_mode" {
  type    = string
  default = "stub"
}
