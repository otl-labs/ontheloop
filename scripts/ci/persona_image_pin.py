#!/usr/bin/env python3
"""Verify, write and check the persona image pin (config/persona_image.yaml), T4.32 c2's pin leg.

verify (pr-validate): a PR that changes the pin's digest or source commit passes only when the image is the attested,
labelled build of a reviewed main commit at or after the previous pin. update (session): rewrites the digest and
source_commit lines from the published main tag. check: reports whether the pin is the newest publish.

Every pin PR is opened by an agent session (a GITHUB_TOKEN-opened PR starts no CI) and merges only on the operator's
final-diff word (Decision 246 cl.4: a pin is a deploy). Stdlib plus load_pin and registry_record; every git, registry,
gh or parse error exits 1 (fail closed, never a skip).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from scripts.ci import registry_record as rr
from src.persona_hosts import load_pin
from src.persona_hosts.image_pin import PIN_PATH, ImagePin

PREFIX = "PERSONA-IMAGE-PIN"
SIGNER_WORKFLOW = ".github/workflows/persona-image-publish.yml"
_LINE = re.compile(r"^(image|digest|source_commit):[ \t]*(.*?)[ \t]*(?:#.*)?$", re.MULTILINE)
_REPO_URL = re.compile(r"github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?/?$")
_MISSING = "<missing>"
ALLOWLIST = "  # pragma: allowlist secret"

Runner = Callable[..., "subprocess.CompletedProcess[str]"]
Reader = Callable[..., rr.ImageRecord]


class PinError(RuntimeError):
    """A pin rule failed; the message names the rule."""

    def __init__(self, rule: str, message: str) -> None:
        super().__init__(f"rule {rule}: {message}")
        self.rule = rule


def _run(argv: Sequence[str], **kwargs: Any) -> "subprocess.CompletedProcess[str]":
    return subprocess.run(  # noqa: S603
        list(argv), check=False, capture_output=True, text=True, encoding="utf-8", errors="replace", **kwargs
    )


def _git_ok(args: Sequence[str], cwd: Path, run: Runner) -> "subprocess.CompletedProcess[str]":
    proc = run(["git", *args], cwd=cwd)
    if proc.returncode != 0:
        raise PinError("git", f"git {' '.join(args)} failed: {proc.stderr.strip()[:200]}")
    return proc


def _is_ancestor(older: str, newer: str, cwd: Path, run: Runner) -> bool:
    proc = run(["git", "merge-base", "--is-ancestor", older, newer], cwd=cwd)
    if proc.returncode not in (0, 1):
        raise PinError("git", f"merge-base --is-ancestor {older[:12]} {newer[:12]} failed: {proc.stderr.strip()[:200]}")
    return proc.returncode == 0


def pin_lines(text: str) -> dict[str, str]:
    """The image, digest and source_commit lines only (a minimal read, so a schema change elsewhere still skips)."""
    found = {m.group(1): m.group(2).strip("\"'") for m in _LINE.finditer(text)}
    return {key: found.get(key, _MISSING) for key in ("image", "digest", "source_commit")}


def _base_text(base: str, root: Path, run: Runner) -> str:
    return _git_ok(["show", f"{base}:{PIN_PATH}"], root, run).stdout


def _load_text(text: str) -> ImagePin:
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / PIN_PATH
        target.parent.mkdir(parents=True)
        target.write_text(text, encoding="utf-8")
        return load_pin(Path(tmp))


def check_rules(
    pin: ImagePin,
    base_pin: Optional[ImagePin],
    base: str,
    root: Path,
    *,
    run: Runner = _run,
    reader: Reader = rr.read_image,
) -> None:
    """Rules (a)-(e); each failure is a PinError naming its rule."""
    if pin.digest is None or pin.source_commit is None:
        raise PinError("a", "digest and source_commit must both be set")
    if base_pin is not None and base_pin.digest == pin.digest:
        raise PinError("b", "a pin change must change the digest")
    if not _is_ancestor(pin.source_commit, base, root, run):
        raise PinError("c", f"source_commit {pin.source_commit[:12]} is not an ancestor of the base commit")
    if (
        base_pin is not None
        and base_pin.source_commit
        and not _is_ancestor(base_pin.source_commit, pin.source_commit, root, run)
    ):
        raise PinError("d", "the previous pin's source_commit is not an ancestor of the new one (no rollback pin)")
    record = reader(pin.image, pin.digest, token=None)
    if record.revision != pin.source_commit:
        raise PinError("e", f"image revision label {record.revision!r} is not source_commit {pin.source_commit[:12]}")


def _repository(root: Path, env: dict[str, str], run: Runner) -> str:
    named = env.get("GITHUB_REPOSITORY")
    if named:
        return named
    url = _git_ok(["config", "--get", "remote.origin.url"], root, run).stdout.strip()
    match = _REPO_URL.search(url)
    if not match:
        raise PinError("gh", "cannot derive the repository from git's origin")
    return match.group(1)


def gh_argv(reference: str, repository: str) -> list[str]:
    return [
        "gh", "attestation", "verify", f"oci://{reference}",
        "--repo", repository,
        "--signer-workflow", f"{repository}/{SIGNER_WORKFLOW}",
        "--source-ref", "refs/heads/main",
        "--predicate-type", "https://slsa.dev/provenance/v1",
        "--deny-self-hosted-runners",
        "--format", "json",
    ]  # fmt: skip


def verify(
    base: str,
    root: Path,
    *,
    run: Runner = _run,
    reader: Reader = rr.read_image,
    env: Optional[dict[str, str]] = None,
) -> str:
    """The one line pr-validate prints; PinError (or any read error) means exit 1."""
    env = dict(os.environ) if env is None else env
    head_text = (root / PIN_PATH).read_text(encoding="utf-8")
    base_text = _base_text(base, root, run)
    base_lines, head_lines = pin_lines(base_text), pin_lines(head_text)
    if _MISSING in head_lines.values():
        raise PinError("a", "the pin file's image, digest and source_commit lines must all exist")
    if base_lines["image"] != head_lines["image"]:
        raise PinError("image", "moving the pinned repository needs its own plan")
    if (base_lines["digest"], base_lines["source_commit"]) == (head_lines["digest"], head_lines["source_commit"]):
        return f"{PREFIX} skip reason=unchanged"
    pin = load_pin(root)
    try:
        base_pin = _load_text(base_text)
    except (ValueError, OSError) as exc:
        raise PinError("base", f"the base pin cannot be parsed ({exc}); fail closed") from exc
    if base_pin.digest is not None and pin.digest is None:
        raise PinError("a", "a set pin must not return to null")
    check_rules(pin, base_pin, base, root, run=run, reader=reader)
    assert pin.digest is not None and pin.source_commit is not None  # noqa: S101 (narrowing; check_rules proved it)
    repository = _repository(root, env, run)
    proc = run(gh_argv(pin.reference(), repository), cwd=root)
    if proc.returncode != 0:
        raise PinError("attestation", f"gh attestation verify failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return f"{PREFIX} verified digest={pin.digest} source_commit={pin.source_commit}"


def _newest(pin: ImagePin, reader: Reader) -> rr.ImageRecord:
    record = reader(pin.image, "main", token=None)
    if not record.revision:
        raise PinError("e", "the main tag carries no revision label")
    return record


def _line(key: str, value: str) -> str:
    return f'{key}: "{value}"' + ALLOWLIST if key == "source_commit" else f'{key}: "{value}"'


def update(root: Path, *, run: Runner = _run, reader: Reader = rr.read_image) -> str:
    path = root / PIN_PATH
    text = path.read_text(encoding="utf-8")
    pin = load_pin(root)
    record = _newest(pin, reader)
    if (pin.digest, pin.source_commit) == (record.digest, record.revision):
        return f"{PREFIX} current"
    _git_ok(["fetch", "-q", "origin", "main"], root, run)
    base = _git_ok(["rev-parse", "origin/main"], root, run).stdout.strip()
    base_pin = _load_text(_base_text(base, root, run))
    candidate = ImagePin(pin.image, record.digest, record.revision, pin.dockerfile, pin.files, pin.copy, pin.consumers)
    check_rules(candidate, base_pin, base, root, run=run, reader=reader)
    for key, value in (("digest", record.digest), ("source_commit", record.revision)):
        replacement = _line(key, str(value))
        pattern = re.compile(rf"^{key}:.*$", re.MULTILINE)
        if not pattern.search(text):
            raise PinError("a", f"no top-level {key} line to rewrite")
        text = pattern.sub(lambda _match: replacement, text, count=1)
    path.write_text(text, encoding="utf-8")
    return f"{PREFIX} updated digest={record.digest} source_commit={record.revision}"


def check(root: Path, *, reader: Reader = rr.read_image) -> str:
    pin = load_pin(root)
    record = _newest(pin, reader)
    if pin.digest == record.digest:
        return f"{PREFIX} current"
    return f"{PREFIX} stale pinned={pin.digest or 'null'} newest={record.digest}"


def main(argv: Optional[Sequence[str]] = None, *, root: Optional[Path] = None, **seams: Any) -> int:
    parser = argparse.ArgumentParser(prog="persona_image_pin")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify").add_argument("--base", required=True)
    sub.add_parser("update")
    sub.add_parser("check")
    args = parser.parse_args(argv)
    where = Path.cwd() if root is None else root
    try:
        if args.command == "verify":
            line = verify(args.base, where, **seams)
        elif args.command == "update":
            line = update(where, **seams)
        else:
            line = check(where, **{k: v for k, v in seams.items() if k == "reader"})
    except (PinError, rr.RecordError, ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"{PREFIX} refused: {exc}", file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
