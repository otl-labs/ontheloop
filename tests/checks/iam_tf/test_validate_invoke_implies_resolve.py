"""Tests for validate_invoke_implies_resolve() (T2.34:c2, Decision 104)."""

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks.iam_tf.validate_invoke_implies_resolve import (
    _extract_block,
    _parse_role_policy_map,
    validate_invoke_implies_resolve,
)


def _write_oidc_tf(tmp_path: Path, body: str) -> None:
    oidc_path = tmp_path / "terraform" / "personal" / "oidc.tf"
    oidc_path.parent.mkdir(parents=True, exist_ok=True)
    oidc_path.write_text(body, encoding="utf-8")


class TestValidateInvokeImpliesResolve:
    """Tests for validate_invoke_implies_resolve() (T2.34:c2, Decision 104)."""

    def test_invoke_implies_resolve_passes_on_real_composed_oidc_tf(self) -> None:
        """The real terraform/personal/oidc.tf: all invoking roles (branch, pr, plan, drift)
        resolve SSM via source_policy_documents composition -- zero failures."""
        failed: list[str] = []
        validate_invoke_implies_resolve(failed)
        assert failed == []

    def test_invoke_implies_resolve_vacuous_pass_for_non_invoking_role(self, tmp_path: Path) -> None:
        """A role whose composed statements never invoke the DuckLake reader/writer passes
        without needing SSM at all."""
        _write_oidc_tf(
            tmp_path,
            """
            data "aws_iam_policy_document" "github_ci_noop" {
              statement {
                sid       = "S3List"
                effect    = "Allow"
                actions   = ["s3:ListBucket"]
                resources = ["*"]
              }
            }

            resource "aws_iam_role_policy" "github_ci_noop" {
              name   = "agent-platform-github-ci-noop"
              role   = aws_iam_role.github_ci_noop.id
              policy = data.aws_iam_policy_document.github_ci_noop.json
            }
            """,
        )
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert failed == []

    def test_invoke_implies_resolve_fails_when_composition_omits_ssm(self, tmp_path: Path) -> None:
        """A role that invokes the ducklake writer but never composes an SSM-granting
        document is a genuine T2.34:c2 violation (rec-2363-class drift)."""
        _write_oidc_tf(
            tmp_path,
            """
            data "aws_iam_policy_document" "github_ci_violator" {
              statement {
                sid    = "DuckLakeInvokeCI"
                effect = "Allow"
                actions = ["lambda:InvokeFunction", "lambda:InvokeFunctionUrl"]
                resources = [
                  aws_lambda_function.ducklake_writer.arn,
                  "${aws_lambda_function.ducklake_writer.arn}:*",
                ]
              }
            }

            resource "aws_iam_role_policy" "github_ci_violator" {
              name   = "agent-platform-github-ci-violator"
              role   = aws_iam_role.github_ci_violator.id
              policy = data.aws_iam_policy_document.github_ci_violator.json
            }
            """,
        )
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert len(failed) == 1
        assert "github_ci_violator" in failed[0]
        assert "lacks ssm:Get*" in failed[0]

    def test_invoke_implies_resolve_passes_via_transitive_source_composition(self, tmp_path: Path) -> None:
        """A role composing a document that itself sources the SSM fragment (two levels of
        source_policy_documents) still resolves -- composition is followed transitively."""
        _write_oidc_tf(
            tmp_path,
            """
            data "aws_iam_policy_document" "ci_ssm_refresh_read" {
              statement {
                sid       = "SSMParameterRead"
                effect    = "Allow"
                actions   = ["ssm:Get*", "ssm:Describe*", "ssm:List*"]
                resources = ["arn:aws:ssm:eu-west-2:1234567890:parameter/agent-platform/*"]
              }
            }

            data "aws_iam_policy_document" "ci_full_refresh_read" {
              source_policy_documents = [data.aws_iam_policy_document.ci_ssm_refresh_read.json]

              statement {
                sid       = "TfstateRead"
                effect    = "Allow"
                actions   = ["s3:GetObject"]
                resources = ["arn:aws:s3:::agent-platform-data-lake/tfstate/personal/*"]
              }
            }

            data "aws_iam_policy_document" "github_ci_composer" {
              source_policy_documents = [data.aws_iam_policy_document.ci_full_refresh_read.json]

              statement {
                sid    = "DuckLakeWriterInvoke"
                effect = "Allow"
                actions = ["lambda:InvokeFunction"]
                resources = [
                  aws_lambda_function.ducklake_writer.arn,
                ]
              }
            }

            resource "aws_iam_role_policy" "github_ci_composer" {
              name   = "agent-platform-github-ci-composer"
              role   = aws_iam_role.github_ci_composer.id
              policy = data.aws_iam_policy_document.github_ci_composer.json
            }
            """,
        )
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert failed == []

    def test_invoke_implies_resolve_fails_loud_when_no_policy_documents_found(self, tmp_path: Path) -> None:
        """An oidc.tf that no longer matches the expected HCL shape fails loud rather than
        silently passing vacuously (Decision 55)."""
        _write_oidc_tf(tmp_path, "# empty -- no aws_iam_policy_document or aws_iam_role_policy blocks\n")
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert len(failed) == 1
        assert "no aws_iam_policy_document / aws_iam_role_policy blocks found" in failed[0]

    def test_resolves_composition_split_across_sibling_tf_files(self, tmp_path: Path) -> None:
        """terraform-decompose-oidc-rename: the SSM fragment and the invoking role's policy
        document now live in DIFFERENT files within terraform/personal/. Root-wide parsing must
        follow the source_policy_documents composition across the file boundary."""
        personal_dir = tmp_path / "terraform" / "personal"
        personal_dir.mkdir(parents=True, exist_ok=True)
        (personal_dir / "oidc.tf").write_text(
            """
            data "aws_iam_policy_document" "ci_ssm_refresh_read" {
              statement {
                sid       = "SSMParameterRead"
                effect    = "Allow"
                actions   = ["ssm:Get*", "ssm:Describe*", "ssm:List*"]
                resources = ["arn:aws:ssm:eu-west-2:1234567890:parameter/agent-platform/*"]
              }
            }
            """,
            encoding="utf-8",
        )
        (personal_dir / "oidc_pipeline_roles.tf").write_text(
            """
            data "aws_iam_policy_document" "github_ci_planner" {
              source_policy_documents = [data.aws_iam_policy_document.ci_ssm_refresh_read.json]

              statement {
                sid    = "DuckLakeWriterInvoke"
                effect = "Allow"
                actions = ["lambda:InvokeFunction"]
                resources = [aws_lambda_function.ducklake_writer.arn]
              }
            }

            resource "aws_iam_role_policy" "github_ci_planner" {
              name   = "agent-platform-github-ci-planner"
              role   = aws_iam_role.github_ci_planner.id
              policy = data.aws_iam_policy_document.github_ci_planner.json
            }
            """,
            encoding="utf-8",
        )
        # RED proof: reading only oidc.tf (the pre-repoint single-file behaviour) cannot see the
        # invoking role at all -- it lives in the sibling file.
        single_file_text = (personal_dir / "oidc.tf").read_text(encoding="utf-8")
        single_file_role_policy = _parse_role_policy_map(single_file_text)
        assert "github_ci_planner" not in single_file_role_policy

        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert failed == [], failed

    def test_decoy_sibling_document_does_not_satisfy_a_different_roles_ssm_requirement(self, tmp_path: Path) -> None:
        """A decoy document in an unrelated sibling file that also grants ssm:Get* must not be
        picked up for a role that never composed it -- resolution stays keyed to the actual
        source_policy_documents graph, not "any SSM grant found somewhere in the root"."""
        personal_dir = tmp_path / "terraform" / "personal"
        personal_dir.mkdir(parents=True, exist_ok=True)
        (personal_dir / "decoy.tf").write_text(
            """
            data "aws_iam_policy_document" "unrelated_ssm_grant" {
              statement {
                sid       = "SSMParameterRead"
                effect    = "Allow"
                actions   = ["ssm:Get*"]
                resources = ["arn:aws:ssm:eu-west-2:1234567890:parameter/agent-platform/*"]
              }
            }
            """,
            encoding="utf-8",
        )
        (personal_dir / "oidc.tf").write_text(
            """
            data "aws_iam_policy_document" "github_ci_violator" {
              statement {
                sid    = "DuckLakeInvokeCI"
                effect = "Allow"
                actions = ["lambda:InvokeFunction"]
                resources = [aws_lambda_function.ducklake_writer.arn]
              }
            }

            resource "aws_iam_role_policy" "github_ci_violator" {
              name   = "agent-platform-github-ci-violator"
              role   = aws_iam_role.github_ci_violator.id
              policy = data.aws_iam_policy_document.github_ci_violator.json
            }
            """,
            encoding="utf-8",
        )
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert len(failed) == 1
        assert "github_ci_violator" in failed[0]
        assert "lacks ssm:Get*" in failed[0]

    def test_unresolvable_document_fails_loud_instead_of_vacuous_pass(self, tmp_path: Path) -> None:
        """Decision 129 clause 4 -- the live hazard: a role whose OWN policy document reference
        cannot be resolved (a repoint left dangling, or a typo) must FAIL, never take the
        'PASS (vacuous)' branch that a genuinely-non-invoking role legitimately gets. Distinguished
        from test_invoke_implies_resolve_vacuous_pass_for_non_invoking_role, whose document DOES
        resolve and simply contains no invoke grant."""
        _write_oidc_tf(
            tmp_path,
            """
            data "aws_iam_policy_document" "unrelated" {
              statement {
                sid       = "S3List"
                effect    = "Allow"
                actions   = ["s3:ListBucket"]
                resources = ["*"]
              }
            }

            resource "aws_iam_role_policy" "github_ci_dangling" {
              name   = "agent-platform-github-ci-dangling"
              role   = aws_iam_role.github_ci_dangling.id
              policy = data.aws_iam_policy_document.github_ci_dangling.json
            }
            """,
        )
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert len(failed) == 1
        assert "github_ci_dangling" in failed[0]
        assert "unresolvable policy document" in failed[0]

    def test_unresolvable_nested_source_fails_loud(self, tmp_path: Path) -> None:
        """The document itself resolves, but a NESTED source it composes does not (e.g. the shared
        SSM fragment was left in a file the split repoint no longer reads) -- must fail loud rather
        than silently dropping the missing source's statements and evaluating on a partial set."""
        _write_oidc_tf(
            tmp_path,
            """
            data "aws_iam_policy_document" "github_ci_partial" {
              source_policy_documents = [data.aws_iam_policy_document.ci_ssm_refresh_read.json]

              statement {
                sid    = "DuckLakeInvokeCI"
                effect = "Allow"
                actions = ["lambda:InvokeFunction"]
                resources = [aws_lambda_function.ducklake_writer.arn]
              }
            }

            resource "aws_iam_role_policy" "github_ci_partial" {
              name   = "agent-platform-github-ci-partial"
              role   = aws_iam_role.github_ci_partial.id
              policy = data.aws_iam_policy_document.github_ci_partial.json
            }
            """,
        )
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert len(failed) == 1
        assert "github_ci_partial" in failed[0]
        assert "unresolvable policy document" in failed[0]
        assert "ci_ssm_refresh_read" in failed[0]

    def test_extract_block_raises_on_unbalanced_braces(self) -> None:
        """_extract_block is a depth-counted brace matcher; malformed HCL (an unterminated
        block) must raise rather than silently return a truncated or wrong body."""
        text = "prefix { unterminated"
        with pytest.raises(ValueError, match="Unbalanced braces"):
            _extract_block(text, text.index("{"))

    def test_resolve_statements_cycle_guard_short_circuits_a_revisited_document(self, tmp_path: Path) -> None:
        """Two documents that source each other (a real HCL authoring mistake, not malice) must
        not infinitely recurse -- the _seen guard returns [] the second time a doc_name is
        revisited, and the composition still resolves via the FIRST visit's own statements."""
        _write_oidc_tf(
            tmp_path,
            """
            data "aws_iam_policy_document" "cycle_a" {
              source_policy_documents = [data.aws_iam_policy_document.cycle_b.json]
              statement {
                sid       = "HarmlessA"
                effect    = "Allow"
                actions   = ["s3:ListBucket"]
                resources = ["*"]
              }
            }

            data "aws_iam_policy_document" "cycle_b" {
              source_policy_documents = [data.aws_iam_policy_document.cycle_a.json]
              statement {
                sid       = "HarmlessB"
                effect    = "Allow"
                actions   = ["s3:ListBucket"]
                resources = ["*"]
              }
            }

            resource "aws_iam_role_policy" "github_ci_cycle" {
              name   = "agent-platform-github-ci-cycle"
              role   = aws_iam_role.github_ci_cycle.id
              policy = data.aws_iam_policy_document.cycle_a.json
            }
            """,
        )
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert failed == [], failed

    def test_invoke_implies_resolve_fails_loud_when_personal_dir_has_no_tf_files(self, tmp_path: Path) -> None:
        """Root-wide parsing (terraform-decompose-oidc-rename): an empty terraform/personal/ (no
        *.tf files at all) fails loud via the same read-guard as an unreadable root -- distinct
        from the 'no policy documents parsed' branch, which needs at least one readable file."""
        personal_dir = tmp_path / "terraform" / "personal"
        personal_dir.mkdir(parents=True, exist_ok=True)
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert len(failed) == 1
        assert "cannot read any *.tf file" in failed[0]

    def test_invoke_implies_resolve_covers_the_telemetry_writer(self, tmp_path: Path) -> None:
        """A role invoking only the telemetry writer escaped the check while the markers named only the
        shared writer and reader; without ssm:Get* it now fails, and with it the same role passes."""
        statement = """
              statement {
                sid       = "TelemetryWriterInvokeCI"
                effect    = "Allow"
                actions   = ["lambda:InvokeFunction"]
                resources = [aws_lambda_function.ducklake_telemetry_writer.arn]
              }
        """
        ssm = """
              statement {
                sid       = "SSMParameterRead"
                effect    = "Allow"
                actions   = ["ssm:Get*"]
                resources = ["arn:aws:ssm:eu-west-2:1234567890:parameter/agent-platform/*"]
              }
        """

        def _tf(extra: str) -> str:
            return f"""
            data "aws_iam_policy_document" "github_ci_telemetry" {{
              {statement}
              {extra}
            }}

            resource "aws_iam_role_policy" "github_ci_telemetry" {{
              name   = "agent-platform-github-ci-telemetry"
              role   = aws_iam_role.github_ci_telemetry.id
              policy = data.aws_iam_policy_document.github_ci_telemetry.json
            }}
            """

        _write_oidc_tf(tmp_path, _tf(""))
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert len(failed) == 1 and "github_ci_telemetry" in failed[0] and "lacks ssm:Get*" in failed[0]

        _write_oidc_tf(tmp_path, _tf(ssm))
        failed = []
        with patch("scripts.checks._common.ROOT", tmp_path):
            validate_invoke_implies_resolve(failed)
        assert failed == []
