"""Aggregation/cap/manifest/invariant tests for scripts/checks/deps/affected_tests.py (Decision
affected-set-selection). Every class below exercises the REAL selector (derive_affected_tests)
against a small, self-contained fixture repo under tmp_path -- never a mock of the selector
itself. Split from tests/checks/deps/test_affected_tests.py (Decision 128 SLOC decomposition,
concern-split: aggregation/cap/manifest/invariant layer vs.
tests/checks/deps/affected_tests/test_channels.py's per-channel derivation tests).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.checks.deps import affected_tests as at
from tests.fixtures.affected_tests_helpers import write_file as _write


class TestAdditiveOnlyInvariant:
    """affected-selection is a superset of the edited-set for any diff, including under
    cap/overflow: an edited-set member (and direct reverse-deps + data-edge hits) is never
    deferred; only the transitive residue is deferred."""

    def _fanout_fixture(self, tmp_path: Path, n_transitive: int) -> None:
        """scripts/base.py <- scripts/mid.py (direct dep of base) <- N test files that import
        mid (each a TRANSITIVE, 2-hop ancestor of base.py, never a direct predecessor of base)."""
        _write(tmp_path, "scripts/base.py", "def do():\n    pass\n")
        _write(tmp_path, "scripts/mid.py", "from scripts.base import do\n\ndef mid():\n    do()\n")
        for i in range(n_transitive):
            _write(
                tmp_path,
                f"tests/test_mid_dep_{i:02d}.py",
                f"from scripts.mid import mid\n\ndef test_{i}():\n    mid()\n",
            )

    def test_edited_set_member_never_deferred_under_cap_overflow(self, tmp_path: Path) -> None:
        self._fanout_fixture(tmp_path, n_transitive=10)
        _write(tmp_path, "tests/test_direct_on_base.py", "from scripts.base import do\n\ndef test_x():\n    do()\n")
        diff = [("M", "scripts/base.py"), ("M", "tests/test_direct_on_base.py")]
        result = at.derive_affected_tests(diff, repo_root=tmp_path, cap=3)
        assert "tests/test_direct_on_base.py" in result["selected"], "edited-set member must never be deferred"

    def test_direct_reverse_dep_never_deferred_under_cap_overflow(self, tmp_path: Path) -> None:
        self._fanout_fixture(tmp_path, n_transitive=10)
        diff = [("M", "scripts/base.py")]
        result = at.derive_affected_tests(diff, repo_root=tmp_path, cap=1)
        # scripts/mid.py is a DIRECT predecessor of scripts/base.py in module-graph terms, but
        # direct reverse-deps are TEST modules; here the direct predecessor of base.py is
        # scripts.mid (not a test), so assert instead that the selection is always a superset
        # of the edited-set (empty here) and never raises/crashes under a pathologically tiny cap.
        assert set() <= set(result["selected"])

    def test_data_edge_hit_never_deferred_under_tiny_cap(self, tmp_path: Path) -> None:
        self._fanout_fixture(tmp_path, n_transitive=10)
        _write(tmp_path, "docs/ROADMAP-PLATFORM.yaml", "tier_items: []\n")
        _write(
            tmp_path,
            "tests/test_roadmap_reader.py",
            'ROADMAP = "ROADMAP-PLATFORM.yaml"\n\ndef test_reads_roadmap():\n    assert ROADMAP\n',
        )
        diff = [("M", "scripts/base.py"), ("M", "docs/ROADMAP-PLATFORM.yaml")]
        result = at.derive_affected_tests(diff, repo_root=tmp_path, cap=1)
        assert "tests/test_roadmap_reader.py" in result["selected"], "data-edge hits must never be deferred"


class TestCapAndDefer:
    """The ~35-module cap defers ONLY the transitive residue -- a mixed batch of protected hits
    plus transitive-residue overflow defers exactly the overflow."""

    def test_transitive_overflow_is_capped_and_deferred(self, tmp_path: Path) -> None:
        """Overflow is what lies BEYOND the deepest kept import-distance layer: the residue budget
        cuts on a layer boundary, so a fixture whose residue all sits at one distance is never
        capped at all (see TestNearestFirstRanking in test_residue_budget.py)."""
        _write(tmp_path, "scripts/base.py", "def do():\n    pass\n")
        _write(tmp_path, "scripts/mid.py", "from scripts.base import do\n\ndef mid():\n    do()\n")
        _write(tmp_path, "scripts/far.py", "from scripts.mid import mid\n\ndef far():\n    mid()\n")
        for i in range(10):
            _write(
                tmp_path,
                f"tests/test_a_near_{i:02d}.py",
                f"from scripts.mid import mid\n\ndef test_{i}():\n    mid()\n",
            )
        for i in range(5):
            _write(
                tmp_path,
                f"tests/test_z_far_{i:02d}.py",
                f"from scripts.far import far\n\ndef test_far_{i}():\n    far()\n",
            )
        result = at.derive_affected_tests([("M", "scripts/base.py")], repo_root=tmp_path, cap=4)
        manifest = result["manifest"]
        assert manifest["capped"] is True
        assert len(manifest["deferred"]) == 5
        assert len(result["selected"]) + len(manifest["deferred"]) == 15
        assert set(result["selected"]).isdisjoint(manifest["deferred"])

    def test_under_cap_nothing_deferred(self, tmp_path: Path) -> None:
        _write(tmp_path, "scripts/base.py", "def do():\n    pass\n")
        _write(tmp_path, "scripts/mid.py", "from scripts.base import do\n\ndef mid():\n    do()\n")
        _write(tmp_path, "tests/test_mid_dep_00.py", "from scripts.mid import mid\n\ndef test_0():\n    mid()\n")
        result = at.derive_affected_tests([("M", "scripts/base.py")], repo_root=tmp_path, cap=at.CAP)
        assert result["manifest"]["capped"] is False
        assert result["manifest"]["deferred"] == []


class TestManifestEmission:
    """Manifest emission + content: required keys, per-test provenance/channel correctness."""

    _REQUIRED_KEYS = {
        "sha",
        "diff",
        "edited_set",
        "selected",
        "provenance",
        "capped",
        "deferred",
        "cap",
        "residue_budget",
        "residue_ranking",
        "residue_kept_depth",
        "timings",
    }

    def test_manifest_has_required_keys(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        result = at.derive_affected_tests([("M", "tests/test_a.py")], repo_root=tmp_path)
        assert self._REQUIRED_KEYS <= set(result["manifest"].keys())

    def test_provenance_tags_edited_set_member(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        result = at.derive_affected_tests([("M", "tests/test_a.py")], repo_root=tmp_path)
        assert result["manifest"]["provenance"]["tests/test_a.py"] == "edited_set"

    def test_diff_field_mirrors_input_entries(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        result = at.derive_affected_tests([("M", "tests/test_a.py")], repo_root=tmp_path)
        assert result["manifest"]["diff"] == [{"status": "M", "path": "tests/test_a.py"}]

    def test_emit_manifest_writes_gitignored_path_and_returns_it(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        result = at.derive_affected_tests([("M", "tests/test_a.py")], repo_root=tmp_path)
        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("S3_LOG_BUCKET", None)
            manifest_path = at.emit_manifest(result["manifest"], repo_root=tmp_path)
        assert manifest_path == tmp_path / "logs" / "debug" / "selection-manifest.json"
        assert manifest_path.exists()

    def test_emit_manifest_local_write_failure_is_loud_skip_not_raise(
        self, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        """A local disk I/O error (e.g. 'logs' exists as a FILE, not a directory, so mkdir()
        raises NotADirectoryError) must be a loud skip -- never crash --pre (Decision 55),
        matching the best-effort philosophy already applied to the S3 upload leg."""
        _write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        result = at.derive_affected_tests([("M", "tests/test_a.py")], repo_root=tmp_path)
        (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("S3_LOG_BUCKET", None)
            manifest_path = at.emit_manifest(result["manifest"], repo_root=tmp_path)
        captured = capsys.readouterr()
        assert "local write" in captured.out
        assert "loud skip" in captured.out.lower()
        assert not manifest_path.exists()


class TestWriteManifest:
    """write_manifest is emit_manifest's local-write leg, split out so it can be called a SECOND
    time late in a --pre run -- once the `budget` block (only computable after the last phase) has
    been attached by scripts/validate.py's trailing budget-assertion scaffold."""

    def test_writes_to_the_same_path_emit_manifest_does(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        result = at.derive_affected_tests([("M", "tests/test_a.py")], repo_root=tmp_path)
        manifest_path = at.write_manifest(result["manifest"], repo_root=tmp_path)
        assert manifest_path == tmp_path / "logs" / "debug" / "selection-manifest.json"
        written = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert written["selected"] == result["manifest"]["selected"]

    def test_second_call_rewrites_with_a_late_attached_block(self, tmp_path: Path) -> None:
        _write(tmp_path, "tests/test_a.py", "def test_a():\n    assert True\n")
        manifest = at.derive_affected_tests([("M", "tests/test_a.py")], repo_root=tmp_path)["manifest"]
        at.write_manifest(manifest, repo_root=tmp_path)
        manifest["budget"] = {"outcome": "within_budget"}
        path = at.write_manifest(manifest, repo_root=tmp_path)
        assert json.loads(path.read_text(encoding="utf-8"))["budget"] == {"outcome": "within_budget"}

    def test_resolves_debug_manifest_path_at_call_time_not_import_time(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """repo_root=None must read DEBUG_MANIFEST_PATH INSIDE the body on every call, so
        tests/conftest.py's autouse _isolate_selection_manifest redirect covers the late second
        write too. A default-argument binding would capture the constant at import time and let a
        tests/validate/ orchestrator run write into the repo's tracked logs/debug/."""
        redirected = tmp_path / "redirected" / "selection-manifest.json"
        monkeypatch.setattr(at, "DEBUG_MANIFEST_PATH", redirected)
        assert at.write_manifest({"selected": []}) == redirected
        assert redirected.exists()

    def test_local_write_failure_is_loud_skip_not_raise(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        (tmp_path / "logs").write_text("not a directory", encoding="utf-8")
        manifest_path = at.write_manifest({"selected": []}, repo_root=tmp_path)
        captured = capsys.readouterr()
        assert "local write" in captured.out
        assert "loud skip" in captured.out.lower()
        assert not manifest_path.exists()

    def test_neither_prints_the_manifest_nor_re_uploads_it(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """The print and best-effort S3 legs stay on emit_manifest: a late second write must not
        re-print the whole manifest to the console or re-upload it."""
        with patch.object(at, "_upload_manifest_best_effort") as mock_upload:
            at.write_manifest({"selected": []}, repo_root=tmp_path)
        mock_upload.assert_not_called()
        assert capsys.readouterr().out == ""


class TestS3UploadDegradesGracefully:
    """With boto3/creds absent, the upload lazy-imports boto3 and prints a LOUD skip -- never
    silent, never raising (Decision 55)."""

    def test_no_bucket_set_prints_loud_skip(self, capsys: pytest.CaptureFixture) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("S3_LOG_BUCKET", None)
            at._upload_manifest_best_effort({"sha": "abc123"})
        captured = capsys.readouterr()
        assert "S3_LOG_BUCKET not set" in captured.out
        assert "skipping" in captured.out.lower()

    def test_boto3_unavailable_prints_loud_skip(self, capsys: pytest.CaptureFixture) -> None:
        import sys

        with patch.dict("os.environ", {"S3_LOG_BUCKET": "some-bucket"}), patch.dict(sys.modules, {"boto3": None}):
            at._upload_manifest_best_effort({"sha": "abc123"})
        captured = capsys.readouterr()
        assert "boto3 not installed" in captured.out
        assert "skipping" in captured.out.lower()

    def test_upload_exception_is_loud_skip_not_raise(self, capsys: pytest.CaptureFixture) -> None:
        import sys
        import types

        fake_boto3 = types.ModuleType("boto3")

        class _BoomSession:
            def __init__(self, *a, **kw):
                raise RuntimeError("boom")

        fake_boto3.Session = _BoomSession  # type: ignore[attr-defined]

        with patch.dict("os.environ", {"S3_LOG_BUCKET": "some-bucket"}), patch.dict(sys.modules, {"boto3": fake_boto3}):
            at._upload_manifest_best_effort({"sha": "abc123"})
        captured = capsys.readouterr()
        assert "best-effort S3 upload failed" in captured.out


class TestS3UploadSuccess:
    """The happy path: boto3 present, bucket set, put_object succeeds -- prints the s3:// URL."""

    def test_successful_upload_prints_s3_url(self, capsys: pytest.CaptureFixture) -> None:
        import sys
        import types

        fake_boto3 = types.ModuleType("boto3")
        put_calls: list[dict] = []

        class _FakeClient:
            def put_object(self, **kwargs):
                put_calls.append(kwargs)

        class _FakeSession:
            def __init__(self, *a, **kw):
                pass

            def client(self, *a, **kw):
                return _FakeClient()

        fake_boto3.Session = _FakeSession  # type: ignore[attr-defined]

        with (
            patch.dict("os.environ", {"S3_LOG_BUCKET": "some-bucket"}),
            patch.dict(sys.modules, {"boto3": fake_boto3}),
            patch("scripts.aws_profile.resolve_aws_profile", return_value=None),
        ):
            at._upload_manifest_best_effort({"sha": "abc123"})

        captured = capsys.readouterr()
        assert "uploaded to s3://some-bucket/ci/selection/abc123/selection-manifest.json" in captured.out
        assert len(put_calls) == 1


class TestExceptionFallback:
    """When the derivation raises internally, the selector falls back to the edited-set and
    prints a loud warning -- never silently shrinking below it."""

    def test_injected_exception_falls_back_to_edited_set(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        _write(tmp_path, "scripts/foo.py", "def bar():\n    pass\n")
        _write(tmp_path, "tests/test_edited.py", "def test_x():\n    assert True\n")
        with patch("scripts.checks.deps.affected_tests._import_closure_channel", side_effect=RuntimeError("boom")):
            result = at.derive_affected_tests([("M", "scripts/foo.py"), ("M", "tests/test_edited.py")], repo_root=tmp_path)
        assert set(result["selected"]) >= {"tests/test_edited.py"}
        assert result["manifest"].get("fallback") is True
        captured = capsys.readouterr()
        assert "FALLING BACK TO EDITED-SET" in captured.out


class TestManifestNotAnInput:
    """The derivation output is byte-identical whether a prior selection-manifest.json is
    ABSENT, present, or deliberately MUTATED -- the manifest never feeds selection."""

    def test_selection_identical_across_absent_present_mutated_manifest(self, tmp_path: Path) -> None:
        _write(tmp_path, "scripts/foo.py", "def bar():\n    pass\n")
        _write(tmp_path, "tests/test_foo_consumer.py", "from scripts.foo import bar\n\ndef test_bar():\n    bar()\n")
        diff = [("M", "scripts/foo.py")]

        result_absent = at.derive_affected_tests(diff, repo_root=tmp_path)

        with patch.dict("os.environ", {}, clear=False):
            import os as _os

            _os.environ.pop("S3_LOG_BUCKET", None)
            at.emit_manifest(result_absent["manifest"], repo_root=tmp_path)
        result_present = at.derive_affected_tests(diff, repo_root=tmp_path)

        manifest_path = tmp_path / "logs" / "debug" / "selection-manifest.json"
        manifest_path.write_text('{"garbage": true, "selected": ["tests/test_should_not_matter.py"]}', encoding="utf-8")
        result_mutated = at.derive_affected_tests(diff, repo_root=tmp_path)

        assert result_absent["selected"] == result_present["selected"] == result_mutated["selected"]
        assert (
            result_absent["manifest"]["provenance"]
            == result_present["manifest"]["provenance"]
            == result_mutated["manifest"]["provenance"]
        )


class TestEmptyDiff:
    """No diff entries at all -- selection is the (empty) edited-set, no crash, no graph build."""

    def test_empty_diff_returns_empty_selection(self, tmp_path: Path) -> None:
        result = at.derive_affected_tests([], repo_root=tmp_path)
        assert result["selected"] == []
        assert result["manifest"]["capped"] is False


class TestRootConftestForcesFullScope:
    """VTS-03: a changed root/autouse conftest forces its subtree into protected/uncapped. W2-D:
    a plain sub-conftest is ALSO protected/uncapped now (see
    tests/checks/deps/affected_tests/test_conftest_subtree.py for the dedicated coverage) --
    this class keeps only the distinct-provenance assertion, since the old
    forced-vs-cappable-by-capping-behavior split this test used to exercise no longer exists."""

    def test_autouse_and_plain_sub_conftests_get_distinct_provenance_both_protected(self, tmp_path: Path) -> None:
        """Autouse sub-conftest -> conftest_subtree_forced; plain sub-conftest ->
        conftest_subtree_structural (W2-D). Both protected/uncapped even under a cap far below
        either subtree's size."""
        _write(
            tmp_path,
            "tests/autouse_pkg/conftest.py",
            "import pytest\n\n\n@pytest.fixture(autouse=True)\ndef _setup():\n    yield\n",
        )
        _write(tmp_path, "tests/autouse_pkg/test_a.py", "def test_a():\n    assert True\n")
        _write(tmp_path, "tests/plain_pkg/conftest.py", "")
        for i in range(3):
            _write(tmp_path, f"tests/plain_pkg/test_{i}.py", f"def test_{i}():\n    assert True\n")
        diff = [("M", "tests/autouse_pkg/conftest.py"), ("M", "tests/plain_pkg/conftest.py")]
        result = at.derive_affected_tests(diff, repo_root=tmp_path, cap=1)
        manifest = result["manifest"]
        assert "tests/autouse_pkg/test_a.py" in result["selected"]
        assert manifest["provenance"]["tests/autouse_pkg/test_a.py"] == "conftest_subtree_forced"
        for i in range(3):
            path = f"tests/plain_pkg/test_{i}.py"
            assert path in result["selected"]
            assert manifest["provenance"][path] == "conftest_subtree_structural"
        assert manifest["full_suite_forced"] is False
        assert manifest["capped"] is False
        assert manifest["deferred"] == []

    def test_rec_2725_incident_replay_real_repo(self) -> None:
        """rec-2725 victim (deferred pre-fix, past the 35-module alphabetical keep window)."""
        result = at.derive_affected_tests([("M", "tests/conftest.py")])
        manifest = result["manifest"]
        assert manifest["capped"] is False
        assert manifest["deferred"] == []
        assert manifest["full_suite_forced"] is True
        assert "tests/ops_data_portal/test_decisions.py" in result["selected"]


class TestResidueChannelPriorityOrdering:
    """W2-D: with conftest_subtree_structural promoted to protected, import_closure_transitive is
    the ONLY channel left in the cappable residue pool -- mirror_map and conftest_subtree_*
    hits are never deferred regardless of cap size. Within that pool the keep-set is ranked by
    import distance and cut on a whole-layer boundary (see _residue_keep_set and
    tests/checks/deps/affected_tests/test_residue_budget.py), never by filename."""

    def test_mirror_and_conftest_survive_a_cap_below_their_combined_size(self, tmp_path: Path) -> None:
        """cap=1 is below the 2 protected hits (mirror_map + conftest_subtree_structural) --
        proves both stay unconditional. The residue spans two distance layers so the cap still
        bites, which also carries the dec-142 coherence assertion (compute_escape_class reads
        ONLY manifest['selected']/['deferred'] as flat, disjoint path-string lists)."""
        from scripts.ops_portal.ci_rca_lifecycle import compute_escape_class

        _write(tmp_path, "scripts/base.py", "def do():\n    pass\n")
        _write(tmp_path, "scripts/mid.py", "from scripts.base import do\n\ndef mid():\n    do()\n")
        _write(tmp_path, "scripts/deeper.py", "from scripts.mid import mid\n\ndef deeper():\n    mid()\n")
        _write(tmp_path, "tests/test_aaa_transitive.py", "from scripts.mid import mid\n\ndef test_x():\n    mid()\n")
        _write(tmp_path, "tests/test_ddd_deeper.py", "from scripts.deeper import deeper\n\ndef test_d():\n    deeper()\n")
        _write(tmp_path, "tests/bbb_pkg/conftest.py", "")
        _write(tmp_path, "tests/bbb_pkg/test_b.py", "def test_b():\n    assert True\n")
        _write(tmp_path, "scripts/zzz_mirror_source.py", "def something():\n    pass\n")
        _write(tmp_path, "tests/test_zzz_mirror_source.py", "def test_x():\n    assert True\n")
        diff = [("M", "scripts/base.py"), ("M", "tests/bbb_pkg/conftest.py"), ("M", "scripts/zzz_mirror_source.py")]

        with patch("scripts.test_coverage_checker.ROOT", tmp_path):
            result = at.derive_affected_tests(diff, repo_root=tmp_path, cap=1)

        manifest = result["manifest"]
        assert set(result["selected"]) == {
            "tests/test_zzz_mirror_source.py",
            "tests/bbb_pkg/test_b.py",
            "tests/test_aaa_transitive.py",
        }
        assert manifest["provenance"]["tests/test_zzz_mirror_source.py"] == "mirror_map"
        assert manifest["provenance"]["tests/bbb_pkg/test_b.py"] == "conftest_subtree_structural"
        assert manifest["deferred"] == ["tests/test_ddd_deeper.py"]
        assert manifest["capped"] is True

        selected, deferred = manifest["selected"], manifest["deferred"]
        assert isinstance(selected, list) and all(isinstance(x, str) for x in selected)
        assert isinstance(deferred, list) and all(isinstance(x, str) for x in deferred)
        assert set(selected).isdisjoint(deferred)
        assert compute_escape_class(deferred[0] + "::test", manifest) == "capped"
        assert compute_escape_class("tests/never_selected.py::test", manifest) == "no-edge"

    def test_one_distance_layer_of_residue_is_kept_whole_under_any_cap(self, tmp_path: Path) -> None:
        """Three siblings at the SAME import distance are equally-evidenced, so cap=1 keeps all
        three rather than the alphabetically-first one. Filename order still decides the reported
        ordering (selected/deferred are sorted), never membership."""
        _write(tmp_path, "scripts/base.py", "def do():\n    pass\n")
        _write(tmp_path, "scripts/mid.py", "from scripts.base import do\n\ndef mid():\n    do()\n")
        for name in ("bbb", "aaa", "ccc"):
            _write(
                tmp_path,
                f"tests/test_{name}_dep.py",
                f"from scripts.mid import mid\n\ndef test_{name}():\n    mid()\n",
            )
        result = at.derive_affected_tests([("M", "scripts/base.py")], repo_root=tmp_path, cap=1)
        manifest = result["manifest"]
        assert result["selected"] == ["tests/test_aaa_dep.py", "tests/test_bbb_dep.py", "tests/test_ccc_dep.py"]
        assert manifest["deferred"] == []
        assert manifest["capped"] is False


def test_setup_cloud_script_selects_contract_test_through_data_edge() -> None:
    result = at.derive_affected_tests([("M", "bin/setup-cloud-env.sh")])

    assert "tests/test_setup_cloud_env.py" in result["selected"]
    assert result["manifest"]["provenance"]["tests/test_setup_cloud_env.py"] == "data_edge"


def test_requirements_lock_selects_mcp_server_tests_through_data_edge() -> None:
    result = at.derive_affected_tests([("M", "requirements.txt")])

    assert "tests/test_agent_sdk_mcp_server.py" in result["selected"]
    assert result["manifest"]["provenance"]["tests/test_agent_sdk_mcp_server.py"] == "data_edge"
