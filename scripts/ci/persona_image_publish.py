#!/usr/bin/env python3
"""The persona image publish job's two steps (PLAN-persona-image-channel; T4.32 c2).

`build` builds the image from the checked-out gate sha with the revision and licenses labels and emits its id.
`push` tags and pushes it as the package's `main` tag, then proves the registry holds that exact image as a single
manifest carrying the revision label before it writes the job outputs the attest job consumes. The registry token goes
to docker on stdin, never argv; every registry read sends its bearer as an unredirected header (registry_record).
Stdlib plus yaml at import (the build step runs before any dev dependency is installed).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.ci import registry_record as rr
from src.persona_hosts.image_pin import load_pin
from src.persona_hosts.local_container.build import BuildOptions, build_image

ROOT = Path(__file__).resolve().parents[2]
LICENSE = "BUSL-1.1"
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")


class PublishError(RuntimeError):
    """A publish step failed; nothing is written to the job outputs."""


def _docker(args: list[str], runner: Callable[..., Any], stdin: Optional[str] = None) -> str:
    proc = runner(["docker", *args], input=stdin, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise PublishError(f"docker {args[0]} failed ({proc.returncode}): {(proc.stderr or '').strip()[-500:]}")
    return (proc.stdout or "").strip()


def _write(env_name: str, lines: list[str]) -> None:
    target = os.environ.get(env_name)
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.writelines(f"{line}\n" for line in lines)


def build(sha: str, root: Path = ROOT, builder: Callable[..., str] = build_image) -> str:
    if not _SHA.match(sha):
        raise PublishError(f"--sha must be 40 hex: {sha!r}")
    labels = {"org.opencontainers.image.revision": sha, "org.opencontainers.image.licenses": LICENSE}
    image_id = builder(root, BuildOptions(labels=labels))
    if not _IMAGE_ID.match(image_id):
        raise PublishError(f"build returned an unexpected image id: {image_id!r}")
    _write("GITHUB_OUTPUT", [f"image_id={image_id}"])
    print(f"built {image_id}")
    return image_id


def _pushed_digest(image: str, tag_ref: str, runner: Callable[..., Any]) -> str:
    raw = _docker(["inspect", "--format", "{{json .RepoDigests}}", tag_ref], runner)
    try:
        digests = json.loads(raw)
    except ValueError as exc:
        raise PublishError(f"docker inspect RepoDigests is not JSON: {exc}") from exc
    for entry in digests if isinstance(digests, list) else []:
        name, _, digest = str(entry).partition("@")
        if name == image and re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
            return digest
    raise PublishError(f"no RepoDigest for {image} after the push")


def push(
    image_id: str,
    sha: str,
    token: str,
    *,
    root: Path = ROOT,
    runner: Callable[..., Any] = subprocess.run,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> tuple[str, str]:
    if not _IMAGE_ID.match(image_id) or not _SHA.match(sha):
        raise PublishError("--image-id must be sha256:<64 hex> and --sha 40 hex")
    if not token:
        raise PublishError("GITHUB_TOKEN is not set")
    image = load_pin(root).image
    tag_ref = f"{image}:main"
    actor = os.environ.get("GITHUB_ACTOR") or "token"
    _docker(["login", rr.REGISTRY, "-u", actor, "--password-stdin"], runner, stdin=token)
    try:
        _docker(["tag", image_id, tag_ref], runner)
        _docker(["push", tag_ref], runner)
        digest = _pushed_digest(image, tag_ref, runner)
        try:
            bearer = rr.pull_token(image, token, opener)
            manifest = rr.fetch_manifest(image, digest, bearer, opener)
            config_digest = manifest["config"]["digest"]
            if image_id not in (config_digest, digest):
                raise PublishError(f"pushed manifest {digest} (config {config_digest}) is not the tested image {image_id}")
            label = rr.revision_label(rr.fetch_config(image, config_digest, bearer, opener))
        except rr.RecordError as exc:
            raise PublishError(f"pushed image could not be verified: {exc}") from exc
        if label != sha:
            raise PublishError(f"pushed revision label {label!r} != source commit {sha}")
    finally:
        runner(["docker", "logout", rr.REGISTRY], capture_output=True, text=True, encoding="utf-8", errors="replace")
    _write("GITHUB_OUTPUT", [f"image={image}", f"digest={digest}"])
    _write(
        "GITHUB_STEP_SUMMARY",
        [
            "## Persona image published",
            f"- image: `{image}@{digest}`",
            f"- source commit: `{sha}`",
            "Pin lines for the pin PR (config/persona_image.yaml):",
            "```",
            f"digest: {digest}",
            f"source_commit: {sha}",
            "```",
        ],
    )
    print(f"PERSONA-IMAGE-PUBLISHED digest={digest} source_commit={sha}")
    return image, digest


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="persona_image_publish")
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sha", required=True)
    p = sub.add_parser("push")
    p.add_argument("--image-id", required=True)
    p.add_argument("--sha", required=True)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "build":
            build(args.sha)
        else:
            push(args.image_id, args.sha, os.environ.get("GITHUB_TOKEN", ""))
    except (PublishError, RuntimeError, ValueError) as exc:
        print(f"::error::PERSONA-IMAGE-PUBLISH {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
