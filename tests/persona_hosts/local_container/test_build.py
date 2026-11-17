"""Mirror test for the local_container build helper, without a daemon."""

from __future__ import annotations

from src.persona_hosts.local_container.build import BuildOptions, build_argv


def test_plain_build() -> None:
    argv = build_argv("d/Dockerfile", "/t/iid", BuildOptions(ca_bundle=None, http_proxy=None))
    assert argv == ["docker", "build", "-f", "d/Dockerfile", "--iidfile", "/t/iid", "."]


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
