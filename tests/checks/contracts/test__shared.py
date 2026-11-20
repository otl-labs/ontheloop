"""Direct coverage for scripts/checks/contracts/_shared.py: the merge-base baseline readers
(path-set, rename map, id-keyed envelope reader) and the pre-existing prompt-compliance loader.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

from scripts.checks.contracts import _shared


def _fake_run(returncode: int, stdout: str = "") -> callable:
    def _run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return _run


class TestMergeBasePathSet:
    def test_success(self, tmp_path: Path) -> None:
        with patch.object(_shared._common, "run", _fake_run(0, "docs/contracts/a.yaml\ndocs/contracts/b.yaml\n")):
            result = _shared.merge_base_path_set(tmp_path, "BASE0000")
        assert result == {"docs/contracts/a.yaml", "docs/contracts/b.yaml"}

    def test_git_failure_returns_none(self, tmp_path: Path) -> None:
        with patch.object(_shared._common, "run", _fake_run(1)):
            assert _shared.merge_base_path_set(tmp_path, "BASE0000") is None


class TestRenameMap:
    def test_success(self, tmp_path: Path) -> None:
        with patch.object(_shared._common, "run", _fake_run(0, "R100\tdocs/contracts/old.yaml\tdocs/contracts/new.yaml\n")):
            result = _shared.rename_map(tmp_path, "BASE0000")
        assert result == {"docs/contracts/new.yaml": "docs/contracts/old.yaml"}

    def test_git_failure_returns_none(self, tmp_path: Path) -> None:
        with patch.object(_shared._common, "run", _fake_run(1)):
            assert _shared.rename_map(tmp_path, "BASE0000") is None

    def test_non_rename_lines_ignored(self, tmp_path: Path) -> None:
        with patch.object(_shared._common, "run", _fake_run(0, "M\tdocs/contracts/unchanged.yaml\n")):
            result = _shared.rename_map(tmp_path, "BASE0000")
        assert result == {}


class TestParseBatchOutput:
    def test_missing_ref(self) -> None:
        text = "BASE0000:docs/contracts/nope.yaml missing\n"
        result = _shared._parse_batch_output(text, ["BASE0000:docs/contracts/nope.yaml"])
        assert result == {"BASE0000:docs/contracts/nope.yaml": None}

    def test_present_ref(self) -> None:
        content = "contract:\n  id: x\n"
        header = f"{'0' * 40} blob {len(content)}\n"
        text = header + content + "\n"
        result = _shared._parse_batch_output(text, ["BASE0000:docs/contracts/x.yaml"])
        assert result == {"BASE0000:docs/contracts/x.yaml": content}


class TestBaselineClassDEnvelopes:
    def test_empty_candidates_short_circuits(self, tmp_path: Path) -> None:
        assert _shared.baseline_class_d_envelopes(tmp_path, "BASE0000", set()) == {}

    def test_git_failure_returns_none(self, tmp_path: Path) -> None:
        with patch.object(_shared._common, "run", _fake_run(1)):
            result = _shared.baseline_class_d_envelopes(tmp_path, "BASE0000", {"docs/contracts/x.yaml"})
        assert result is None

    def test_missing_ref_is_skipped(self, tmp_path: Path) -> None:
        stdout = "BASE0000:docs/contracts/x.yaml missing\n"
        with patch.object(_shared._common, "run", _fake_run(0, stdout)):
            result = _shared.baseline_class_d_envelopes(tmp_path, "BASE0000", {"docs/contracts/x.yaml"})
        assert result == {}

    def test_non_mapping_blob_is_skipped(self, tmp_path: Path) -> None:
        content = "- just\n- a\n- list\n"
        header = f"{'0' * 40} blob {len(content)}\n"
        with patch.object(_shared._common, "run", _fake_run(0, header + content + "\n")):
            result = _shared.baseline_class_d_envelopes(tmp_path, "BASE0000", {"docs/contracts/x.yaml"})
        assert result == {}

    def test_class_d_envelope_extracted_by_id(self, tmp_path: Path) -> None:
        content = "contract:\n  id: foo\n  class: D\n"
        header = f"{'0' * 40} blob {len(content)}\n"
        with patch.object(_shared._common, "run", _fake_run(0, header + content + "\n")):
            result = _shared.baseline_class_d_envelopes(tmp_path, "BASE0000", {"docs/contracts/x.yaml"})
        assert result == {"foo": content}

    def test_envelope_parse_goes_through_parse_yaml(self, tmp_path: Path) -> None:
        content = "contract:\n  id: foo\n  class: D\n"
        header = f"{'0' * 40} blob {len(content)}\n"
        with (
            patch.object(_shared._common, "run", _fake_run(0, header + content + "\n")),
            patch.object(_shared, "parse_yaml", wraps=_shared.parse_yaml) as spy,
        ):
            _shared.baseline_class_d_envelopes(tmp_path, "BASE0000", {"docs/contracts/x.yaml"})
        assert spy.call_count == 1

    def test_non_class_d_and_unparseable_blobs_are_skipped(self, tmp_path: Path) -> None:
        a = "contract:\n  id: a\n  class: A\n"
        b = "{bad: [unclosed"
        header_a = f"{'0' * 40} blob {len(a)}\n"
        header_b = f"{'0' * 40} blob {len(b)}\n"
        combined = header_a + a + "\n" + header_b + b + "\n"
        with patch.object(_shared._common, "run", _fake_run(0, combined)):
            result = _shared.baseline_class_d_envelopes(
                tmp_path, "BASE0000", {"docs/contracts/a.yaml", "docs/contracts/b.yaml"}
            )
        assert result == {}


class TestLoadPromptCompliance:
    def test_missing_module_returns_none(self, monkeypatch) -> None:
        monkeypatch.setattr(_shared._common, "ROOT", Path("/nonexistent-root-for-this-test"))
        assert _shared._load_prompt_compliance() is None

    def test_loads_real_module(self) -> None:
        mod = _shared._load_prompt_compliance()
        assert mod is not None
        assert hasattr(mod, "__name__")

    def test_injects_and_restores_sys_path_when_root_absent(self, monkeypatch) -> None:
        import sys

        root_str = str(_shared._common.ROOT)
        # Strip ALL occurrences in one step (sys.path can carry duplicates -- e.g. under
        # pytest-xdist workers) so the injected/removed branch is exercised.
        monkeypatch.setattr(sys, "path", [p for p in sys.path if p != root_str])
        # Snapshot immediately before the call and diff after, rather than asserting
        # absolute absence of root_str: under a full-suite pytest-xdist run, unrelated
        # test-package collection can leave AMBIENT duplicate root entries in this
        # worker's sys.path over its lifetime, which an absolute "not in" assertion
        # would misreport as a leak from THIS function. A before/after snapshot proves
        # the function's own contract (transient inject-then-restore, net zero change)
        # without depending on what else shares the worker process's sys.path.
        before = list(sys.path)
        mod = _shared._load_prompt_compliance()
        assert mod is not None
        assert list(sys.path) == before
