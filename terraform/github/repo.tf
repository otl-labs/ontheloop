resource "github_repository" "this" {
  name       = var.repository_name
  visibility = "public"

  # GitHub Advanced Security: secret scanning + push protection (T2.12 / CD.20).
  security_and_analysis {
    # advanced_security is omitted: GitHub Advanced Security is always-on for PUBLIC repos;
    # setting it via the API errors ("always available for public repositories"). The repo
    # went public at T2.13 (2026-05-30), so only secret scanning + push protection are managed.
    secret_scanning {
      status = "enabled"
    }
    secret_scanning_push_protection {
      status = "enabled"
    }
  }

  # Preserve existing repo metadata. Terraform will read the live state on first plan;
  # ignore_changes prevents accidental resets of description/topics/features on subsequent plans.
  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      description,
      homepage_url,
      topics,
      has_issues,
      has_projects,
      has_wiki,
      has_discussions,
      has_downloads,
      allow_merge_commit,
      allow_squash_merge,
      allow_rebase_merge,
      allow_auto_merge,
      allow_update_branch,
      delete_branch_on_merge,
      archived,
      # Deprecated, Default false and never read or imported by the provider: an import leaves it null and the
      # plan would PATCH the repository to set it (rec-3136).
      ignore_vulnerability_alerts_during_read,
      # Never set here, so they plan to the provider defaults; the PR-time plan read them otherwise and
      # planned a reset. Unmanaged, like the allow_* merge methods above (rec-3136).
      merge_commit_message,
      merge_commit_title,
      squash_merge_commit_message,
      squash_merge_commit_title,
    ]
  }
}

# Branch-protection ruleset for main (T2.12 / CD.20).
# Required status checks use the LIVE check-run names from ci.yml jobs 'pr-validate' and
# 'terraform-validate' (NOT the placeholder 'validate (pre)' -- see VP step 4 / ci.yml:16,138).
# bypass_actors carries an admin escape hatch: actor_id=5 = Repository Admin role so a human
# always retains an in-band escape from lockout.
# require_code_owner_review = true (DEP-05 / T2.50 / Decision 144): PRs touching a path
# scoped in .github/CODEOWNERS require code-owner review before merge. Path-scoping
# comes from CODEOWNERS alone -- required_approving_review_count stays 0 so routine PRs
# outside those paths remain approval-free and non-wedging (Decision 83). A PR authored by
# the sole code owner passes the rule with no review (rule-suite insights for #1534 and #1599:
# pass, not bypass), so agent PRs, authored as that person, are gated by the operator's typed
# approval in the project thread, not by this rule (rec-4355; the non-owner proof is rec-4625).
# The admin bypass_actors escape hatch above stays available (a GitHub-audit-logged bypass,
# not a hard block -- see rec-2827 for the deferred hard block against the admin identity).
resource "github_repository_ruleset" "main_protection" {
  repository  = github_repository.this.name
  name        = "main-protection"
  target      = "branch"
  enforcement = "active"

  conditions {
    ref_name {
      include = ["refs/heads/main"]
      exclude = []
    }
  }

  bypass_actors {
    actor_id    = var.admin_bypass_actor_id
    actor_type  = "RepositoryRole"
    bypass_mode = "always"
  }

  rules {
    deletion         = true
    non_fast_forward = true

    # The live rule also serves dismissal_restriction (disabled) and
    # require_extra_approval_for_unattributed_changes (true), organisation-repository keys that
    # provider 6.12.1 does not model. An import or a no-op plan leaves them alone, but any update of
    # this ruleset sends only the modelled keys and can reset both to the API defaults: do not
    # change this ruleset until a provider release models them (rec-4624; T4.21 c10 needs the
    # dismissal restriction managed here).
    pull_request {
      dismiss_stale_reviews_on_push     = false
      require_code_owner_review         = true
      require_last_push_approval        = false
      required_approving_review_count   = 0
      required_review_thread_resolution = false
    }

    required_status_checks {
      required_check {
        # integration_id = 0 uses GitHub's legacy "any integration" semantics, which is
        # the documented approach for GitHub Actions checks in the Terraform provider.
        context        = "pr-validate"
        integration_id = 0
      }
      required_check {
        context        = "terraform-validate"
        integration_id = 0
      }
      # strict = false: allows merging a branch that is not up-to-date with main.
      # Intentional for the Decision 76 squash-merge flow (sole-developer project);
      # the ruleset still requires green status checks before any merge.
      strict_required_status_checks_policy = false
    }

    required_linear_history = true

    # No required_signatures: intentional -- do not add. main-protection is a deliberately
    # MINIMAL rule set (required checks limited to pr-validate and terraform-validate, admin
    # bypass always, strict = false, terraform-converged advisory) -- every additional rule
    # needs its own justification, and none exists for required_signatures. Server-side
    # verification is NOT the blocker: GitHub reports these commits Verified. See AGENTS.md
    # "### Commit signing (CC-web: SSH-signed via harness signer)".
  }
}

# Actions settings: all actions allowed (filtered by the ruleset gate), but Actions
# are explicitly enabled. Restricts which action types may run (a future addition).
resource "github_actions_repository_permissions" "this" {
  repository      = github_repository.this.name
  allowed_actions = "all"
  enabled         = true
}

# Workflow permissions: default token = read-only; GitHub Actions cannot approve PRs.
# Restricts the blast radius of a compromised workflow -- the GITHUB_TOKEN cannot
# self-approve or write without an explicit permissions: block in the workflow file.
# Note: the "fork pull request approval" policy is not exposed by this provider attribute.
# Since the transfer the repository has the otl-labs organisation's stricter default
# (github-org-transfer.yaml evidence_ledger, 2026-10-24); it is set in the organisation's or
# the repository's Actions settings, never here.
resource "github_workflow_repository_permissions" "this" {
  repository                       = github_repository.this.name
  default_workflow_permissions     = "read"
  can_approve_pull_request_reviews = false
}
