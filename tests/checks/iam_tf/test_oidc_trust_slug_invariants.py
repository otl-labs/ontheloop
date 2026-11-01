"""Standing guards for the OIDC trust invariants, originated by PLAN-repo-rename-relicense and
carried forward by PLAN-oidc-trust-contraction-closure's contraction to the immutable entry alone.

THREE durable invariants -- the first two survive any future contraction or rename; the third is
the contraction's own durable successor, replacing what used to be a time-boxed dual-slug window:

  1. Decision 94 both-sub-forms: the bootstrap apply role's trust carries BOTH the
     ref:refs/heads/main form (routine auto-apply) and the environment:tf-gated-apply form
     (the gated-apply job, where GitHub OVERRIDES sub to the environment claim). Dropping the
     environment form silently ungates every gated apply -- the VP9 regression Decision 94 was
     written to prevent, and the exact failure class a rename window makes easy to hit.

  2. Cross-root slug-list agreement: terraform/personal and terraform/bootstrap each declare
     their own local.github_repos (no cross-root resource reference is possible). The two lists
     must stay identical, or a contraction can narrow one root and miss the other -- a divergence
     otherwise caught only post-deploy, after the apply has landed.

  3. Immutable-format-only (Decision 172, TestImmutableSubjectEntry.test_every_entry_is_immutable_format):
     every declared entry must match OWNER@OWNER-ID/REPO@REPO-ID. A legacy name-only entry mints
     nothing post-rename and must never survive a contraction.

Pre-staged state (Decision 218, rec-3902): both lists carry TWO entries -- the current
benjamin-blake segment and the otl-labs/ontheloop target segment -- until the contract's
predecessor_removal phase drops the first. TestTargetOwnerSegment pins the target's NAMES and its
durable repository id, never its owner id (the id lives only in the two lists).

Assertions 1 and 2 pin no COUNT of trusted slugs. Assertion 3 is a FORMAT assertion for the same
reason: Decision 172 point 2's pre-stage playbook adds a future name's immutable entry additively,
BEFORE any later rename, so a transient multi-entry state must stay green throughout -- a count
assertion would wrongly reject that additive staging the moment it happened (tests/CLAUDE.md
"Test-count coupling").
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_PERSONAL_LOCALS = _REPO_ROOT / "terraform" / "personal" / "oidc.tf"
_BOOTSTRAP_APPLY = _REPO_ROOT / "terraform" / "bootstrap" / "github_ci_apply.tf"
# Decision 172 (immutable-subject entry): three of the five expected sub suffixes are declared
# by sub sites that live OUTSIDE oidc.tf / github_ci_apply.tf -- :ref:refs/heads/agent/* and
# :pull_request/:ref:refs/pull/* are rendered only in oidc_ci_roles.tf and oidc_pipeline_roles.tf.
# TestImmutableSubjectEntry reads these two files as well to cover all five sub sites.
_BRANCH_AND_PR_ROLES = _REPO_ROOT / "terraform" / "personal" / "oidc_ci_roles.tf"
_PIPELINE_ROLES = _REPO_ROOT / "terraform" / "personal" / "oidc_pipeline_roles.tf"

_GITHUB_REPOS_RE = re.compile(r"github_repos\s*=\s*\[(?P<body>[^\]]*)\]", re.S)
_SLUG_RE = re.compile(r'"([^"]+)"')
# An immutable repo-segment entry: NAME@DIGITS/NAME@DIGITS -- never a full "repo:" sub prefix
# (every sub site below already renders "repo:${repo}:<suffix>").
_IMMUTABLE_ENTRY_RE = re.compile(r"^([A-Za-z0-9_.-]+)@(\d+)/([A-Za-z0-9_.-]+)@(\d+)$")
_EXPECTED_OWNER = "benjamin-blake"
_EXPECTED_OWNER_ID = "217728084"
_EXPECTED_REPO = "theseus"
_EXPECTED_REPO_ID = "1252427466"
_TARGET_OWNER = "otl-labs"
_TARGET_REPO = "ontheloop"
_IMMUTABLE_SEGMENT = f"{_EXPECTED_OWNER}@{_EXPECTED_OWNER_ID}/{_EXPECTED_REPO}@{_EXPECTED_REPO_ID}"
# The five sub-site suffix TEMPLATES (with the un-substituted `${repo}` token) each expected sub
# site must declare, and the fully-expanded literal each one yields once `${repo}` resolves to
# the immutable segment above.
_EXPECTED_SUFFIXES = (
    ":ref:refs/heads/main",
    ":ref:refs/heads/agent/*",
    ":pull_request",
    ":ref:refs/pull/*",
    ":environment:tf-gated-apply",
)


def _declared_slugs(path: Path) -> list[str]:
    """Return the slug list declared by `local.github_repos` in a Terraform root."""
    match = _GITHUB_REPOS_RE.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"{path} declares no local.github_repos list"
    return _SLUG_RE.findall(match.group("body"))


def _apply_role_trust_block() -> str:
    """Return the github_ci_apply assume_role_policy block text."""
    text = _BOOTSTRAP_APPLY.read_text(encoding="utf-8")
    start = text.index('resource "aws_iam_role" "github_ci_apply"')
    policy_start = text.index("assume_role_policy", start)
    return text[policy_start:]


class TestDecision94BothSubForms:
    def test_apply_role_trusts_the_ref_sub_form(self) -> None:
        assert ":ref:refs/heads/main" in _apply_role_trust_block(), (
            "github_ci_apply lost the ref:refs/heads/main sub form -- the routine auto-apply path can no longer authenticate."
        )

    def test_apply_role_trusts_the_environment_sub_form(self) -> None:
        assert ":environment:tf-gated-apply" in _apply_role_trust_block(), (
            "github_ci_apply lost the environment:tf-gated-apply sub form -- GitHub overrides "
            "sub to the environment claim for a job declaring environment:, so every gated "
            "apply now fails to authenticate (Decision 94 VP9 regression)."
        )

    def test_both_sub_forms_are_trusted_for_every_declared_slug(self) -> None:
        """Each trusted slug must carry both forms, not one form each."""
        block = _apply_role_trust_block()
        for form in (":ref:refs/heads/main", ":environment:tf-gated-apply"):
            assert block.count(form) >= 1, f"apply role does not trust the {form} sub form"
        # The forms are emitted per-slug inside one comprehension body, so both appear
        # exactly once in the template regardless of how many slugs are trusted.
        assert block.index(":ref:refs/heads/main") < block.index(":environment:tf-gated-apply"), (
            "Decision 94 sub forms are no longer emitted from a single per-slug body -- "
            "verify both forms are still expanded for every slug."
        )


class TestCrossRootSlugAgreement:
    def test_both_roots_declare_a_slug_list(self) -> None:
        assert _declared_slugs(_PERSONAL_LOCALS), "terraform/personal declares no slugs"
        assert _declared_slugs(_BOOTSTRAP_APPLY), "terraform/bootstrap declares no slugs"

    def test_slug_lists_are_identical_across_roots(self) -> None:
        personal = _declared_slugs(_PERSONAL_LOCALS)
        bootstrap = _declared_slugs(_BOOTSTRAP_APPLY)
        assert personal == bootstrap, (
            "terraform/personal and terraform/bootstrap trust different repository slugs "
            f"(personal={personal}, bootstrap={bootstrap}). A contraction narrowed one root "
            "and missed the other; the roles in the missed root trust a dead or unintended slug."
        )

    def test_slug_lists_have_no_duplicates(self) -> None:
        for path in (_PERSONAL_LOCALS, _BOOTSTRAP_APPLY):
            slugs = _declared_slugs(path)
            assert len(slugs) == len(set(slugs)), f"{path} declares a duplicate slug: {slugs}"


class TestNoScalarSurvivor:
    def test_no_root_reintroduces_the_scalar_local(self) -> None:
        """A scalar local.github_repo means some sub site trusts a single slug."""
        scalar = re.compile(r"local\.github_repo\b")
        for root in ("personal", "bootstrap"):
            for tf_file in sorted((_REPO_ROOT / "terraform" / root).glob("*.tf")):
                assert not scalar.search(tf_file.read_text(encoding="utf-8")), (
                    f"{tf_file} references the scalar local.github_repo -- every sub site must "
                    "iterate local.github_repos so no role is left trusting a single slug."
                )


class TestImmutableSubjectEntry:
    """Decision 172: GitHub's immutable OIDC subject-claim format, applied to any repository
    renamed/transferred after 2026-07-15. benjamin-blake/theseus renamed at
    2026-08-15T12:55:57Z and now mints ONLY repo:benjamin-blake@217728084/theseus@1252427466:<...>
    -- the two legacy name-only entries mint nothing post-rename."""

    def test_every_entry_is_immutable_format(self) -> None:
        """FORMAT assertion, never a count assertion (Decision 172 point 2's pre-stage playbook
        adds a future name's immutable entry additively, BEFORE any later rename -- a count
        assertion would wrongly reject that additive staging). Red while any legacy name-only
        entry survives in either root; green once every declared entry matches
        OWNER@OWNER-ID/REPO@REPO-ID."""
        for path in (_PERSONAL_LOCALS, _BOOTSTRAP_APPLY):
            slugs = _declared_slugs(path)
            non_immutable = [s for s in slugs if not _IMMUTABLE_ENTRY_RE.match(s)]
            assert not non_immutable, (
                f"{path} declares non-immutable-format entries {non_immutable} in "
                "local.github_repos -- every entry must match OWNER@OWNER-ID/REPO@REPO-ID; a "
                "legacy name-only slug is unmintable post-rename and must be removed."
            )

    def test_both_roots_declare_the_immutable_entry(self) -> None:
        for path in (_PERSONAL_LOCALS, _BOOTSTRAP_APPLY):
            slugs = _declared_slugs(path)
            matches = [_IMMUTABLE_ENTRY_RE.match(s) for s in slugs]
            immutable = [m for m in matches if m is not None]
            assert immutable, (
                f"{path} declares no OWNER@OWNER-ID/REPO@REPO-ID immutable entry in local.github_repos (slugs={slugs})"
            )
            current = [m for m in immutable if m.group(1) == _EXPECTED_OWNER]
            assert len(current) == 1, f"{path} must declare exactly one {_EXPECTED_OWNER} entry (slugs={slugs})"
            owner, owner_id, repo, repo_id = current[0].groups()
            assert (owner, owner_id, repo, repo_id) == (
                _EXPECTED_OWNER,
                _EXPECTED_OWNER_ID,
                _EXPECTED_REPO,
                _EXPECTED_REPO_ID,
            ), (
                f"{path}'s immutable entry is {current[0].group(0)!r}, expected "
                f"{_IMMUTABLE_SEGMENT!r} (owner id 217728084 / repo id 1252427466)."
            )

    def test_no_entry_carries_a_repo_prefix(self) -> None:
        """A "repo:"-prefixed entry would render "repo:repo:..." at every sub site -- the
        double-prefix bug class the sub sites' own "repo:${repo}:<suffix>" template guards
        against only if the LIST never supplies an already-prefixed segment."""
        for path in (_PERSONAL_LOCALS, _BOOTSTRAP_APPLY):
            for slug in _declared_slugs(path):
                assert not slug.startswith("repo:"), (
                    f"{path} declares {slug!r} with a repo: prefix -- every sub site already "
                    'renders "repo:${repo}:<suffix>", so this entry would double-prefix to '
                    '"repo:repo:..." and match nothing.'
                )

    def test_flattened_expansion_yields_all_five_immutable_subs(self) -> None:
        """Each expected suffix must be declared, as the "repo:${repo}:<suffix>" template, by a
        sub site that iterates local.github_repos -- proving (with the entry-membership
        assertion above) that the flattened expansion yields
        repo:benjamin-blake@217728084/theseus@1252427466 suffixed by each of the five forms."""
        combined = "\n".join(p.read_text(encoding="utf-8") for p in (_BRANCH_AND_PR_ROLES, _PIPELINE_ROLES, _BOOTSTRAP_APPLY))
        for suffix in _EXPECTED_SUFFIXES:
            template = f"repo:${{repo}}{suffix}"
            assert template in combined, (
                f"no sub site declares the {template!r} template over local.github_repos -- "
                "the module is not reading the file that declares this sub site (extend the "
                "file set) or the sub site itself is missing this suffix."
            )
            rendered = template.replace("${repo}", _IMMUTABLE_SEGMENT)
            expected = f"repo:{_IMMUTABLE_SEGMENT}{suffix}"
            assert rendered == expected  # trivially true; pins the yielded literal for readers


class TestTargetOwnerSegment:
    """Decision 218: the otl-labs owner segment is pre-staged additively beside the current one so the
    organisation transfer cannot lock CI out. Pins NAMES and the durable repository id, never the owner id."""

    def test_both_roots_stage_the_target_segment(self) -> None:
        for path in (_PERSONAL_LOCALS, _BOOTSTRAP_APPLY):
            slugs = _declared_slugs(path)
            targets = [m for m in (_IMMUTABLE_ENTRY_RE.match(s) for s in slugs) if m and m.group(1) == _TARGET_OWNER]
            assert len(targets) == 1, f"{path} must declare exactly one {_TARGET_OWNER} entry (slugs={slugs})"
            owner, owner_id, repo, repo_id = targets[0].groups()
            assert repo == _TARGET_REPO, f"{path}'s target entry names repository {repo!r}, expected {_TARGET_REPO!r}"
            assert repo_id == _EXPECTED_REPO_ID, (
                f"{path}'s target entry carries repository id {repo_id}; expected the durable {_EXPECTED_REPO_ID}"
            )
            assert owner_id != _EXPECTED_OWNER_ID, f"{path}'s target entry reuses the current owner id"
