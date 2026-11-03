"""The repository's GitHub identity, resolved once per call (rec-3903; breakage_map.owner_literal_defaults).

Every script that queries GitHub reads the repository from GITHUB_REPOSITORY (set by GitHub Actions) when it is a
well-formed owner/name, else FALLBACK_FULL_NAME, so CI callers follow the GitHub transfer at once and local callers
keep the fallback until the post-transfer sweep moves it (docs/contracts/github-org-transfer.yaml). Never raises.
"""

from __future__ import annotations

import os

FALLBACK_FULL_NAME = "benjamin-blake/theseus"


def full_name() -> str:
    """GITHUB_REPOSITORY stripped when it is exactly owner/name with both parts non-empty, else the fallback."""
    value = os.environ.get("GITHUB_REPOSITORY", "").strip()
    parts = value.split("/")
    return value if len(parts) == 2 and all(parts) else FALLBACK_FULL_NAME


def owner_and_name() -> tuple[str, str]:
    owner, name = full_name().split("/")
    return owner, name


def resolve(owner: str | None, repo: str | None) -> tuple[str, str]:
    """Fill only the missing parts from the resolved identity; explicit arguments win."""
    resolved_owner, resolved_name = owner_and_name()
    return owner or resolved_owner, repo or resolved_name
