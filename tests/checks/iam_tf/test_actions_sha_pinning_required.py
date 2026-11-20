"""Static guard: terraform/github requires SHA-pinned actions (PLAN-actions-sha-pin-native-policy, rec-5290)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.fixtures.terraform_hcl_blocks import find_resource_block, tf_dir_file_texts

_TF_GITHUB_DIR = Path(__file__).resolve().parents[3] / "terraform" / "github"
_TYPE = "github_actions_repository_permissions"
_ATTR = re.compile(r"^\s*sha_pinning_required\s*=\s*true\s*$", re.MULTILINE)


def requires_sha_pinning(texts: dict[str, str]) -> bool:
    for text in texts.values():
        if f'resource "{_TYPE}" "this"' in text:
            return bool(_ATTR.search(find_resource_block(text, _TYPE, "this")))
    return False


def test_repository_requires_sha_pinned_actions() -> None:
    assert requires_sha_pinning(tf_dir_file_texts(_TF_GITHUB_DIR))


@pytest.mark.parametrize(
    "body",
    ["", "  sha_pinning_required = false\n", "  # sha_pinning_required = true\n"],
)
def test_absent_or_false_attribute_is_refused(body: str) -> None:
    assert not requires_sha_pinning({"x.tf": f'resource "{_TYPE}" "this" {{\n{body}}}\n'})
