"""A stand-in docker executable for run_image's real argv, so the image layout is exercised with no Docker daemon.

Named outside test_*.py: never collected. Usage: python docker_shim.py run <run_argv flags> IMAGE [args]. It lays out a
temporary /app from docker/persona/Dockerfile's own COPY lines (COPY targets outside /app are skipped), honours the
Dockerfile's ENV and WORKDIR, maps the -v mounts, rewrites /app-prefixed argv to the temporary root, applies
Dockerfile.dockerignore, passes only the -e variables named in argv, and runs the ENTRYPOINT with sys.executable -I.
It proves the image layout, not the image's pinned dependency set (the venv's litellm, not requirements-persona.txt);
the conformance suite stays the image proof. RUN, FROM, USER and LABEL are skipped; any other instruction, COPY --from,
COPY --chown, or a run flag outside the allowlist fails naming it.
"""

from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker/persona/Dockerfile"
DOCKERIGNORE = ROOT / "docker/persona/Dockerfile.dockerignore"
SKIPPED = {"FROM", "RUN", "USER", "LABEL"}
HANDLED = {"ENV", "COPY", "WORKDIR", "ENTRYPOINT"}
BARE_FLAGS = {"--rm", "--read-only"}
VALUE_FLAGS = {"--tmpfs", "--cap-drop", "--security-opt", "--user", "--network", "-v", "-e", "--entrypoint"}


class ShimError(Exception):
    pass


def instructions(text: str) -> list[tuple[str, str]]:
    joined: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        joined.append(pending + line)
        pending = ""
    rows = []
    for line in joined:
        keyword, _, rest = line.partition(" ")
        keyword = keyword.upper()
        if keyword not in SKIPPED | HANDLED:
            raise ShimError(f"unsupported Dockerfile instruction {keyword}")
        rows.append((keyword, rest.strip()))
    return rows


def _ignored(relative: str, patterns: list[str]) -> bool:
    ignored = False
    for pattern in patterns:
        negate = pattern.startswith("!")
        body = pattern[1:] if negate else pattern
        body = body.rstrip("/")
        parts = relative.split("/")
        if body.startswith("**/"):
            hit = any(fnmatch.fnmatch(part, body[3:]) for part in parts)
        else:
            hit = relative == body or relative.startswith(body + "/") or fnmatch.fnmatchcase(relative, body)
        if hit:
            ignored = not negate
    return ignored


def _copy(source: str, target: str, root: Path, patterns: list[str]) -> None:
    src = ROOT / source
    destination = root / target.lstrip("/")
    if not src.exists():
        raise ShimError(f"COPY source {source} does not exist")
    files = [src] if src.is_file() else sorted(p for p in src.rglob("*") if p.is_file())
    for path in files:
        relative = path.relative_to(ROOT).as_posix()
        if _ignored(relative, patterns):
            continue
        out = destination if src.is_file() and not target.endswith("/") else destination / path.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, out)


def build_layout(root: Path) -> tuple[dict[str, str], str, list[str]]:
    patterns = [
        line.strip()
        for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    env: dict[str, str] = {}
    workdir = "/"
    entrypoint: list[str] = []
    for keyword, rest in instructions(DOCKERFILE.read_text(encoding="utf-8")):
        if keyword == "ENV":
            name, _, value = rest.partition("=")
            env[name] = value
        elif keyword == "WORKDIR":
            workdir = rest
        elif keyword == "ENTRYPOINT":
            entrypoint = json.loads(rest)
        elif keyword == "COPY":
            tokens = rest.split()
            if any(t.startswith("--") for t in tokens):
                raise ShimError(f"unsupported COPY flag in: {rest}")
            *sources, target = tokens
            if not target.startswith("/app"):
                continue
            for source in sources:
                _copy(source, target, root, patterns)
    return env, workdir, entrypoint


def parse_run(argv: list[str]) -> tuple[dict[str, str], list[str], str, list[str], list[str]]:
    mounts: dict[str, str] = {}
    env_names: list[str] = []
    entrypoint: list[str] = []
    index = 0
    while index < len(argv) and argv[index].startswith("-"):
        flag = argv[index]
        if flag in BARE_FLAGS:
            index += 1
            continue
        if flag not in VALUE_FLAGS:
            raise ShimError(f"unsupported docker run flag {flag}")
        value = argv[index + 1]
        index += 2
        if flag == "-v":
            host, container, *_ = value.split(":")
            mounts[container] = host
        elif flag == "-e":
            env_names.append(value)
        elif flag == "--entrypoint":
            entrypoint = [value]
    if index >= len(argv):
        raise ShimError("no image given")
    return mounts, env_names, argv[index], entrypoint, argv[index + 1 :]


def main(argv: list[str]) -> int:
    if not argv or argv[0] != "run":
        raise ShimError("only 'docker run' is supported")
    mounts, env_names, _image, override, rest = parse_run(argv[1:])
    with tempfile.TemporaryDirectory(prefix="docker-shim-") as tmp:
        root = Path(tmp) / "rootfs"
        scratch = Path(tmp) / "tmp"
        root.mkdir()
        scratch.mkdir()
        env, workdir, entrypoint = build_layout(root)
        if override:
            entrypoint = override

        def rewrite(value: str) -> str:
            for container, host in mounts.items():
                if value == container or value.startswith(container + "/"):
                    return host + value[len(container) :]
            if value == "/app" or value.startswith("/app/"):
                return str(root / value.lstrip("/"))
            if value == "/tmp" or value.startswith("/tmp/"):
                return str(scratch) + value[4:]
            return value

        command = [rewrite(a) for a in [*entrypoint, *rest]]
        if command[0] == "python":
            command[0] = sys.executable
        child_env = {"PATH": os.environ.get("PATH", "")}
        child_env.update({k: rewrite(v) for k, v in env.items()})
        child_env.update({n: os.environ[n] for n in env_names if n in os.environ})
        proc = subprocess.run(
            command, cwd=rewrite(workdir), env=child_env, capture_output=True, text=True, encoding="utf-8", errors="replace"
        )
        sys.stdout.write(proc.stdout)
        sys.stderr.write(proc.stderr)
        return proc.returncode


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except ShimError as exc:
        print(f"docker_shim: {exc}", file=sys.stderr)
        sys.exit(125)
