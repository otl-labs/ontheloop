"""Shared fixture: terraform/bootstrap files the IAM leak scan deliberately skips, with the reason each is skipped."""

from __future__ import annotations

UNSCANNED_BOOTSTRAP_FILES: dict[str, str] = {
    "github_ci_apply_boundary.tf": "a ceiling that grants nothing",
    "agent_session_user.tf": "the session user and its own policies, operator-applied (Decision 256)",
    "platform_roles.tf": "PlatformDev and PlatformAdmin themselves, operator-applied (Decision 223)",
    "platform_admin_policies.tf": "PlatformAdmin's own grants, operator-applied (Decision 223)",
    "platform_admin_data_policies.tf": "PlatformAdmin's own grants, operator-applied (Decision 223)",
    "platform_dev_policies.tf": "PlatformDev's own grants, operator-applied (Decision 223)",
    "platform_security_admin_policy.tf": "the detector's, Decision 202",
    "platform_security_alarms.tf": "the detector's, Decision 202",
    "platform_security_heartbeat_canary.tf": "the detector's, Decision 202",
    "platform_security_trail.tf": "the detector's, Decision 202",
}
