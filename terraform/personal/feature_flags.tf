# Feature flags SSM parameters (T1.13 CI-RCA methodology contract, Phase 1).
#
# CI_RCA_STRICT_MODE is the canonical flag source: config/feature_flags.yaml.
# This SSM param mirrors the YAML value for future server-side strict enforcement
# (e.g. Lambda-side strict mode gating). The YAML stays authoritative; this param
# is read-only from the Lambda side.
#
# Auto-applies under terraform/personal/ (Decision 77 / CD.35): a plain SSM String
# parameter with no IAM/destroy/trust change passes the terraform_apply_guard.

resource "aws_ssm_parameter" "ci_rca_strict_mode" {
  name  = "/agent-platform/feature-flags/CI_RCA_STRICT_MODE" # pragma: allowlist secret
  type  = "String"
  value = try(yamldecode(file("${path.module}/../../config/feature_flags.yaml"))["CI_RCA_STRICT_MODE"], "warn")

  description = "CI-RCA schema enforcement mode (warn | strict). Source of truth: config/feature_flags.yaml."

  tags = {
    Project   = "agent-platform"
    Component = "feature-flags"
    ManagedBy = "terraform"
    TierItem  = "T1.13"
  }
}
# throwaway comment line for rec-4913 VP 9 (closed unmerged)
