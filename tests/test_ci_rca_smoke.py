"""scripts/ci_rca/smoke.py verbs (rec-5493): select, stage, outputs, verify."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from scripts.ci_rca import coverage, smoke

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ci_rca" / "smoke"
SMOKE_ID = "smoke-123"


def _line(ctx: object, source: str = "ci_rca", action: str = "file_ops", table: str = "ops_recommendations") -> str:
    record = {"source": source, "context_v2_json": json.dumps(ctx) if not isinstance(ctx, str) else ctx}
    return json.dumps({"action": action, "table": table, "record": record})


def _env(tmp_path: Path, agent_ctx: object, fallback_ctx: object, marker: str = f"FILED: {SMOKE_ID} code_regression") -> dict:
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    (bundles / "abc.json").write_text(json.dumps({"sha256": "sha-1"}), encoding="utf-8")
    sink = tmp_path / "sink.jsonl"
    sink.write_text(_line(agent_ctx) + "\n", encoding="utf-8")
    fb = tmp_path / "fb.jsonl"
    fb.write_text(_line(fallback_ctx) + "\n", encoding="utf-8")
    out = tmp_path / "out.json"
    out.write_text(json.dumps({"result": marker}), encoding="utf-8")
    return {"bundles_dir": bundles, "sink": sink, "fallback_sink": fb, "agent_output": out}


GOOD_AGENT = {"evidence_bundle_ref": {"sha256": "sha-1"}}
GOOD_FALLBACK = {"rca_confidence": "undetermined"}


@pytest.fixture(autouse=True)
def _armed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI_RCA_SMOKE_SINK", "/tmp/unused-sink.jsonl")


class TestSelect:
    def test_changed_input_runs_and_unrelated_does_not(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(coverage, "load", lambda: SimpleNamespace(workflow_inputs=lambda wf: {"a/b.py", "dir/"}))
        assert smoke.select("b", "a", ["a/b.py"]) == "run=true"
        assert smoke.select("b", "a", ["dir/sub/x.txt"]) == "run=true"
        assert smoke.select("b", "a", ["docs/x.md"]) == "run=false"

    @pytest.mark.parametrize("before", ["", "0" * 40])
    def test_unusable_before_runs_with_warning(self, before: str, capsys: pytest.CaptureFixture[str]) -> None:
        assert smoke.select(before, "a", None) == "run=true"
        assert "::warning::" in capsys.readouterr().err

    def test_git_diff_lists_changed_files(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(coverage, "load", lambda: SimpleNamespace(workflow_inputs=lambda wf: {"x.py"}))
        done = subprocess.CompletedProcess([], 0, stdout="x.py\n\n", stderr="")
        with patch("scripts.ci_rca.smoke.subprocess.run", return_value=done) as run:
            assert smoke.select("deadbeef", "cafe", None) == "run=true"
        assert run.call_args.args[0] == ["git", "diff", "--name-only", "deadbeef", "cafe"]

    def test_git_failure_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        bad = subprocess.CompletedProcess([], 128, stdout="", stderr="bad object")
        with patch("scripts.ci_rca.smoke.subprocess.run", return_value=bad):
            assert smoke.select("deadbeef", "cafe", None) == "run=true"
        assert "failed; running the smoke" in capsys.readouterr().err

    def test_git_missing_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch("scripts.ci_rca.smoke.subprocess.run", side_effect=OSError("no git")):
            assert smoke.select("deadbeef", "cafe", None) == "run=true"
        assert "git failed" in capsys.readouterr().err

    def test_derivation_failure_runs(self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
        def boom() -> None:
            raise RuntimeError("graph")

        monkeypatch.setattr(coverage, "load", boom)
        assert smoke.select("b", "a", ["docs/x.md"]) == "run=true"
        assert "input derivation failed" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("config/claude-cli-version", "run=true"),
            (".github/workflows/ci-rca-smoke.yml", "run=true"),
            ("config/ci_rca_taxonomy.yaml", "run=true"),
            ("docs/PROJECT_CONTEXT.md", "run=false"),
        ],
    )
    def test_real_tree(self, path: str, expected: str) -> None:
        assert smoke.select("HEAD", "HEAD", [path]) == expected


class TestStage:
    def test_copies_and_missing_source_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        targets = tuple(str(tmp_path / n) for n in ("a", "b", "c"))
        monkeypatch.setattr(smoke, "_STAGE_TARGETS", targets)
        sources = [str(FIXTURES / n) for n in ("failed.log", "jobs.json", "log-evidence.json")]
        assert smoke.stage(sources) == 0
        assert (tmp_path / "a").read_bytes() == (FIXTURES / "failed.log").read_bytes()
        assert smoke.stage([*sources[:2], str(tmp_path / "missing")]) == 1
        assert "is missing" in capsys.readouterr().err
        assert smoke.stage(sources[:1]) == 1


class TestOutputs:
    def test_bundle_present(self, tmp_path: Path) -> None:
        out = tmp_path / "o.txt"
        out.write_text(
            "BUNDLE_LOCAL=/x/y.json\nBUNDLE_SHA=abc\nBUNDLE_S3_URI=\nBUNDLE_PATH=/p\nBUNDLE_SHA=zzz\n", encoding="utf-8"
        )
        lines = smoke.outputs(out, tmp_path / "none")
        assert "bundle_sha=abc" in lines
        assert "bundle_path=/p" in lines
        assert "bundle_s3_uri=" in lines
        assert "bundle_absent=false" in lines

    def test_absent_and_unreadable(self, tmp_path: Path) -> None:
        assert "bundle_absent=true" in smoke.outputs(tmp_path / "missing.txt", tmp_path)

    def test_bundle_files_alone_are_not_absent(self, tmp_path: Path) -> None:
        (tmp_path / "one.json").write_text("{}", encoding="utf-8")
        (tmp_path / "two.json").write_text("{}", encoding="utf-8")
        assert "bundle_absent=false" in smoke.outputs(tmp_path / "missing.txt", tmp_path)


class TestVerify:
    def _verify(self, env: dict) -> int:
        return smoke.verify(SMOKE_ID, env["bundles_dir"], env["sink"], env["fallback_sink"], env["agent_output"])

    def test_passes_on_a_good_pair(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._verify(_env(tmp_path, GOOD_AGENT, GOOD_FALLBACK)) == 0
        assert "verify: ok" in capsys.readouterr().out

    def test_no_bundle(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        env = _env(tmp_path, GOOD_AGENT, GOOD_FALLBACK)
        (env["bundles_dir"] / "abc.json").unlink()
        assert self._verify(env) == 1
        assert "emitted no bundle" in capsys.readouterr().err

    def test_sha_mismatch_says_ref_is_optional(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._verify(_env(tmp_path, {"evidence_bundle_ref": {"sha256": "other"}}, GOOD_FALLBACK)) == 1
        assert "evidence_bundle_ref is optional" in capsys.readouterr().err

    def test_missing_ref_entirely(self, tmp_path: Path) -> None:
        assert self._verify(_env(tmp_path, {}, GOOD_FALLBACK)) == 1

    def test_no_undetermined_fallback(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._verify(_env(tmp_path, GOOD_AGENT, {"rca_confidence": "high"})) == 1
        assert "no undetermined filing" in capsys.readouterr().err

    def test_missing_marker(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._verify(_env(tmp_path, GOOD_AGENT, GOOD_FALLBACK, marker="FILED: none")) == 1
        assert f"no FILED: {SMOKE_ID} marker" in capsys.readouterr().err

    def test_marker_ignored_when_sink_unarmed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CI_RCA_SMOKE_SINK")
        assert self._verify(_env(tmp_path, GOOD_AGENT, GOOD_FALLBACK)) == 1


class TestSinkRecords:
    def test_filters_every_wrong_shape(self, tmp_path: Path) -> None:
        sink = tmp_path / "s.jsonl"
        lines = [
            "not json",
            "[1, 2]",
            json.dumps({"action": "file_ops", "table": "ops_recommendations", "record": 5}),
            _line({"a": 1}, action="update_ops"),
            _line({"a": 1}, table="ops_decisions"),
            _line({"a": 1}, source="manual"),
            _line("{bad json"),
            _line("[1, 2]"),
            _line({"keep": True}),
        ]
        sink.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert smoke._sink_records(sink) == [{"keep": True}]
        assert smoke._sink_records(tmp_path / "missing") == []

    def test_context_given_as_object(self, tmp_path: Path) -> None:
        sink = tmp_path / "s.jsonl"
        record = {"source": "ci_rca", "context_v2_json": {"x": 1}}
        sink.write_text(
            json.dumps({"action": "file_ops", "table": "ops_recommendations", "record": record}) + "\n", encoding="utf-8"
        )
        assert smoke._sink_records(sink) == [{"x": 1}]


class TestBundleShas:
    def test_unreadable_and_shaless_bundles_fall_back_to_the_stem(self, tmp_path: Path) -> None:
        (tmp_path / "bad.json").write_text("{nope", encoding="utf-8")
        (tmp_path / "list.json").write_text("[]", encoding="utf-8")
        (tmp_path / "ok.json").write_text(json.dumps({"sha256": "s"}), encoding="utf-8")
        assert smoke._bundle_shas(tmp_path) == ["list", "s"]


class TestMain:
    def test_dispatch(self, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
        assert smoke.main(["select", "--before", "HEAD", "--after", "HEAD", "--changed", "docs/PROJECT_CONTEXT.md"]) == 0
        assert capsys.readouterr().out.strip() == "run=false"
        monkeypatch.setattr(smoke, "_STAGE_TARGETS", tuple(str(tmp_path / n) for n in "abc"))
        assert smoke.main(["stage", *(str(FIXTURES / n) for n in ("failed.log", "jobs.json", "log-evidence.json"))]) == 0
        out = tmp_path / "o.txt"
        out.write_text("BUNDLE_SHA=q\n", encoding="utf-8")
        assert smoke.main(["outputs", str(out), str(tmp_path)]) == 0
        assert "bundle_sha=q" in capsys.readouterr().out
        (tmp_path / "v").mkdir()
        env = _env(tmp_path / "v", GOOD_AGENT, GOOD_FALLBACK)
        argv = ["verify", "--smoke-id", SMOKE_ID, "--bundles-dir", str(env["bundles_dir"])]
        assert smoke.main([*argv, str(env["sink"]), str(env["fallback_sink"]), str(env["agent_output"])]) == 0

    def test_module_entrypoint(self) -> None:
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.ci_rca.smoke",
                "select",
                "--before",
                "HEAD",
                "--after",
                "HEAD",
                "--changed",
                "x.md",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=Path(__file__).resolve().parents[1],
            check=False,
        )
        assert proc.stdout.strip() == "run=false"


def test_committed_fixtures_yield_one_code_regression_bundle(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import scripts.ci_rca.evidence as ev

    emit = tmp_path / "emit"
    args = [
        "--no-upload",
        "--log-file",
        str(FIXTURES / "failed.log"),
        "--retrieval-envelope",
        str(FIXTURES / "log-evidence.json"),
        "--jobs-file",
        str(FIXTURES / "jobs.json"),
        "--workflow-name",
        "CI",
        "--workflow-run-id",
        "900000001",
        "--emit-dir",
        str(emit),
    ]
    with (
        patch.object(ev, "_PENDING_DIR", tmp_path / "pending"),
        patch("scripts.ci_rca.tier_map.probe_runtime", return_value=("median=50ms", 0.05)),
        patch("scripts.ci_rca.tier_map.build_tier_membership", return_value={}),
        patch("scripts.ci_rca.evidence._upload_to_s3") as upload,
    ):
        ev.main(args)
    upload.assert_not_called()
    bundles = sorted(emit.glob("*.json"))
    assert len(bundles) == 1
    assert json.loads(bundles[0].read_text(encoding="utf-8"))["failure_category"] == "code_regression"
    assert "BUNDLE_S3_URI=\n" in capsys.readouterr().out


def test_select_imports_only_the_declared_third_party_set() -> None:
    root = Path(__file__).resolve().parents[1]
    code = (
        "import sys\n"
        "allowed = set(sys.stdlib_module_names) | {'scripts', 'src', 'yaml', 'networkx', '__future__'}\n"
        "class Guard:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] not in allowed:\n"
        "            raise ImportError('refused ' + name)\n"
        "        return None\n"
        "sys.meta_path.insert(0, Guard())\n"
        "import scripts.ci_rca.smoke\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root, check=False
    )
    assert proc.returncode == 0, proc.stderr
