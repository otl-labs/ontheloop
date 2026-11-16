# IAM Access Analyzer policy checks for bootstrap-preview only (Decision 257 Update 2026-11-16, PLAN-bootstrap-iam-analyzer-checks).
locals {
  bootstrap_preview_analyzer_actions = [
    "access-analyzer:ValidatePolicy",
    "access-analyzer:CheckAccessNotGranted",
    "access-analyzer:CheckNoNewAccess",
    "access-analyzer:CheckNoPublicAccess",
  ]

  bootstrap_preview_analyzer_json = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AccessAnalyzerPolicyChecks"
        Effect = "Allow"
        Action = [
          "access-analyzer:ValidatePolicy",
          "access-analyzer:CheckAccessNotGranted",
          "access-analyzer:CheckNoNewAccess",
          "access-analyzer:CheckNoPublicAccess",
        ]
        Resource = ["*"]
      },
    ]
  })
}

resource "aws_iam_policy" "bootstrap_preview_analyzer" {
  name        = "bootstrap-preview-analyzer"
  description = "Access Analyzer policy checks for bootstrap-preview (PLAN-bootstrap-iam-analyzer-checks)"
  policy      = local.bootstrap_preview_analyzer_json

  lifecycle {
    precondition {
      condition     = length(jsonencode(jsondecode(local.bootstrap_preview_analyzer_json))) <= 6144
      error_message = "bootstrap-preview-analyzer exceeds the 6,144 B managed-policy limit (whitespace-stripped measure)."
    }
  }
}

resource "aws_iam_role_policy_attachment" "bootstrap_preview_analyzer" {
  role       = aws_iam_role.bootstrap_preview.name
  policy_arn = aws_iam_policy.bootstrap_preview_analyzer.arn
}
