"""Mirror test for the local_container build helper, without a daemon."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from src.persona_hosts.local_container import build
from src.persona_hosts.local_container.build import BuildOptions, build_argv

ROOT = Path(__file__).resolve().parents[3]


def test_plain_build() -> None:
    argv = build_argv("d/Dockerfile", "/t/iid", BuildOptions(ca_bundle=None, http_proxy=None))
    assert argv == [
        "docker", "build", "-f", "d/Dockerfile", "--iidfile", "/t/iid", "--provenance=false", "--sbom=false", "."
    ]  # fmt: skip


def test_ca_bundle_is_a_secret_never_a_build_arg() -> None:
    argv = build_argv("d/Dockerfile", "/t/iid", BuildOptions(ca_bundle="/etc/ca.pem", http_proxy=None))
    assert "--secret" in argv and "id=build_ca,src=/etc/ca.pem" in argv
    assert "--build-arg" not in argv
    assert "/etc/ca.pem" not in [a for a in argv if a.startswith("--build-arg")]


def test_loopback_proxy_uses_host_network() -> None:
    argv = build_argv("d/Dockerfile", "/t/iid", BuildOptions(ca_bundle=None, http_proxy="http://127.0.0.1:3128"))
    assert argv[argv.index("--network") + 1] == "host"
    assert "HTTPS_PROXY=http://127.0.0.1:3128" in argv


def test_non_loopback_proxy_adds_no_host_network() -> None:
    argv = build_argv("d/Dockerfile", "/t/iid", BuildOptions(ca_bundle=None, http_proxy="http://proxy.example:3128"))
    assert "--network" not in argv


def test_defaults_read_the_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PIP_CERT", "SSL_CERT_FILE", "HTTPS_PROXY", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    assert BuildOptions() == BuildOptions(ca_bundle=None, http_proxy=None)
    monkeypatch.setenv("SSL_CERT_FILE", "/ssl.pem")
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.example:1")
    assert BuildOptions() == BuildOptions(ca_bundle="/ssl.pem", http_proxy="http://proxy.example:1")
    monkeypatch.setenv("PIP_CERT", "/pip.pem")
    assert BuildOptions().ca_bundle == "/pip.pem"


def _fake_build(returncode: int = 0, stderr: str = ""):
    def run(argv, **kwargs):
        if returncode == 0:
            Path(argv[argv.index("--iidfile") + 1]).write_text("sha256:" + "e" * 64 + "\n", encoding="utf-8")
        return subprocess.CompletedProcess(argv, returncode, stdout="", stderr=stderr)

    return run


def test_build_image_returns_the_image_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build.subprocess, "run", _fake_build())
    assert build.build_image(ROOT, BuildOptions(ca_bundle=None, http_proxy=None)) == "sha256:" + "e" * 64


def test_build_image_default_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build.subprocess, "run", _fake_build())
    assert build.build_image(ROOT).startswith("sha256:")


def test_build_failure_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(build.subprocess, "run", _fake_build(1, "boom"))
    with pytest.raises(RuntimeError, match="docker build failed"):
        build.build_image(ROOT, BuildOptions(ca_bundle=None, http_proxy=None))


def test_missing_docker_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(argv, **kwargs):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(build.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="docker unreachable"):
        build.build_image(ROOT, BuildOptions(ca_bundle=None, http_proxy=None))


def test_labels_are_sorted_label_pairs() -> None:
    options = BuildOptions(ca_bundle=None, http_proxy=None, labels={"b.k": "2", "a.k": "1"})
    argv = build_argv("d/Dockerfile", "/t/iid", options)
    pairs = [argv[i + 1] for i, a in enumerate(argv) if a == "--label"]
    assert pairs == ["a.k=1", "b.k=2"]


def test_no_labels_by_default_and_attestation_attachments_always_off() -> None:
    argv = build_argv("d/Dockerfile", "/t/iid", BuildOptions(ca_bundle=None, http_proxy=None))
    assert "--label" not in argv
    assert "--provenance=false" in argv and "--sbom=false" in argv
