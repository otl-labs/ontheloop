"""Tests for validate_deploy_channel_conformance() (Decision 104, Decision 125/126, T2.43).

VP step 1: a canned channel_class<->ignore_changes mismatch fixture asserts the check
FAILS (proves it is behavioural, not vacuous); a matching-state fixture asserts it PASSES.

Every test writes BOTH the ducklake AND the prod class fixtures (via _write's defaults) so a
scenario targeting one class doesn't spuriously fail on the other, unless the test is
specifically exercising a prod-class (or ducklake-class) mismatch.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from scripts.checks.ops_governance.validate_deploy_channel_conformance import (
    _actual_decoupled_state,
    _doc_state_from_channel_class,
    _extract_function_blocks,
    _prod_channels_complete,
    _taxonomy_state,
    validate_deploy_channel_conformance,
)

_TF_DECOUPLED = """
resource "aws_lambda_function" "writer" {
  function_name = "agent-platform-ducklake-writer"
  source_code_hash = try(filemd5("x.zip"), null)

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

resource "aws_lambda_function" "reader" {
  function_name = "agent-platform-ducklake-reader"
  source_code_hash = try(filemd5("y.zip"), null)

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}
"""

_TF_COUPLED = """
resource "aws_lambda_function" "writer" {
  function_name = "agent-platform-ducklake-writer"
  source_code_hash = try(filemd5("x.zip"), null)
}

resource "aws_lambda_function" "reader" {
  function_name = "agent-platform-ducklake-reader"
  source_code_hash = try(filemd5("y.zip"), null)
}
"""

_TF_MIXED = """
resource "aws_lambda_function" "writer" {
  function_name = "agent-platform-ducklake-writer"
  source_code_hash = try(filemd5("x.zip"), null)

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

resource "aws_lambda_function" "reader" {
  function_name = "agent-platform-ducklake-reader"
  source_code_hash = try(filemd5("y.zip"), null)
}
"""

# A COUPLED resource preceding a decoupled one: a block that over-runs its closing brace
# absorbs the next resource's ignore_changes and the partial rollout disappears.
_TF_MIXED_COUPLED_FIRST = """
resource "aws_lambda_function" "writer" {
  function_name = "agent-platform-ducklake-writer"
  source_code_hash = try(filemd5("x.zip"), null)
}

resource "aws_lambda_function" "reader" {
  function_name = "agent-platform-ducklake-reader"
  source_code_hash = try(filemd5("y.zip"), null)

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}
"""

# A nested environment/variables block that closes BEFORE the lifecycle block: a block that
# stops at the first inner closing brace loses the ignore_changes text entirely.
_TF_NESTED_BLOCK_DECOUPLED = """
resource "aws_lambda_function" "writer" {
  function_name = "agent-platform-ducklake-writer"
  source_code_hash = try(filemd5("x.zip"), null)

  environment {
    variables = {
      DUCKLAKE_STAGE = "prod"
    }
  }

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}
"""

_PROD_TF_DECOUPLED = """
resource "aws_lambda_function" "scheduled_agent_dispatcher" {
  function_name = "agent-platform-scheduled-agent-dispatcher"
  source_code_hash = try(filemd5("a.zip"), null)

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

resource "aws_lambda_function" "findings_processor" {
  function_name = "agent-platform-findings-processor"
  source_code_hash = try(filemd5("b.zip"), null)

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}
"""

_PROD_TF_COUPLED = """
resource "aws_lambda_function" "scheduled_agent_dispatcher" {
  function_name = "agent-platform-scheduled-agent-dispatcher"
  source_code_hash = try(filemd5("a.zip"), null)
}

resource "aws_lambda_function" "findings_processor" {
  function_name = "agent-platform-findings-processor"
  source_code_hash = try(filemd5("b.zip"), null)
}
"""

_PROD_TF_MIXED = """
resource "aws_lambda_function" "scheduled_agent_dispatcher" {
  function_name = "agent-platform-scheduled-agent-dispatcher"
  source_code_hash = try(filemd5("a.zip"), null)

  lifecycle {
    ignore_changes = [source_code_hash]
  }
}

resource "aws_lambda_function" "findings_processor" {
  function_name = "agent-platform-findings-processor"
  source_code_hash = try(filemd5("b.zip"), null)
}
"""

_BUILD_LAMBDA_CHANNELS_COMPLETE = """
deploy_channels:
  ducklake_functions:
    root: terraform/personal
    channel_class: {ducklake_channel_class}
  prod_functions:
    root: terraform/personal
    channel_class: decoupled_build_pipeline
    governed_channel: .github/workflows/deploy-prod-lambdas.yml
    break_glass_only: "bin/venv-python -m scripts.build_lambda --deploy"
"""

_BUILD_LAMBDA_DECOUPLED = _BUILD_LAMBDA_CHANNELS_COMPLETE.format(
    ducklake_channel_class="terraform_personal_ignore_changes_decoupled"
)
_BUILD_LAMBDA_COUPLED = _BUILD_LAMBDA_CHANNELS_COMPLETE.format(ducklake_channel_class="terraform_personal_filemd5_coupled")

_BUILD_LAMBDA_PROD_CHANNELS_INCOMPLETE = """
deploy_channels:
  ducklake_functions:
    root: terraform/personal
    channel_class: terraform_personal_ignore_changes_decoupled
  prod_functions:
    root: terraform/personal
    channel_class: decoupled_build_pipeline
"""

_TAXONOMY_YAML_TEMPLATE = """conformance:
  ducklake_class: {ducklake_marker}
  prod_class: {prod_marker}
"""

_TAXONOMY_DECOUPLED = _TAXONOMY_YAML_TEMPLATE.format(ducklake_marker="DECOUPLED", prod_marker="DECOUPLED")
_TAXONOMY_COUPLED = _TAXONOMY_YAML_TEMPLATE.format(ducklake_marker="COUPLED", prod_marker="DECOUPLED")
_TAXONOMY_PROD_COUPLED = _TAXONOMY_YAML_TEMPLATE.format(ducklake_marker="DECOUPLED", prod_marker="COUPLED")
_TAXONOMY_DUCKLAKE_AMBIGUOUS = _TAXONOMY_YAML_TEMPLATE.format(ducklake_marker="UNKNOWN", prod_marker="DECOUPLED")
_TAXONOMY_PROD_AMBIGUOUS = _TAXONOMY_YAML_TEMPLATE.format(ducklake_marker="DECOUPLED", prod_marker="UNKNOWN")
# conformance: dict present but missing the class-specific key entirely -- the silent-pass shape
# a typed read that defaults on a missing key must NOT introduce (CFG-11 conversion).
_TAXONOMY_DUCKLAKE_MISSING_KEY = "conformance:\n  prod_class: DECOUPLED\n"
_TAXONOMY_PROD_MISSING_KEY = "conformance:\n  ducklake_class: DECOUPLED\n"


_DISPATCHER_TF_DECOUPLED = (
    'resource "aws_lambda_function" "workflow_dispatcher" {\n  lifecycle {\n    ignore_changes = [source_code_hash]\n  }\n}\n'
)


class TestValidateDeployChannelConformance:
    def _write(
        self,
        tmp_path: Path,
        tf: str,
        build_lambda: str,
        taxonomy: str,
        prod_tf: str = _PROD_TF_DECOUPLED,
    ) -> None:
        personal_dir = tmp_path / "terraform" / "personal"
        personal_dir.mkdir(parents=True, exist_ok=True)
        (personal_dir / "ducklake_lambdas.tf").write_text(tf, encoding="utf-8")
        (personal_dir / "prod_lambdas.tf").write_text(prod_tf, encoding="utf-8")

        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text(build_lambda, encoding="utf-8")
        (contracts_dir / "environment-taxonomy.yaml").write_text(taxonomy, encoding="utf-8")

    def _run(self, tmp_path: Path) -> list[str]:
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_deploy_channel_conformance(failed)
        return failed

    def test_matching_decoupled_state_passes(self, tmp_path: Path) -> None:
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DECOUPLED)
        assert self._run(tmp_path) == []

    def test_matching_coupled_state_passes(self, tmp_path: Path) -> None:
        self._write(tmp_path, _TF_COUPLED, _BUILD_LAMBDA_COUPLED, _TAXONOMY_COUPLED)
        assert self._run(tmp_path) == []

    def test_stale_build_lambda_channel_class_fails(self, tmp_path: Path) -> None:
        """Actual state is decoupled but build-lambda.yaml still claims coupled -- the #544 drift class."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_COUPLED, _TAXONOMY_DECOUPLED)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "build-lambda.yaml" in failed[0]
        assert "decoupled" in failed[0] and "coupled" in failed[0]

    def test_stale_taxonomy_conformance_marker_fails(self, tmp_path: Path) -> None:
        """Actual state is decoupled but environment-taxonomy.yaml still claims COUPLED."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_COUPLED)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "environment-taxonomy.yaml" in failed[0]

    def test_ducklake_ambiguous_conformance_value_fails(self, tmp_path: Path) -> None:
        """conformance.ducklake_class is present but neither 'DECOUPLED' nor 'COUPLED'."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DUCKLAKE_AMBIGUOUS)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "environment-taxonomy.yaml" in failed[0]
        assert "ambiguous" in failed[0]

    def test_ducklake_missing_conformance_key_fails(self, tmp_path: Path) -> None:
        """conformance.ducklake_class is absent entirely -- a typed read that silently defaults on
        a missing key would be a vacuous pass; this must FAIL loudly instead."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DUCKLAKE_MISSING_KEY)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "environment-taxonomy.yaml" in failed[0]
        assert "conformance.ducklake_class" in failed[0]

    def test_no_ducklake_functions_found_fails(self, tmp_path: Path) -> None:
        personal_dir = tmp_path / "terraform" / "personal"
        personal_dir.mkdir(parents=True, exist_ok=True)
        (personal_dir / "prod_lambdas.tf").write_text(_PROD_TF_DECOUPLED, encoding="utf-8")
        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text(_BUILD_LAMBDA_DECOUPLED, encoding="utf-8")
        (contracts_dir / "environment-taxonomy.yaml").write_text(_TAXONOMY_DECOUPLED, encoding="utf-8")
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "no aws_lambda_function resources found" in failed[0]

    def test_partial_rollout_fails(self, tmp_path: Path) -> None:
        self._write(tmp_path, _TF_MIXED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DECOUPLED)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "partial rollout" in failed[0]

    # --- prod class (T2.43) ---

    def test_dispatcher_tf_without_ignore_changes_fails_the_prod_class(self, tmp_path: Path) -> None:
        """scheduled_workflow_dispatch.tf is scanned with prod_lambdas.tf: a function there that lacks
        ignore_changes is a partial rollout of the prod class."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DECOUPLED)
        (tmp_path / "terraform" / "personal" / "scheduled_workflow_dispatch.tf").write_text(
            'resource "aws_lambda_function" "workflow_dispatcher" {\n  function_name = "x"\n}\n',
            encoding="utf-8",
        )
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "partial rollout" in failed[0]
        assert "prod" in failed[0]

    def test_dispatcher_tf_with_ignore_changes_passes_the_prod_class(self, tmp_path: Path) -> None:
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DECOUPLED)
        (tmp_path / "terraform" / "personal" / "scheduled_workflow_dispatch.tf").write_text(
            _DISPATCHER_TF_DECOUPLED,
            encoding="utf-8",
        )
        assert self._run(tmp_path) == []

    def test_prod_matching_decoupled_state_passes(self, tmp_path: Path) -> None:
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DECOUPLED, prod_tf=_PROD_TF_DECOUPLED)
        assert self._run(tmp_path) == []

    def test_prod_no_functions_found_fails(self, tmp_path: Path) -> None:
        """prod_lambdas.tf absent entirely -- cannot determine the prod class's actual state."""
        personal_dir = tmp_path / "terraform" / "personal"
        personal_dir.mkdir(parents=True, exist_ok=True)
        (personal_dir / "ducklake_lambdas.tf").write_text(_TF_DECOUPLED, encoding="utf-8")
        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text(_BUILD_LAMBDA_DECOUPLED, encoding="utf-8")
        (contracts_dir / "environment-taxonomy.yaml").write_text(_TAXONOMY_DECOUPLED, encoding="utf-8")
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "no aws_lambda_function resources found" in failed[0]
        assert "prod" in failed[0]

    def test_prod_partial_rollout_fails(self, tmp_path: Path) -> None:
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_DECOUPLED, prod_tf=_PROD_TF_MIXED)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "partial rollout" in failed[0]
        assert "prod" in failed[0]

    def test_prod_stale_taxonomy_marker_fails(self, tmp_path: Path) -> None:
        """prod is actually decoupled but conformance.prod_class still claims COUPLED -- the check
        must catch this WITHOUT tripping the ducklake class's own comparison, since
        conformance.ducklake_class (which stays DECOUPLED) is an independent typed field."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_PROD_COUPLED, prod_tf=_PROD_TF_DECOUPLED)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "environment-taxonomy.yaml" in failed[0]
        assert "prod" in failed[0]

    def test_prod_ambiguous_conformance_value_fails(self, tmp_path: Path) -> None:
        """conformance.prod_class is present but neither 'DECOUPLED' nor 'COUPLED'."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_PROD_AMBIGUOUS, prod_tf=_PROD_TF_DECOUPLED)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "environment-taxonomy.yaml" in failed[0]
        assert "ambiguous" in failed[0]
        assert "prod" in failed[0]

    def test_prod_missing_conformance_key_fails(self, tmp_path: Path) -> None:
        """conformance.prod_class is absent entirely -- a typed read that silently defaults on a
        missing key would be a vacuous pass; this must FAIL loudly instead."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_DECOUPLED, _TAXONOMY_PROD_MISSING_KEY, prod_tf=_PROD_TF_DECOUPLED)
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "environment-taxonomy.yaml" in failed[0]
        assert "conformance.prod_class" in failed[0]

    def test_prod_missing_governed_channel_fails(self, tmp_path: Path) -> None:
        """build-lambda.yaml deploy_channels.prod_functions is missing governed_channel/
        break_glass_only -- the completeness check (distinct from the channel_class comparison,
        since decoupled_build_pipeline doesn't use the _decoupled/_coupled suffix convention)."""
        self._write(
            tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_PROD_CHANNELS_INCOMPLETE, _TAXONOMY_DECOUPLED, prod_tf=_PROD_TF_DECOUPLED
        )
        failed = self._run(tmp_path)
        assert len(failed) == 1
        assert "deploy_channels.prod_functions" in failed[0]
        assert "governed_channel" in failed[0]

    def test_prod_and_ducklake_mismatches_both_reported(self, tmp_path: Path) -> None:
        """A single run can surface BOTH a ducklake-class failure and a prod-class failure --
        the two classes' checks are independent, not short-circuiting on the first failure."""
        self._write(tmp_path, _TF_DECOUPLED, _BUILD_LAMBDA_COUPLED, _TAXONOMY_PROD_COUPLED, prod_tf=_PROD_TF_DECOUPLED)
        failed = self._run(tmp_path)
        assert len(failed) == 2
        assert any("build-lambda.yaml" in f for f in failed)
        assert any("prod" in f and "environment-taxonomy.yaml" in f for f in failed)

    # --- error/edge branches on the private read helpers (direct unit calls) ---

    def test_actual_decoupled_state_read_error_fails(self, tmp_path: Path) -> None:
        """A glob-matched .tf path that cannot be read as text (here: a directory, not a file)
        hits the OSError branch."""
        personal_dir = tmp_path / "terraform" / "personal"
        (personal_dir / "ducklake_broken.tf").mkdir(parents=True, exist_ok=True)
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            result = _actual_decoupled_state(failed)
        assert result is None
        assert any("cannot read" in f for f in failed)

    def test_doc_state_from_channel_class_read_error_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text("deploy_channels: [unterminated", encoding="utf-8")
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            result = _doc_state_from_channel_class(failed)
        assert result is None
        assert any("cannot read/parse" in f for f in failed)

    def test_doc_state_from_channel_class_missing_key_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text(
            "deploy_channels:\n  ducklake_functions:\n    root: x\n", encoding="utf-8"
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            result = _doc_state_from_channel_class(failed)
        assert result is None
        assert any("missing deploy_channels.ducklake_functions.channel_class" in f for f in failed)

    def test_doc_state_from_channel_class_ambiguous_suffix_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text(
            "deploy_channels:\n  ducklake_functions:\n    channel_class: something_else\n", encoding="utf-8"
        )
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            result = _doc_state_from_channel_class(failed)
        assert result is None
        assert any("does not end in" in f for f in failed)

    def test_prod_channels_complete_read_error_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text("deploy_channels: [unterminated", encoding="utf-8")
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            result = _prod_channels_complete(failed)
        assert result is False
        assert any("cannot read/parse" in f for f in failed)

    def test_taxonomy_state_read_error_fails(self, tmp_path: Path) -> None:
        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "environment-taxonomy.yaml").write_text("conformance: [unterminated", encoding="utf-8")
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            result = _taxonomy_state(failed)
        assert result is None
        assert any("cannot read/parse" in f for f in failed)


class TestDeployChannelBlockExtraction:
    """Brace-depth block extraction and the PASS line; own helpers (Decision 131)."""

    @staticmethod
    def _write(tmp_path: Path, tf: str, prod_tf: str = _PROD_TF_DECOUPLED) -> None:
        personal_dir = tmp_path / "terraform" / "personal"
        personal_dir.mkdir(parents=True, exist_ok=True)
        (personal_dir / "ducklake_lambdas.tf").write_text(tf, encoding="utf-8")
        (personal_dir / "prod_lambdas.tf").write_text(prod_tf, encoding="utf-8")

        contracts_dir = tmp_path / "docs" / "contracts"
        contracts_dir.mkdir(parents=True, exist_ok=True)
        (contracts_dir / "build-lambda.yaml").write_text(_BUILD_LAMBDA_DECOUPLED, encoding="utf-8")
        (contracts_dir / "environment-taxonomy.yaml").write_text(_TAXONOMY_DECOUPLED, encoding="utf-8")

    @staticmethod
    def _run(tmp_path: Path) -> list[str]:
        with patch("scripts.checks._common.ROOT", tmp_path):
            failed: list[str] = []
            validate_deploy_channel_conformance(failed)
        return failed

    def test_coupled_first_partial_rollout_is_still_reported(self, tmp_path: Path) -> None:
        """A COUPLED resource preceding a decoupled one is still a partial rollout -- a block
        running past its closing brace would swallow the next resource's ignore_changes."""
        self._write(tmp_path, _TF_MIXED_COUPLED_FIRST)
        failed = self._run(tmp_path)
        assert len(failed) == 1, f"Expected exactly one failure but got: {failed}"
        assert "1/2 ducklake" in failed[0]
        assert "partial rollout" in failed[0]

    def test_nested_block_does_not_truncate_the_resource_block(self) -> None:
        """The extracted block spans to the OUTER closing brace, so text sitting after an
        inner block that closes first is still part of it."""
        blocks = _extract_function_blocks(_TF_NESTED_BLOCK_DECOUPLED)
        assert sorted(blocks) == ["writer"]
        assert "ignore_changes = [source_code_hash]" in blocks["writer"]

    def test_nested_block_resource_reads_as_decoupled_end_to_end(self, tmp_path: Path) -> None:
        """End-to-end companion: the nested-block resource still reads as decoupled, so the
        structural claim above is tied to the conformance verdict it feeds."""
        self._write(tmp_path, _TF_NESTED_BLOCK_DECOUPLED)
        assert self._run(tmp_path) == []

    def test_agreeing_state_prints_the_pass_line(self, tmp_path: Path, capsys) -> None:
        """The PASS line is printed only when the run added no failures."""
        self._write(tmp_path, _TF_DECOUPLED)
        failed = self._run(tmp_path)
        assert failed == []
        assert "PASS: docs and terraform/personal agree" in capsys.readouterr().out
