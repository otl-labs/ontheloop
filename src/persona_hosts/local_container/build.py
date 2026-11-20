"""Build the persona image from a repository tree, passing a session CA as a BuildKit secret."""

from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

from src.persona_hosts.image_pin import load_pin

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _default_ca() -> str | None:
    return os.environ.get("PIP_CERT") or os.environ.get("SSL_CERT_FILE") or None


def _default_proxy() -> str | None:
    return os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or None


@dataclass(frozen=True)
class BuildOptions:
    ca_bundle: str | None = field(default_factory=_default_ca)
    http_proxy: str | None = field(default_factory=_default_proxy)
    docker: str = "docker"
    labels: dict[str, str] = field(default_factory=dict)


def build_argv(dockerfile: str, iidfile: str, options: BuildOptions) -> list[str]:
    argv = [options.docker, "build", "-f", dockerfile, "--iidfile", iidfile, "--provenance=false", "--sbom=false"]
    for key, value in sorted(options.labels.items()):
        argv += ["--label", f"{key}={value}"]
    if options.ca_bundle:
        argv += ["--secret", f"id=build_ca,src={options.ca_bundle}"]
    if options.http_proxy:
        if (urlparse(options.http_proxy).hostname or "") in _LOOPBACK:
            argv += ["--network", "host"]
        argv += ["--build-arg", f"HTTPS_PROXY={options.http_proxy}"]
    return [*argv, "."]


def build_image(root: Path, options: BuildOptions | None = None) -> str:
    pin = load_pin(root)
    options = options or BuildOptions()
    with tempfile.TemporaryDirectory(prefix="persona-build-") as tmp:
        iidfile = str(Path(tmp) / "iid")
        env = {**os.environ, "DOCKER_BUILDKIT": "1"}
        try:
            proc = subprocess.run(
                build_argv(pin.dockerfile, iidfile, options),
                cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
            )  # fmt: skip
        except FileNotFoundError as exc:
            raise RuntimeError(f"docker unreachable: {exc}") from exc
        if proc.returncode != 0:
            raise RuntimeError(f"docker build failed ({proc.returncode}): {proc.stderr[-2000:]}")
        return Path(iidfile).read_text(encoding="utf-8").strip()
