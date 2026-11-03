"""Tests for validate_terraform_no_build_artifact_reads() (rec-4243)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks import _common, registry
from scripts.checks.iam_tf import _manifest
from scripts.checks.iam_tf import validate_terraform_no_build_artifact_reads as mod
from scripts.checks.iam_tf.validate_terraform_no_build_artifact_reads import (
    find_build_artifact_reads,
    validate_terraform_no_build_artifact_reads,
)

_NAME = "validate_terraform_no_build_artifact_reads"


def _scan(tmp_path: Path, tf: str, ignored: tuple[str, ...] = ()) -> list[tuple[str, int, list[str]]]:
    root = tmp_path / "terraform" / "personal"
    root.mkdir(parents=True, exist_ok=True)
    (root / "main.tf").write_text(tf, encoding="utf-8")
    return find_build_artifact_reads(root, lambda p: any(p.rstrip("/").endswith(i) for i in ignored))


def test_real_tree_has_no_build_artifact_reads() -> None:
    findings = find_build_artifact_reads(_common.ROOT / "terraform", mod._git_check_ignore)
    assert findings == []


class TestRedCases:
    def test_direct_try_filemd5_flagged_once(self, tmp_path: Path) -> None:
        tf = 'x = try(filemd5("${path.module}/../../lambda-packages/x.zip"), null)\n'
        f = _scan(tmp_path, tf)
        assert [(r, ln) for r, ln, _ in f] == [("main.tf", 1)]

    def test_local_indirection_flagged_on_the_local(self, tmp_path: Path) -> None:
        tf = 'locals {\n  p = "${path.module}/../../lambda-packages/x.zip"\n}\nh = filemd5(local.p)\n'
        assert [ln for _, ln, _ in _scan(tmp_path, tf)] == [2]

    def test_directory_local_flagged(self, tmp_path: Path) -> None:
        tf = 'locals {\n  d = "${path.module}/../../lambda-packages"\n}\n'
        assert [ln for _, ln, _ in _scan(tmp_path, tf)] == [2]

    def test_source_code_hash_argument_flagged(self, tmp_path: Path) -> None:
        f = _scan(tmp_path, 'resource "a" "b" {\n  source_code_hash = "abc"\n}\n')
        assert f[0][1] == 2 and "(c)" in f[0][2][0]

    def test_ignore_changes_marker_is_not_an_argument(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, "lifecycle {\n  ignore_changes = [source_code_hash]\n}\n") == []

    def test_bare_relative_literal_flagged(self, tmp_path: Path) -> None:
        assert len(_scan(tmp_path, 'x = "../../lambda-packages/x.zip"\n')) == 1

    def test_ignored_path_flagged(self, tmp_path: Path) -> None:
        f = _scan(tmp_path, 'x = "${path.root}/build/out.json"\n', ignored=("build/out.json",))
        assert f and "(b)" in f[0][2][0]

    def test_templatefile_of_ignored_path_flagged(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, 'x = templatefile("build/t.tpl", {})\n', ignored=("build/t.tpl",))

    def test_bare_file_function_arguments_flagged(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, 'x = try(filemd5("lambda-packages/x.zip"), null)\n')
        assert _scan(tmp_path, 'x = file("secret/out.txt")\n', ignored=("secret/out.txt",))

    def test_directory_pattern_probed_with_trailing_slash(self, tmp_path: Path) -> None:
        root = tmp_path / "terraform" / "personal"
        root.mkdir(parents=True)
        (root / "main.tf").write_text('x = file("outdir")\n', encoding="utf-8")
        assert find_build_artifact_reads(root, lambda p: p.endswith("/")) != []

    def test_probe_error_fails_closed(self, tmp_path: Path) -> None:
        root = tmp_path / "terraform" / "personal"
        root.mkdir(parents=True)
        (root / "main.tf").write_text('x = file("a.txt")\n', encoding="utf-8")

        def boom(_: str) -> bool:
            raise RuntimeError("no git")

        f = find_build_artifact_reads(root, boom)
        assert f and "failed closed" in f[0][2][0]


class TestLegitimate:
    def test_s3_key_literal_not_flagged(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, 's3_key = "lambda-packages/x.zip"\n') == []

    def test_commented_read_not_flagged(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, '# x = filemd5("${path.module}/../../lambda-packages/x.zip")\n') == []

    def test_tracked_file_read_not_flagged(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, 'x = file("${path.module}/../../config/a.yaml")\n') == []

    def test_unresolvable_interpolation_skips_probe(self, tmp_path: Path) -> None:
        assert _scan(tmp_path, 'x = file("${path.module}/${var.n}")\n', ignored=("n",)) == []

    def test_dot_terraform_dir_skipped(self, tmp_path: Path) -> None:
        root = tmp_path / "terraform" / "personal" / ".terraform" / "m"
        root.mkdir(parents=True)
        (root / "main.tf").write_text('x = "../lambda-packages/x.zip"\n', encoding="utf-8")
        assert find_build_artifact_reads(tmp_path / "terraform", lambda p: False) == []


class TestRegisteredCheck:
    def _run(self, tmp_path: Path, tf: str) -> list[str]:
        root = tmp_path / "terraform" / "personal"
        root.mkdir(parents=True, exist_ok=True)
        (root / "main.tf").write_text(tf, encoding="utf-8")
        failed: list[str] = []
        with patch("scripts.checks._common.ROOT", tmp_path), patch.object(mod, "_git_check_ignore", lambda p: False):
            validate_terraform_no_build_artifact_reads(failed)
        return failed

    def test_failure_appends_and_names_the_fix(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        failed = self._run(tmp_path, 'source_code_hash = "x"\n')
        assert failed == ["Terraform build-artifact reads"]
        assert "s3_bucket/s3_key" in capsys.readouterr().out

    def test_pass_path(self, tmp_path: Path) -> None:
        assert self._run(tmp_path, 's3_key = "lambda-packages/x.zip"\n') == []

    def test_declares_examined(self, tmp_path: Path) -> None:
        with patch.object(registry, "examined") as ex:
            self._run(tmp_path, "x = 1\n")
        ex.assert_called_once_with(1, unit="tf_files")


class TestGitProbe:
    @pytest.mark.parametrize(("code", "expected"), [(0, True), (1, False)])
    def test_exit_codes(self, code: int, expected: bool) -> None:
        proc = type("P", (), {"returncode": code})()
        with patch.object(mod.subprocess, "run", return_value=proc):
            assert mod._git_check_ignore("x") is expected

    def test_other_exit_raises(self) -> None:
        proc = type("P", (), {"returncode": 128})()
        with patch.object(mod.subprocess, "run", return_value=proc), pytest.raises(RuntimeError):
            mod._git_check_ignore("x")


def test_check_is_registered_in_pre() -> None:
    entry = next(e for e in _manifest.ENTRIES if e.name == _NAME)
    assert entry.pre is True
    assert "terraform/**/*.tf" in entry.pre_globs and ".gitignore" in entry.pre_globs
    assert any(e.name == _NAME for e in registry._ALL_ENTRIES.values())
