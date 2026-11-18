"""scripts/ci/persona_image_publish.py and .github/workflows/persona-image-publish.yml, without a daemon."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci import persona_image_publish as pub
from scripts.ci import registry_record as rr
from src.persona_hosts import load_pin

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/persona-image-publish.yml"
IMAGE = load_pin(ROOT).image
SHA = "b" * 40
IMAGE_ID = "sha256:" + "1" * 64
DIGEST = "sha256:" + "2" * 64


class Docker:
    def __init__(self, repo_digests: list[str] | None = None, fail: str | None = None) -> None:
        self.calls: list[tuple[list[str], str | None]] = []
        self.repo_digests = [f"{IMAGE}@{DIGEST}"] if repo_digests is None else repo_digests
        self.fail = fail

    def __call__(
        self,
        argv: list[str],
        input: str | None = None,  # noqa: A002
        **_: Any,
    ) -> subprocess.CompletedProcess[str]:
        self.calls.append((argv, input))
        if self.fail and argv[1] == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "boom")
        out = json.dumps(self.repo_digests) if argv[1] == "inspect" else ""
        return subprocess.CompletedProcess(argv, 0, out, "")


class Registry:
    def __init__(self, config_digest: str = IMAGE_ID, label: str | None = SHA, media: str = rr.OCI_MANIFEST) -> None:
        self.config_digest, self.label, self.media = config_digest, label, media

    def __call__(self, request: Any, timeout: int = 0) -> Any:
        url = request.full_url
        if "/token" in url:
            body: Any = {"token": "t"}
        elif "/manifests/" in url:
            body = {"mediaType": self.media, "config": {"digest": self.config_digest}}
            if self.media == rr.OCI_INDEX:
                body["manifests"] = []
        else:
            body = {"config": {"Labels": {rr.REVISION_LABEL: self.label} if self.label else {}}}

        class R:
            def __enter__(self) -> "R":
                return self

            def __exit__(self, *e: Any) -> None:
                return None

            def read(self) -> bytes:
                return json.dumps(body).encode()

        return R()


@pytest.fixture(autouse=True)
def outputs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    paths = {"GITHUB_OUTPUT": tmp_path / "out", "GITHUB_STEP_SUMMARY": tmp_path / "sum"}
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))
    monkeypatch.setenv("GITHUB_ACTOR", "bot")
    return paths


def do_push(docker: Docker, registry: Registry, token: str = "secret-token") -> tuple[str, str]:
    return pub.push(IMAGE_ID, SHA, token, runner=docker, opener=registry)


def test_build_passes_both_labels_and_emits_the_id(outputs: dict[str, Path]) -> None:
    seen: dict[str, Any] = {}

    def builder(root: Path, options: Any) -> str:
        seen["labels"] = options.labels
        return IMAGE_ID

    assert pub.build(SHA, builder=builder) == IMAGE_ID
    assert seen["labels"] == {
        "org.opencontainers.image.revision": SHA,
        "org.opencontainers.image.licenses": "BUSL-1.1",
    }
    assert outputs["GITHUB_OUTPUT"].read_text() == f"image_id={IMAGE_ID}\n"
    with pytest.raises(pub.PublishError):
        pub.build("short", builder=builder)


def test_push_sends_the_token_on_stdin_never_argv(outputs: dict[str, Path], capsys: Any) -> None:
    docker = Docker()
    assert do_push(docker, Registry()) == (IMAGE, DIGEST)
    assert all("secret-token" not in " ".join(argv) for argv, _ in docker.calls)
    login = next(c for c in docker.calls if c[0][1] == "login")
    assert login[1] == "secret-token" and "--password-stdin" in login[0]
    assert docker.calls[-1][0][1] == "logout"
    assert outputs["GITHUB_OUTPUT"].read_text() == f"image={IMAGE}\ndigest={DIGEST}\n"
    summary = outputs["GITHUB_STEP_SUMMARY"].read_text()
    assert f"digest: {DIGEST}" in summary and f"source_commit: {SHA}" in summary
    assert f"PERSONA-IMAGE-PUBLISHED digest={DIGEST} source_commit={SHA}" in capsys.readouterr().out
    assert [c[0][1] for c in docker.calls[:4]] == ["login", "tag", "push", "inspect"]


@pytest.mark.parametrize(
    "registry",
    [
        Registry(config_digest="sha256:" + "9" * 64),
        Registry(media=rr.OCI_INDEX),
        Registry(label="c" * 40),
        Registry(label=None),
    ],
)
def test_push_refuses_a_manifest_that_is_not_the_tested_image(registry: Registry, outputs: dict[str, Path]) -> None:
    docker = Docker()
    with pytest.raises(pub.PublishError):
        do_push(docker, registry)
    assert not outputs["GITHUB_OUTPUT"].exists()
    assert docker.calls[-1][0][1] == "logout"


def test_push_accepts_the_containerd_store_digest_identity() -> None:
    docker = Docker(repo_digests=[f"{IMAGE}@{IMAGE_ID}"])
    assert do_push(docker, Registry(config_digest="sha256:" + "9" * 64))[1] == IMAGE_ID


def test_push_logs_out_on_a_docker_failure_and_needs_a_token_and_a_digest() -> None:
    docker = Docker(fail="push")
    with pytest.raises(pub.PublishError):
        do_push(docker, Registry())
    assert docker.calls[-1][0][1] == "logout"
    with pytest.raises(pub.PublishError):
        do_push(Docker(repo_digests=[]), Registry())
    with pytest.raises(pub.PublishError):
        do_push(Docker(), Registry(), token="")


def test_main_reports_a_failure_as_exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "")
    assert pub.main(["push", "--image-id", IMAGE_ID, "--sha", SHA]) == 1


def test_build_step_imports_without_pydantic_or_pytest() -> None:
    code = "import sys\nfor m in ('pydantic','pytest'): sys.modules[m] = None\nimport scripts.ci.persona_image_publish\n"
    proc = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def steps(job: str) -> list[dict[str, Any]]:
    return workflow()["jobs"][job]["steps"]


def test_workflow_shape() -> None:
    wf = workflow()
    text = WORKFLOW.read_text(encoding="utf-8")
    on = wf[True]
    assert wf["name"] == "persona-image-publish" and wf["permissions"] == {"contents": "read"}
    assert on["workflow_run"] == {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}
    assert set(on["workflow_dispatch"]["inputs"]) == {"force", "first_publish"}
    assert wf["concurrency"] == {"group": "persona-image-publish", "cancel-in-progress": False}
    jobs = wf["jobs"]
    assert "github.actor != 'otl-workflow-dispatcher[bot]'" in jobs["gate"]["if"]
    assert jobs["gate"]["permissions"] == {"contents": "read", "actions": "read", "packages": "read"}
    assert "pip install pyyaml==" in text and "pydantic==" in text
    assert "deploy_gate resolve --channel persona_image" in jobs["gate"]["steps"][-1]["run"]
    assert jobs["publish"]["needs"] == "gate" and "needs.gate.outputs.deploy == 'true'" in jobs["publish"]["if"]
    assert jobs["publish"]["permissions"] == {"contents": "read", "packages": "write"}
    assert jobs["attest"]["needs"] == "publish"
    assert jobs["attest"]["permissions"] == {"id-token": "write", "attestations": "write"}
    attest_steps = jobs["attest"]["steps"]
    assert len(attest_steps) == 1 and "run" not in attest_steps[0]
    assert attest_steps[0]["uses"].startswith("actions/attest-build-provenance@")
    assert attest_steps[0]["with"]["push-to-registry"] is False
    for name, job in jobs.items():
        if name != "attest":
            assert "id-token" not in (job.get("permissions") or {})


def test_publish_step_order_and_secret_hygiene() -> None:
    ids = [s.get("id") for s in steps("publish")]
    assert ids.index("build") < ids.index("install-dev") < ids.index("suite") < ids.index("push")
    assert ids.index("install-build") < ids.index("build")
    suite = next(s for s in steps("publish") if s.get("id") == "suite")
    assert suite["env"] == {"PERSONA_IMAGE_UNDER_TEST": "${{ steps.build.outputs.image_id }}"}
    assert "tests/persona_hosts/conformance_suite.py" in suite["run"]
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "secrets." not in text and "aws-actions" not in text
    for job in workflow()["jobs"].values():
        for step in job["steps"]:
            blob = json.dumps(step)
            assert "run_persona" not in blob and "persona_compute_host" not in blob


def test_every_uses_is_a_sha_pin_and_checkouts_persist_nothing() -> None:
    for job in workflow()["jobs"].values():
        for step in job["steps"]:
            if "uses" in step:
                assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", step["uses"]), step["uses"]
            if str(step.get("uses", "")).startswith("actions/checkout"):
                assert step["with"]["persist-credentials"] is False
    assert len(re.findall(r"@[0-9a-f]{40} # v\d", WORKFLOW.read_text(encoding="utf-8"))) == 5
