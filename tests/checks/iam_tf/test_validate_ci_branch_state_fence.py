"""Mirror test of scripts/checks/iam_tf/validate_ci_branch_state_fence.py (rec-4632)."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.checks import registry
from scripts.checks.iam_tf import _manifest
from scripts.checks.iam_tf import validate_ci_branch_state_fence as mod

_ROOT = Path(__file__).resolve().parents[3]
_B = "${aws_s3_bucket.data_lake.arn}"


def _hcl(
    actions=mod.FENCE_ACTIONS,
    prefixes=mod.FENCE_PREFIXES,
    effect="Deny",
    condition="",
    present=True,
    source="",
    extra="",
    comment="",
) -> str:
    acts = ", ".join(f'"{a}"' for a in sorted(actions))
    res = ", ".join(f'"{_B}/{p}"' for p in prefixes)
    stmt = (
        f'statement {{\n sid = "{mod.FENCE_SID}"\n effect = "{effect}"\n actions = [{acts}]\n'
        f" resources = [{res}{comment}]\n{extra}{condition}\n}}"
        if present
        else ""
    )
    return (
        'data "aws_iam_policy_document" "github_ci_branch" {\n'
        f"{source}\n"
        'statement {\n sid = "S3ReadWrite"\n effect = "Allow"\n actions = ["s3:GetObject"]\n'
        f' resources = ["{_B}/*"]\n}}\n{stmt}\n}}\n'
        'resource "aws_iam_role_policy" "github_ci_branch" {\n'
        ' name = "x"\n role = aws_iam_role.github_ci_branch.id\n'
        " policy = data.aws_iam_policy_document.github_ci_branch.json\n}\n"
    )


def test_synthetic_baseline_is_green() -> None:
    assert mod.fence_problems(_hcl()) == []


def test_live_tree_passes() -> None:
    text = mod._vir._read_root_text(_ROOT / "terraform" / "personal")
    assert mod.fence_problems(text) == []


@pytest.mark.parametrize("verb", sorted(mod.FENCE_ACTIONS))
def test_red_on_each_dropped_verb(verb: str) -> None:
    assert mod.fence_problems(_hcl(actions=mod.FENCE_ACTIONS - {verb}))


@pytest.mark.parametrize("prefix", mod.FENCE_PREFIXES)
def test_red_on_each_dropped_prefix(prefix: str) -> None:
    assert mod.fence_problems(_hcl(prefixes=tuple(p for p in mod.FENCE_PREFIXES if p != prefix)))


def test_red_on_allow_effect() -> None:
    assert mod.fence_problems(_hcl(effect="Allow"))


def test_red_on_added_condition() -> None:
    cond = ' condition {\n test = "StringEquals"\n variable = "aws:PrincipalTag/x"\n values = ["y"]\n }'
    assert mod.fence_problems(_hcl(condition=cond))


def test_red_when_statement_absent() -> None:
    assert mod.fence_problems(_hcl(present=False))


def test_red_on_unresolvable_source_document() -> None:
    problems = mod.fence_problems(_hcl(source="source_policy_documents = [data.aws_iam_policy_document.missing.json]"))
    assert any("unresolvable" in p for p in problems)


def test_s3_wildcard_action_satisfies() -> None:
    assert mod.fence_problems(_hcl(actions={"s3:*"})) == []


def test_registered_pre_and_full() -> None:
    name = "validate_ci_branch_state_fence"
    assert name in {s.name for s in registry.pre_sequence() if s.kind == "check"}
    assert next(e for e in _manifest.ENTRIES if e.name == name).full_segment == "full_after_lint"


def test_red_when_prefix_only_in_comment() -> None:
    narrowed = tuple(p for p in mod.FENCE_PREFIXES if p != "tfplan/*")
    assert mod.fence_problems(_hcl(prefixes=narrowed, comment=f'\n # "{_B}/tfplan/*"\n'))


@pytest.mark.parametrize("extra", ['not_resources = ["x"]', 'not_actions = ["s3:Get*"]', 'dynamic "condition" {}'])
def test_red_on_unparseable_constructs(extra: str) -> None:
    assert any("cannot parse" in p for p in mod.fence_problems(_hcl(extra=extra + "\n")))


def test_red_on_override_policy_documents() -> None:
    assert mod.fence_problems(_hcl(source="override_policy_documents = [data.aws_iam_policy_document.x.json]"))


def test_red_when_fence_only_in_another_role_document() -> None:
    other = _hcl().replace("github_ci_branch", "other_role")
    plain = _hcl(present=False)
    assert mod.fence_problems(plain + other)


def _tree(tmp_path: Path, text: str | None) -> Path:
    d = tmp_path / "terraform" / "personal"
    d.mkdir(parents=True)
    if text is not None:
        (d / "a.tf").write_text(text, encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize(("text", "red"), [(_hcl(), False), (_hcl(present=False), True), (None, True)])
def test_entry_point(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, text: str | None, red: bool) -> None:
    monkeypatch.setattr(mod._common, "ROOT", _tree(tmp_path, text))
    failed: list[str] = []
    mod.validate_ci_branch_state_fence(failed)
    assert bool(failed) is red


def test_red_when_role_policy_mapping_absent() -> None:
    problems = mod.fence_problems(_hcl().split("resource ")[0])
    assert any("cannot resolve" in p for p in problems)
