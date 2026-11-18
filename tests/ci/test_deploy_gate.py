"""scripts/ci/deploy_gate.py: newest-green resolution, decide rules 0-5, the CLI contract and freshness."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import pytest

from scripts.ci import deploy_gate as dg

PATHSPECS = ("src/w",)


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.mkdir(parents=True)
        self.git = dg.Git(str(path))
        self.git.out("init", "-q", "-b", "main")
        self.git.out("config", "user.email", "t@example.com")
        self.git.out("config", "user.name", "t")
        self.git.out("config", "commit.gpgsign", "false")
        self.n = 0

    def commit(self, *files: str) -> str:
        self.n += 1
        for rel in files or (f"misc/{self.n}.txt",):
            target = self.path / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{self.n}\n", encoding="utf-8")
        self.git.out("add", "-A")
        self.git.out("commit", "-q", "-m", f"c{self.n}")
        return self.git.out("rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path) -> Repo:
    return Repo(tmp_path / "r")


class FakeApi:
    def __init__(self) -> None:
        self.runs: dict[str, list[dict[str, Any]]] = {}
        self.jobs: dict[int, list[dict[str, Any]]] = {}
        self.fail = False
        self.paths: list[str] = []
        self._id = 100

    def add(
        self,
        sha: str,
        conclusion: str = "success",
        step: str = dg.FULL_TIER_STEP,
        step_conclusion: str = "success",
        job_conclusion: str = "success",
        updated: str = "2026-01-01T00:00:00Z",
    ) -> None:
        self._id += 1
        self.runs.setdefault(sha, []).append(
            {"id": self._id, "head_sha": sha, "conclusion": conclusion, "updated_at": updated}
        )
        steps = [{"name": step, "conclusion": step_conclusion}] if step else []
        self.jobs[self._id] = [{"name": dg.FULL_TIER_JOB, "conclusion": job_conclusion, "steps": steps}]

    def get(self, path: str) -> dict[str, Any]:
        self.paths.append(path)
        if self.fail:
            raise dg.GateError("boom")
        if "/jobs" in path:
            return {"jobs": self.jobs[int(path.split("/")[2])]}
        sha = path.split("head_sha=")[1].split("&")[0]
        assert "event=push" in path and "branch=main" in path
        return {"workflow_runs": self.runs.get(sha, [])}


def gate_repo(repo: Repo) -> list[str]:
    """c1 (pre-gate), c2 adds the gate module, c3 watched, c4 unwatched, c5 unwatched."""
    c1 = repo.commit("src/w/a.py")
    c2 = repo.commit(dg.GATE_MODULE)
    c3 = repo.commit("src/w/a.py")
    c4 = repo.commit()
    c5 = repo.commit()
    return [c1, c2, c3, c4, c5]


def test_newest_green_picks_newest_full_tier_pass(repo: Repo) -> None:
    c = gate_repo(repo)
    api = FakeApi()
    api.add(c[4], conclusion="failure")
    api.add(c[3], step_conclusion="skipped")
    api.add(c[2])
    api.add(c[1])
    assert dg.newest_green_sha(api, repo.git, ref="HEAD") == c[2]


def test_fast_tier_only_run_is_not_green(repo: Repo) -> None:
    c = gate_repo(repo)
    api = FakeApi()
    api.add(c[4], step="")
    api.add(c[3], step="Validate fast tier")
    api.add(c[2], job_conclusion="failure")
    assert dg.newest_green_sha(api, repo.git, ref="HEAD") is None


def test_running_and_missing_runs_are_passed_over(repo: Repo) -> None:
    c = gate_repo(repo)
    api = FakeApi()
    api.add(c[4], conclusion="")
    api.add(c[2])
    assert dg.newest_green_sha(api, repo.git, ref="HEAD") == c[2]


def test_pr_event_runs_are_never_queried(repo: Repo) -> None:
    c = gate_repo(repo)
    api = FakeApi()
    api.add(c[4])
    dg.newest_green_sha(api, repo.git, ref="HEAD")
    assert api.paths and all("event=push" in p for p in api.paths if "head_sha" in p)


def test_run_for_another_sha_is_ignored(repo: Repo) -> None:
    c = gate_repo(repo)
    api = FakeApi()
    api.add(c[4])
    api.runs[c[4]][0]["head_sha"] = c[0]
    assert dg.newest_green_sha(api, repo.git, ref="HEAD") is None


def test_settled_before_excludes_a_just_finished_run(repo: Repo) -> None:
    c = gate_repo(repo)
    api = FakeApi()
    api.add(c[4], updated="2026-01-01T12:00:00Z")
    api.add(c[2], updated="2026-01-01T10:00:00Z")
    cutoff = datetime(2026, 1, 1, 11, 0, tzinfo=timezone.utc)
    assert dg.newest_green_sha(api, repo.git, ref="HEAD", settled_before=cutoff) == c[2]
    assert dg.newest_green_sha(api, repo.git, ref="HEAD") == c[4]


def test_api_error_raises_gate_error(repo: Repo) -> None:
    gate_repo(repo)
    api = FakeApi()
    api.fail = True
    with pytest.raises(dg.GateError):
        dg.newest_green_sha(api, repo.git, ref="HEAD")


def test_limit_bounds_the_walk(repo: Repo) -> None:
    c = gate_repo(repo)
    api = FakeApi()
    api.add(c[0])
    assert dg.newest_green_sha(api, repo.git, limit=2, ref="HEAD") is None


def test_rule0_pre_gate_target_skips(repo: Repo) -> None:
    c = gate_repo(repo)
    assert dg.decide(c[0], {"f": None}, PATHSPECS, repo.git, True) == (False, "pre_gate_target")


def test_rule1_never_rolls_back_even_with_missing_record(repo: Repo) -> None:
    c = gate_repo(repo)
    assert dg.decide(c[2], {"a": c[4], "b": None}, PATHSPECS, repo.git, True) == (False, "newer_deployed")


@pytest.mark.parametrize("record", [None, "", "0" * 40])
def test_rule2_unknown_baseline_deploys(repo: Repo, record: Optional[str]) -> None:
    c = gate_repo(repo)
    assert dg.decide(c[4], {"a": c[4], "b": record}, PATHSPECS, repo.git, False) == (True, "baseline_unknown")


def test_rule3_already_deployed_and_force_bypasses(repo: Repo) -> None:
    c = gate_repo(repo)
    records: dict[str, str | None] = {"a": c[4], "b": c[4]}
    assert dg.decide(c[4], records, PATHSPECS, repo.git, False) == (False, "already_deployed")
    assert dg.decide(c[4], records, PATHSPECS, repo.git, True) == (True, "source_changed")


def test_rule4_no_source_change_skips_and_force_bypasses(repo: Repo) -> None:
    c = gate_repo(repo)
    records: dict[str, str | None] = {"a": c[2], "b": c[3]}
    assert dg.decide(c[4], records, PATHSPECS, repo.git, False) == (False, "no_source_change")
    assert dg.decide(c[4], records, PATHSPECS, repo.git, True) == (True, "source_changed")


def test_rule4_oldest_record_anchor_under_a_hold(repo: Repo) -> None:
    c = gate_repo(repo)
    c6 = repo.commit("src/w/b.py")
    c7 = repo.commit()
    records: dict[str, str | None] = {"maintenance": c[2], "serving": c6}
    assert dg.decide(c7, records, PATHSPECS, repo.git, False) == (True, "source_changed")


def test_rule5_source_changed_deploys(repo: Repo) -> None:
    c = gate_repo(repo)
    c6 = repo.commit("src/w/b.py")
    assert dg.decide(c6, {"a": c[2], "b": c[2]}, PATHSPECS, repo.git, False) == (True, "source_changed")


def test_forked_records_fall_to_rule5(repo: Repo) -> None:
    c = gate_repo(repo)
    repo.git.out("checkout", "-q", "-b", "side", c[2])
    side = repo.commit()
    repo.git.out("checkout", "-q", "main")
    assert dg.decide(c[4], {"a": c[3], "b": side}, PATHSPECS, repo.git, False) == (True, "source_changed")


def test_force_never_bypasses_rules_0_to_2(repo: Repo) -> None:
    c = gate_repo(repo)
    assert dg.decide(c[0], {"a": c[0]}, PATHSPECS, repo.git, True)[1] == "pre_gate_target"
    assert dg.decide(c[2], {"a": c[4]}, PATHSPECS, repo.git, True)[1] == "newer_deployed"
    assert dg.decide(c[4], {"a": None}, PATHSPECS, repo.git, True)[1] == "baseline_unknown"


def run_resolve(repo: Repo, api: FakeApi, tmp_path: Path, records: dict[str, Optional[str]], force: bool = False) -> int:
    return dg.resolve(
        "ducklake",
        force,
        str(tmp_path / "m.json"),
        api=api,
        git=repo.git,
        reader=lambda channel, fn: records[fn],
        inputs=(list(records), PATHSPECS),
    )


def test_resolve_writes_manifest_and_github_output(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    c = gate_repo(repo)
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.setenv("GITHUB_RUN_ID", "77")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    api = FakeApi()
    api.add(c[4])
    repo.git.out("update-ref", "refs/remotes/origin/main", c[4])
    rc = run_resolve(repo, api, tmp_path, {"a": c[2], "b": c[2]})
    assert rc == 0
    assert capsys.readouterr().out.strip() == f"DEPLOY-GATE skip reason=no_source_change sha={c[4]}"
    manifest = json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))
    assert manifest == {
        "channel": "ducklake",
        "sha": c[4],
        "deploy": False,
        "reason": "no_source_change",
        "records": {"a": c[2], "b": c[2]},
        "run_id": "77",
    }
    assert out.read_text(encoding="utf-8").splitlines() == [f"sha={c[4]}", "deploy=false", "reason=no_source_change"]


def test_resolve_no_green_commit_skips_with_notice(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    gate_repo(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    repo.git.out("update-ref", "refs/remotes/origin/main", "HEAD")
    assert run_resolve(repo, FakeApi(), tmp_path, {"a": None}) == 0
    captured = capsys.readouterr().out
    assert "::notice::" in captured and "reason=no_green_in_window" in captured
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["deploy"] is False


def test_resolve_api_error_exits_2(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate_repo(repo)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    repo.git.out("update-ref", "refs/remotes/origin/main", "HEAD")
    api = FakeApi()
    api.fail = True
    assert run_resolve(repo, api, tmp_path, {"a": None}) == 2
    assert not (tmp_path / "m.json").exists()


def test_resolve_refuses_a_shallow_clone(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    gate_repo(repo)
    shallow = tmp_path / "s"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo.path}", str(shallow)], check=True)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    rc = dg.resolve(
        "ducklake", False, str(tmp_path / "m.json"), api=FakeApi(), git=dg.Git(str(shallow)),
        reader=lambda c, f: None, inputs=(["a"], PATHSPECS),
    )  # fmt: skip
    assert rc == 2


@pytest.fixture
def aws_stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    stub = bindir / "aws"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'key="$3"; f="$STUB_DIR/$(basename "$key")"\n'
        'if [ -f "$f" ]; then cat "$f"; exit 0; fi\n'
        'echo "fatal error: An error occurred (404) when calling the HeadObject operation: Key does not exist" >&2\n'
        "exit 1\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    records = tmp_path / "records"
    records.mkdir()
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("STUB_DIR", str(records))
    monkeypatch.delenv("AWS_REGION", raising=False)
    return records


def test_freshness_ok_and_stale_and_absent(aws_stub: Path, capsys: Any) -> None:
    sha = "a" * 40
    (aws_stub / "fn1.json").write_text(json.dumps({"source_git_sha": sha}), encoding="utf-8")
    (aws_stub / "fn2.json").write_text(json.dumps({"source_git_sha": "b" * 40}), encoding="utf-8")
    assert dg.main(["freshness", "--channel", "ducklake", "--expected-sha", sha, "fn1"]) == 0
    assert f"OK fn1 deploy record fresh: {sha}" in capsys.readouterr().out
    assert dg.main(["freshness", "--channel", "ducklake", "--expected-sha", sha, "fn1", "fn2"]) == 1
    assert "DEPLOY_RECORD_STALE fn2" in capsys.readouterr().err
    assert dg.main(["freshness", "--channel", "prod", "--expected-sha", sha, "missing"]) == 1
    err = capsys.readouterr().err
    assert "DEPLOY_RECORD_STALE missing" in err and "(404) when calling the HeadObject operation" in err


def test_read_record_distinguishes_absent_from_other_failures(aws_stub: Path) -> None:
    assert dg.read_record_sha("ducklake", "nothing") is None

    def forbidden(cmd: list[str], **_: Any) -> Any:
        return subprocess.CompletedProcess(cmd, 1, "", "An error occurred (403) when calling the HeadObject operation")

    with pytest.raises(dg.GateError):
        dg.read_record_sha("ducklake", "x", run=forbidden)


def test_module_imports_only_stdlib() -> None:
    source = Path(dg.__file__).read_text(encoding="utf-8")
    top = source.split("\nclass GateError")[0]
    assert "yaml" not in top and "pydantic" not in top and "from scripts" not in top


@pytest.mark.parametrize("channel", ["ducklake", "prod", "telemetry-content", "persona_image"])
def test_channel_inputs_resolve_real_functions_and_pathspecs(channel: str) -> None:
    functions, pathspecs = dg._channel_inputs(channel)
    assert functions and pathspecs and all(isinstance(f, str) for f in functions)


CONTENT_CONFIG = dg.PRE_CHANNEL_FILE
CONTENT_SPECS = (
    "src/lambdas/ducklake_telemetry_content_reader",
    "src/common/ducklake_content_read_verbs.py",
    "src/sensitive_values",
)


def test_telemetry_content_channel_resolves_its_function_and_pathspecs() -> None:
    functions, pathspecs = dg._channel_inputs("telemetry-content")
    assert functions == ["agent-platform-ducklake-telemetry-content"]
    assert pathspecs == CONTENT_SPECS


def test_an_unknown_channel_raises_instead_of_falling_through_to_prod() -> None:
    with pytest.raises(dg.GateError):
        dg._channel_inputs("telemetry-contnet")


def test_pathspecs_subcommand_prints_exactly_the_protective_set(capsys: Any) -> None:
    assert dg.main(["pathspecs", "--channel", "telemetry-content"]) == 0
    assert capsys.readouterr().out.splitlines() == list(CONTENT_SPECS)


def test_a_target_without_deploy_config_is_pre_channel_and_never_deploys(repo: Repo) -> None:
    c1 = repo.commit(dg.GATE_MODULE)
    assert dg.decide(c1, {"f": None}, CONTENT_SPECS, repo.git, False, CONTENT_CONFIG) == (False, "pre_channel_target")
    c2 = repo.commit(CONTENT_CONFIG)
    assert dg.decide(c2, {"f": None}, CONTENT_SPECS, repo.git, False, CONTENT_CONFIG) == (True, "baseline_unknown")


def test_shared_module_change_is_no_source_change_and_masking_change_is_source_changed(repo: Repo) -> None:
    base = repo.commit(dg.GATE_MODULE, CONTENT_CONFIG)
    shared = repo.commit("src/common/ducklake_runtime.py")
    assert dg.decide(shared, {"f": base}, CONTENT_SPECS, repo.git, False, CONTENT_CONFIG) == (False, "no_source_change")
    masked = repo.commit("src/sensitive_values/engine.py")
    assert dg.decide(masked, {"f": base}, CONTENT_SPECS, repo.git, False, CONTENT_CONFIG) == (True, "source_changed")


def _resolve_channel(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, functions: list[str], records: dict, full: Any
) -> list[str]:
    out = tmp_path / "out.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    sha = repo.commit(dg.GATE_MODULE, CONTENT_CONFIG)
    api = FakeApi()
    api.add(sha)
    repo.git.out("update-ref", "refs/remotes/origin/main", sha)
    seen = {fn: (sha if records.get(fn) else None) for fn in functions}
    dg.resolve(
        "telemetry-content",
        False,
        str(tmp_path / "m.json"),
        api=api,
        git=repo.git,
        reader=lambda channel, fn: seen[fn],
        inputs=(functions, CONTENT_SPECS),
        record_reader=full,
    )
    return out.read_text(encoding="utf-8").splitlines()


def test_single_function_channel_emits_record_outputs(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = _resolve_channel(repo, tmp_path, monkeypatch, ["f"], {"f": True}, lambda ch, fn: {"code_sha256": "ZGlnZXN0"})
    assert any(line.startswith("record_sha=") for line in lines) and "record_code_sha256=ZGlnZXN0" in lines


def test_absent_record_and_two_function_channel_emit_no_record_outputs(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(ch: str, fn: str) -> dict:
        raise AssertionError("record_reader must not run")

    lines = _resolve_channel(repo, tmp_path, monkeypatch, ["f"], {}, forbidden)
    assert not any(line.startswith("record_") for line in lines)


def test_two_function_channel_never_reads_a_full_record(repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(ch: str, fn: str) -> dict:
        raise AssertionError("record_reader must not run")

    lines = _resolve_channel(repo, tmp_path, monkeypatch, ["f", "g"], {"f": True, "g": True}, forbidden)
    assert not any(line.startswith("record_") for line in lines)


def test_read_record_returns_the_mapping_and_none_when_absent() -> None:
    import types

    ok = types.SimpleNamespace(returncode=0, stdout='{"source_git_sha": "abc", "code_sha256": "x"}', stderr="")
    assert dg.read_record("c", "f", run=lambda *a, **k: ok) == {"source_git_sha": "abc", "code_sha256": "x"}
    absent = types.SimpleNamespace(returncode=1, stdout="", stderr=dg.ABSENT_RECORD_TEXT)
    assert dg.read_record("c", "f", run=lambda *a, **k: absent) is None
    notmap = types.SimpleNamespace(returncode=0, stdout="[1]", stderr="")
    assert dg.read_record("c", "f", run=lambda *a, **k: notmap) is None


def _no_record(ch: str, fn: str) -> dict:
    raise AssertionError("record_reader must not run")


def _persona_resolve(repo: Repo, api: FakeApi, tmp_path: Path, record: Optional[str], required: tuple[str, ...]) -> int:
    repo.git.out("update-ref", "refs/remotes/origin/main", "HEAD")

    def reader(channel: str, fn: str) -> Optional[str]:
        reads.append(fn)
        return record

    reads: list[str] = []
    rc = dg.resolve(
        "persona_image", False, str(tmp_path / "m.json"), api=api, git=repo.git, reader=reader,
        inputs=(["image"], PATHSPECS), record_reader=_no_record, required_paths=required,
    )  # fmt: skip
    _persona_resolve.reads = reads  # type: ignore[attr-defined]
    return rc


def test_persona_image_inputs_are_the_pin_inputs_without_the_pin_file() -> None:
    from src.persona_hosts import load_pin

    functions, pathspecs = dg._channel_inputs("persona_image")
    assert functions == ["image"] and pathspecs == load_pin(dg.ROOT).inputs()
    assert "config/persona_image.yaml" not in pathspecs
    assert "config/persona_image.yaml" in dg._persona_required()


def test_decide_required_paths_skip_pre_channel_and_string_pre_channel_file_still_works(repo: Repo) -> None:
    c = [repo.commit("seed.txt"), repo.commit(dg.GATE_MODULE), repo.commit("config/persona_image.yaml")]
    assert dg.decide(c[1], {"a": None}, PATHSPECS, repo.git, False, required_paths=("config/persona_image.yaml",)) == (
        False, "pre_channel_target",
    )  # fmt: skip
    assert dg.decide(c[2], {"a": None}, PATHSPECS, repo.git, False, required_paths=("config/persona_image.yaml",)) == (
        True, "baseline_unknown",
    )  # fmt: skip
    assert dg.decide(c[1], {"a": None}, PATHSPECS, repo.git, False, "config/persona_image.yaml") == (
        False,
        "pre_channel_target",
    )


def test_persona_image_deploys_on_an_absent_record_and_skips_when_it_equals_the_target(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    c = [repo.commit(dg.GATE_MODULE), repo.commit("config/persona_image.yaml"), repo.commit("src/w/x.py")]
    api = FakeApi()
    api.add(c[-1])
    req = ("config/persona_image.yaml",)
    assert _persona_resolve(repo, api, tmp_path, None, req) == 0
    assert "deploy reason=baseline_unknown" in capsys.readouterr().out
    assert _persona_resolve(repo, api, tmp_path, c[-1], req) == 0
    assert "skip reason=already_deployed" in capsys.readouterr().out


def test_persona_image_pre_channel_and_no_green_skip_without_any_record_read(
    repo: Repo, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    c = [repo.commit(dg.GATE_MODULE), repo.commit("src/w/x.py")]
    api = FakeApi()
    api.add(c[-1])
    assert _persona_resolve(repo, api, tmp_path, None, ("config/persona_image.yaml",)) == 0
    assert "reason=pre_channel_target" in capsys.readouterr().out and _persona_resolve.reads == []  # type: ignore[attr-defined]
    assert _persona_resolve(repo, FakeApi(), tmp_path, None, ("config/persona_image.yaml",)) == 0
    assert "reason=no_green_in_window" in capsys.readouterr().out and _persona_resolve.reads == []  # type: ignore[attr-defined]


def test_first_publish_is_refused_for_other_channels_and_persona_image_is_not_a_lambda_channel(capsys: Any) -> None:
    assert dg.main(["resolve", "--channel", "prod", "--first-publish", "--manifest", "m.json"]) == 2
    assert "persona_image" in capsys.readouterr().err
    for cmd in (["pathspecs"], ["freshness", "--expected-sha", "a" * 40, "f"]):
        with pytest.raises(SystemExit) as exc:
            dg.main([cmd[0], "--channel", "persona_image", *cmd[1:]])
        assert exc.value.code == 2


def test_main_binds_the_registry_reader_to_the_pin_image_and_the_env_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    from src.persona_hosts import load_pin

    registry = importlib.import_module("scripts.ci.registry_record")
    seen: dict[str, Any] = {}
    monkeypatch.setattr(registry, "read_record", lambda image, **kw: seen.update(image=image, **kw) or "rev")
    monkeypatch.setenv("GITHUB_TOKEN", "tok-value")
    captured: dict[str, Any] = {}
    monkeypatch.setattr(dg, "resolve", lambda *a, **kw: captured.update(args=a, **kw) or 0)
    assert dg.main(["resolve", "--channel", "persona_image", "--first-publish", "--manifest", "m.json"]) == 0
    assert captured["reader"]("persona_image", "image") == "rev"
    assert seen == {"image": load_pin(dg.ROOT).image, "token": "tok-value", "first_publish": True}
    captured.clear()
    assert dg.main(["resolve", "--channel", "ducklake", "--manifest", "m.json"]) == 0
    assert "reader" not in captured


def test_record_error_and_unreadable_pin_become_gate_errors_exit_2(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: Any
) -> None:
    import importlib

    registry = importlib.import_module("scripts.ci.registry_record")

    def boom(image: str, **_: Any) -> None:
        raise registry.RecordError("denied")

    monkeypatch.setattr(registry, "read_record", boom)
    with pytest.raises(dg.GateError):
        dg.persona_image_reader()("persona_image", "image")
    monkeypatch.setattr(dg, "ROOT", Path("/nonexistent-root"))
    with pytest.raises(dg.GateError):
        dg._channel_inputs("persona_image")
    git = Repo(tmp_path / "r").git
    assert dg.resolve("persona_image", False, str(tmp_path / "m.json"), api=FakeApi(), git=git) == 2
    assert "::error::DEPLOY-GATE" in capsys.readouterr().err


def test_registry_record_imports_nothing_from_deploy_gate() -> None:
    import ast

    tree = ast.parse((Path(dg.__file__).parent / "registry_record.py").read_text(encoding="utf-8"))
    names = [a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names]
    names += [n.module or "" for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)]
    assert not [n for n in names if "deploy_gate" in n]
