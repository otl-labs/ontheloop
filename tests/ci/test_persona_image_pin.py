"""scripts/ci/persona_image_pin.py over tmp_path git repositories and injected registry and gh seams."""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from scripts.ci import persona_image_pin as pin
from scripts.ci import registry_record as rr

ROOT = Path(__file__).resolve().parents[2]
D1, D2 = "sha256:" + "1" * 64, "sha256:" + "2" * 64
HEADER = "# header comment\nimage: x/y\n"
TAIL = (
    "build:\n  dockerfile: d/Dockerfile\n  files: [d/Dockerfile]\n  copy: [a.py]\n"
    "consumers:\n  fargate: {status: pending, owning_item: 'T4.15:c12'}\n"
)


def pin_text(digest: str | None, commit: str | None, image: str = "x/y") -> str:
    d = "null" if digest is None else f'"{digest}"'
    c = "null" if commit is None else f'"{commit}"'
    return f"{HEADER.replace('x/y', image)}digest: {d}\nsource_commit: {c}\n{TAIL}"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@example.invalid", "-c", "user.name=t", *args],
        cwd=repo, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


class Repo:
    """c1 -> c2 -> base on main; the base commit carries base_pin."""

    def __init__(self, tmp: Path, base_pin: str) -> None:
        self.path = tmp
        git(tmp, "init", "-q", "-b", "main")
        (tmp / "config").mkdir()
        (tmp / "f.txt").write_text("1")
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "c1")
        self.c1 = git(tmp, "rev-parse", "HEAD")
        (tmp / "f.txt").write_text("2")
        git(tmp, "commit", "-qam", "c2")
        self.c2 = git(tmp, "rev-parse", "HEAD")
        (tmp / "config/persona_image.yaml").write_text(base_pin)
        git(tmp, "add", "-A")
        git(tmp, "commit", "-qm", "base")
        self.base = git(tmp, "rev-parse", "HEAD")
        self.stray = "f" * 40

    def head(self, text: str) -> None:
        (self.path / "config/persona_image.yaml").write_text(text)


class Gh:
    def __init__(self, code: int = 0) -> None:
        self.code, self.calls = code, []

    def __call__(self, argv: list[str], **kw: Any) -> Any:
        if argv[0] == "gh":
            self.calls.append(argv)
            return SimpleNamespace(returncode=self.code, stdout="", stderr="denied" if self.code else "")
        return pin._run(argv, **kw)


class Reader:
    def __init__(self, revision: str | None, digest: str = D2, fail: Exception | None = None) -> None:
        self.revision, self.digest, self.fail, self.calls = revision, digest, fail, []

    def __call__(self, image: str, reference: str, *, token: Any) -> rr.ImageRecord:
        self.calls.append((image, reference, token))
        if self.fail:
            raise self.fail
        return rr.ImageRecord(self.digest if reference == "main" else reference, self.revision)


ENV = {"GITHUB_REPOSITORY": "o/r"}


def run_verify(repo: Repo, gh: Gh, reader: Reader) -> str:
    return pin.verify(repo.base, repo.path, run=gh, reader=reader, env=ENV)


def refused(rule: str, repo: Repo, gh: Gh, reader: Reader) -> None:
    with pytest.raises(pin.PinError) as err:
        run_verify(repo, gh, reader)
    assert err.value.rule == rule


def test_first_pin_verified_with_exact_gh_argv(tmp_path: Path) -> None:
    repo, gh = Repo(tmp_path, pin_text(None, None)), Gh()
    repo.head(pin_text(D2, repo.c2))
    line = run_verify(repo, gh, Reader(repo.c2))
    assert line == f"PERSONA-IMAGE-PIN verified digest={D2} source_commit={repo.c2}"
    argv = gh.calls[0]
    assert argv[:4] == ["gh", "attestation", "verify", f"oci://x/y@{D2}"]
    assert argv[4:] == [
        "--repo", "o/r", "--signer-workflow", "o/r/.github/workflows/persona-image-publish.yml",
        "--source-ref", "refs/heads/main", "--predicate-type", "https://slsa.dev/provenance/v1",
        "--deny-self-hosted-runners", "--format", "json",
    ]  # fmt: skip
    assert "--source-digest" not in argv


def test_unchanged_skips_without_registry_or_gh(tmp_path: Path) -> None:
    repo, gh, reader = Repo(tmp_path, pin_text(D1, "a" * 40)), Gh(), Reader("a" * 40)
    repo.head(pin_text(D1, "a" * 40).replace("# header comment", "# edited comment"))
    assert run_verify(repo, gh, reader) == "PERSONA-IMAGE-PIN skip reason=unchanged"
    assert not gh.calls and not reader.calls


def test_skip_survives_a_schema_change_outside_the_three_lines(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    repo.head(pin_text(None, None).replace("fargate:", "future_key: 1\n  fargate:"))
    assert run_verify(repo, Gh(), Reader(None)).endswith("skip reason=unchanged")


def test_image_line_change_refused(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    repo.head(pin_text(None, None, image="x/z"))
    refused("image", repo, Gh(), Reader(None))


def test_set_pin_returning_to_null_refused(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(D1, "a" * 40))
    repo.head(pin_text(None, None))
    refused("a", repo, Gh(), Reader(None))


def test_unparsable_base_pin_fails_closed(tmp_path: Path) -> None:
    repo = Repo(tmp_path, "image: x/y\ndigest: null\nsource_commit: null\nbuild: nope\n")
    repo.head(pin_text(D2, repo.c2))
    refused("base", repo, Gh(), Reader(repo.c2))


def test_rule_a_one_line_missing_refused(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    repo.head(pin_text(D2, repo.c2).replace(f'source_commit: "{repo.c2}"\n', ""))
    refused("a", repo, Gh(), Reader(repo.c2))


def test_rule_b_new_commit_same_digest_refused(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(D2, "1" * 40))
    repo.head(pin_text(D2, repo.c2))
    refused("b", repo, Gh(), Reader(repo.c2))


def test_rule_c_commit_not_in_base_refused(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    repo.head(pin_text(D2, "9" * 40))
    refused("git", repo, Gh(), Reader("9" * 40))
    git(repo.path, "checkout", "-q", "--", "config/persona_image.yaml")
    git(repo.path, "checkout", "-qb", "side", repo.c1)
    (repo.path / "g.txt").write_text("x")
    git(repo.path, "add", "-A")
    git(repo.path, "commit", "-qm", "side")
    side = git(repo.path, "rev-parse", "HEAD")
    git(repo.path, "checkout", "-q", "main")
    repo.head(pin_text(D2, side))
    refused("c", repo, Gh(), Reader(side))


def test_rule_d_rollback_refused_and_forced_rebuild_allowed(tmp_path: Path) -> None:
    probe = Repo(tmp_path, pin_text(None, None))
    base_pin = pin_text(D1, probe.c2)
    git(probe.path, "checkout", "-q", "-b", "set")
    probe.head(base_pin)
    git(probe.path, "commit", "-qam", "set")
    probe.base = git(probe.path, "rev-parse", "HEAD")
    probe.head(pin_text(D2, probe.c1))
    refused("d", probe, Gh(), Reader(probe.c1))
    probe.head(pin_text(D2, probe.c2))
    assert run_verify(probe, Gh(), Reader(probe.c2)).startswith("PERSONA-IMAGE-PIN verified")


def test_rule_e_label_mismatch_and_index_answer_refused(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    repo.head(pin_text(D2, repo.c2))
    refused("e", repo, Gh(), Reader(repo.c1))
    refused("e", repo, Gh(), Reader(None))
    with pytest.raises(rr.RecordError):
        run_verify(repo, Gh(), Reader(None, fail=rr.RecordError("expected a single image manifest")))


def test_gh_failure_and_read_errors_exit_one_never_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    repo.head(pin_text(D2, repo.c2))
    refused("attestation", repo, Gh(code=1), Reader(repo.c2))
    assert pin.main(["verify", "--base", repo.base], root=repo.path, run=Gh(1), reader=Reader(repo.c2), env=ENV) == 1
    err = Reader(None, fail=rr.RecordAbsent("DENIED", "denied"))
    assert pin.main(["verify", "--base", repo.base], root=repo.path, run=Gh(), reader=err, env=ENV) == 1
    assert pin.main(["verify", "--base", "0" * 40], root=repo.path, run=Gh(), reader=Reader(repo.c2), env=ENV) == 1
    assert "skip" not in capsys.readouterr().out


def test_repository_falls_back_to_git_origin(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    git(repo.path, "remote", "add", "origin", "https://github.com/own/rep.git")
    repo.head(pin_text(D2, repo.c2))
    gh = Gh()
    pin.verify(repo.base, repo.path, run=gh, reader=Reader(repo.c2), env={})
    assert gh.calls[0][gh.calls[0].index("--repo") + 1] == "own/rep"


def _origin(repo: Repo) -> None:
    git(repo.path, "update-ref", "refs/remotes/origin/main", repo.base)
    git(repo.path, "remote", "add", "origin", str(repo.path))


def test_update_rewrites_only_the_two_lines(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    _origin(repo)
    before = (repo.path / "config/persona_image.yaml").read_text()
    reader = Reader(repo.c2)
    assert pin.update(repo.path, run=pin._run, reader=reader).startswith("PERSONA-IMAGE-PIN updated")
    after = (repo.path / "config/persona_image.yaml").read_text()
    diff = [(a, b) for a, b in zip(before.splitlines(), after.splitlines()) if a != b]
    assert len(before.splitlines()) == len(after.splitlines()) and len(diff) == 2
    assert (
        f'digest: "{D2}"' in after
        and f'source_commit: "{repo.c2}"  # pragma: allowlist secret' in after
        and "# header comment" in after
    )
    assert yaml.safe_load(after)["digest"] == D2
    assert pin.check(repo.path, reader=reader) == "PERSONA-IMAGE-PIN current"


def test_update_already_current_is_a_no_op(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(D2, "1" * 40))
    repo.head(pin_text(D2, repo.c2))
    before = (repo.path / "config/persona_image.yaml").read_text()
    out = pin.update(repo.path, run=Gh(), reader=Reader(repo.c2))
    assert out == "PERSONA-IMAGE-PIN current" and (repo.path / "config/persona_image.yaml").read_text() == before


def test_update_refuses_a_failed_rule_and_reads_the_main_tag_anonymously(tmp_path: Path) -> None:
    repo = Repo(tmp_path, pin_text(None, None))
    _origin(repo)
    reader = Reader("9" * 40)
    assert pin.main(["update"], root=repo.path, reader=reader) == 1
    assert reader.calls[0] == ("x/y", "main", None)


def test_check_stale_and_read_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    repo = Repo(tmp_path, pin_text(D1, "a" * 40))
    assert pin.check(repo.path, reader=Reader("a" * 40)) == f"PERSONA-IMAGE-PIN stale pinned={D1} newest={D2}"
    assert pin.main(["check"], root=repo.path, reader=Reader(None, fail=rr.RecordError("boom"))) == 1


def _workflow() -> dict[str, Any]:
    return yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))["jobs"]["pr-validate"]


def test_ci_step_shape() -> None:
    job = _workflow()
    names = [s.get("name", "") for s in job["steps"]]
    i = names.index("Persona image pin verification")
    assert names.index("Persona image conformance (local_container suite)") < i
    assert i < names.index("Upload affected-set selection manifest") == len(names) - 1
    step = job["steps"][i]
    assert step["id"] == "persona_image_pin" and step["timeout-minutes"] == 5 and "continue-on-error" not in step
    assert step["env"] == {"GH_TOKEN": "${{ github.token }}", "BASE_SHA": "${{ github.event.pull_request.base.sha }}"}
    assert step["run"].strip() == 'python -m scripts.ci.persona_image_pin verify --base "$BASE_SHA"'
    assert "run_persona" not in step["run"] and "persona_compute_host" not in step["run"]
    assert set(job["permissions"]) - {"attestations"} == {"contents", "actions"}
    assert job["permissions"]["contents"] == "read" and job["permissions"]["actions"] == "read"


def test_capacity_ledger_has_the_publish_row() -> None:
    doc = yaml.safe_load((ROOT / "docs/contracts/actions-capacity.yaml").read_text(encoding="utf-8"))
    rows = [r for r in _walk(doc) if isinstance(r, dict) and r.get("id") == "persona-image-publish"]
    assert len(rows) == 1
    assert rows[0]["live_signal"] == {
        "workflow": ".github/workflows/persona-image-publish.yml", "job": "*", "event": "workflow_run",
    }  # fmt: skip


def _walk(node: Any) -> Any:
    yield node
    for child in node.values() if isinstance(node, dict) else node if isinstance(node, list) else ():
        yield from _walk(child)


def test_codeowners_names_the_pin_script() -> None:
    lines = (ROOT / ".github/CODEOWNERS").read_text(encoding="utf-8").splitlines()
    assert any(ln.split()[:1] == ["/scripts/ci/persona_image_pin.py"] for ln in lines if ln and not ln.startswith("#"))
