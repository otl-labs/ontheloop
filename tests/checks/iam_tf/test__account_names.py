"""Mirror test for scripts/checks/iam_tf/_account_names.py."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.iam_tf import _account_names as an

REAL = Path(__file__).resolve().parents[3]
BOOT = REAL / "terraform" / "bootstrap"
NAMES = (BOOT / "account_names.tf").read_text(encoding="utf-8")


def test_sandbox_render_of_all_three_forms() -> None:
    text = 'a = "${local.data_lake_bucket_arn}/x"\nb = local.data_lake_bucket_name\nc = [local.ducklake_dr_bucket_arn]\n'
    out = an.render(text)
    assert 'a = "arn:aws:s3:::agent-platform-data-lake/x"' in out
    assert 'b = "agent-platform-data-lake"' in out
    assert 'c = ["arn:aws:s3:::agent-platform-ducklake-catalog-dr"]' in out


def test_sit_render_adds_the_suffix() -> None:
    assert an.render("b = local.bootstrap_state_bucket_name", env="sit") == 'b = "agent-platform-bootstrap-tfstate-sit"'


def test_parenthesised_key_becomes_a_quoted_string() -> None:
    assert an.render("(local.bootstrap_state_bucket_arn) = 1") == '"arn:aws:s3:::agent-platform-bootstrap-tfstate" = 1'


def test_other_tokens_are_untouched() -> None:
    text = (
        'x = local.other_name\ny = "${local.ducklake_catalog_dr_function}"\n'
        "z = aws_s3_bucket.data_lake.arn\nw = foo.local.data_lake_bucket_name"
    )
    assert an.render(text) == text


def test_absent_account_names_is_the_identity(tmp_path: Path) -> None:
    with patch("scripts.checks._common.ROOT", tmp_path):
        assert an.render("b = local.data_lake_bucket_name") == "b = local.data_lake_bucket_name"


def test_an_account_names_file_without_assignments_is_the_identity(tmp_path: Path) -> None:
    (tmp_path / "account_names.tf").write_text("# nothing\n", encoding="utf-8")
    assert an.render("b = local.data_lake_bucket_name", root=tmp_path) == "b = local.data_lake_bucket_name"


def test_values_are_read_at_call_time_from_a_patched_root(tmp_path: Path) -> None:
    patched = tmp_path / "terraform" / "bootstrap"
    patched.mkdir(parents=True)
    (patched / "account_names.tf").write_text(NAMES.replace("agent-platform-data-lake$", "other-lake$"), encoding="utf-8")
    with patch("scripts.checks._common.ROOT", tmp_path):
        assert an.render("b = local.data_lake_bucket_name") == 'b = "other-lake"'


def test_an_explicit_root_is_tried_first(tmp_path: Path) -> None:
    (tmp_path / "account_names.tf").write_text(NAMES.replace("agent-platform-data-lake$", "mine$"), encoding="utf-8")
    assert an.account_names_files(tmp_path)[0] == tmp_path / "account_names.tf"
    assert an.render("b = local.data_lake_bucket_name", root=tmp_path) == 'b = "mine"'


def test_suffix_map_and_validation_parsing() -> None:
    assert an.suffix_map(NAMES) == {"sandbox": "", "sit": "-sit"}
    assert an.suffix_map("nothing") == {}
    assert an.admitted_environments((BOOT / "variables.tf").read_text(encoding="utf-8")) == ["sandbox", "sit"]
    assert an.admitted_environments("nothing") == []


def test_name_locals_resolves_nested_locals_and_stops_on_a_cycle() -> None:
    cyc = 'locals {\n  name_suffix = { sandbox = "" }[var.environment]\n  a = "${local.b}"\n  b = "${local.a}"\n}\n'
    assert set(an.name_locals(cyc)) == {"a", "b"}
