"""The persona image name and digest live in one file (T4.2 c13 second half; T4.32 c2).

config/persona_image.yaml is the only tracked file (besides docs/plans/**, which describe the image) that may hold an
image reference. This check also keeps the image's build honest: a digest-pinned base, COPY sources and build-context
allow-list equal to the pin's build.copy plus the lock, built consumers that read the pin, pending consumers owned by
an open roadmap criterion, and Dependabot watching the image's lock and base. It never compares the image lock with
requirements-dev.txt: the lock is the image's own and the conformance suite tests each bump.

Decision 170: calls registry.examined() on every reachable exit path.
"""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from typing import Any

import yaml

from scripts.checks import _common, registry
from src.persona_hosts.image_pin import ImagePin, load_pin

_PREFIX = "persona-image-digest"
_LOCK = "docker/persona/requirements-persona.txt"
_ROADMAP = "docs/ROADMAP-PLATFORM.yaml"
_DEPENDABOT = ".github/dependabot.yml"
_FROM_DIGEST = re.compile(r"^FROM\s+\S+@sha256:[0-9a-f]{64}(\s|$)", re.M)
_COPY = re.compile(r"^COPY\s+(?:--\S+\s+)*(\S+)\s+(\S+)\s*$", re.M)
_RULE_COUNT = 6


def _reference_hits(root: Path, pin: ImagePin) -> list[str]:
    proc = subprocess.run(
        ["git", "grep", "-n", "-I", "-F", "-e", f"{pin.image}:", "-e", f"{pin.image}@", "--",
         ".", ":(exclude)config/persona_image.yaml", ":(exclude)docs/plans"],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )  # fmt: skip
    if proc.returncode not in (0, 1):
        return [f"git grep for the image reference failed: {proc.stderr.strip()[:200]}"]
    return [
        f"{line.split(':', 2)[0]}: holds an image reference; read it through load_pin" for line in proc.stdout.splitlines()
    ]


def _calls_load_pin(path: Path) -> bool:
    for py in [path] if path.is_file() else sorted(path.rglob("*.py")):
        try:
            tree = ast.parse(py.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                if (isinstance(fn, ast.Name) and fn.id == "load_pin") or (
                    isinstance(fn, ast.Attribute) and fn.attr == "load_pin"
                ):
                    return True
    return False


def _open_criterion(root: Path, owning_item: str) -> bool:
    item_id, _, crit = owning_item.partition(":")
    try:
        data = yaml.safe_load((root / _ROADMAP).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return False
    for item in data.get("tier_items", []):
        if item.get("id") == item_id:
            return any(c.get("id") == crit and c.get("status") == "open" for c in item.get("exit_criteria") or [])
    return False


def _consumer_findings(root: Path, pin: ImagePin) -> list[str]:
    out: list[str] = []
    for name, row in pin.consumers:
        if row["status"] == "built":
            location = root / row["location"]
            if not location.exists():
                out.append(f"consumer {name}: location {row['location']} does not exist")
            elif not _calls_load_pin(location):
                out.append(f"consumer {name}: no .py file under {row['location']} calls load_pin")
        elif not _open_criterion(root, row["owning_item"]):
            out.append(f"consumer {name}: owning_item {row['owning_item']} is not an open roadmap criterion")
    return out


def _dockerfile_findings(root: Path, pin: ImagePin) -> list[str]:
    out: list[str] = []
    dockerfile = root / pin.dockerfile
    try:
        text = dockerfile.read_text(encoding="utf-8")
    except OSError:
        return [f"{pin.dockerfile} is unreadable"]
    if not _FROM_DIGEST.search(text):
        out.append(f"{pin.dockerfile}: FROM is not pinned by digest")
    expected = set(pin.copy) | {_LOCK}
    copied = {m.group(1) for m in _COPY.finditer(text)}
    if copied != expected:
        out += [
            f"{pin.dockerfile}: COPY of {p} is not in build.copy; add it to config/persona_image.yaml"
            for p in sorted(copied - expected)
        ]
        out += [f"{pin.dockerfile}: build.copy line {p} is never copied" for p in sorted(expected - copied)]
    ignore = root / f"{pin.dockerfile}.dockerignore"
    try:
        lines = [ln.strip() for ln in ignore.read_text(encoding="utf-8").splitlines()]
    except OSError:
        return [*out, f"{ignore.relative_to(root)} is unreadable"]
    allowed = {ln[1:] for ln in lines if ln.startswith("!")}
    if allowed != expected:
        out += [f"{ignore.name}: allow-list lacks {p}; add !{p}" for p in sorted(expected - allowed)]
        out += [f"{ignore.name}: allow-list holds {p}, which is not a build.copy line" for p in sorted(allowed - expected)]
    return out


def _dependabot_findings(root: Path, pin: ImagePin) -> list[str]:
    directory = "/" + Path(pin.dockerfile).parent.as_posix()
    try:
        config: Any = yaml.safe_load((root / _DEPENDABOT).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return [f"{_DEPENDABOT} is unreadable"]
    have = {(u.get("package-ecosystem"), u.get("directory")) for u in (config or {}).get("updates", []) if isinstance(u, dict)}
    return [f"{_DEPENDABOT}: no {eco} update for {directory}" for eco in ("pip", "docker") if (eco, directory) not in have]


@registry.register("validate_persona_image_digest", owner="platform")
def validate_persona_image_digest(failed: list[str]) -> None:
    """One image file, a digest-pinned base, and a build that matches the pin. See module docstring."""
    print("\n=== persona image digest guard ===")
    root = _common.ROOT
    try:
        pin = load_pin(root)
    except ValueError as exc:
        print(f"  FAIL: {exc}")
        failed.append(f"{_PREFIX}: {exc}")
        registry.examined(1, unit="image_pin_rules")
        return
    found = [
        *_reference_hits(root, pin),
        *_consumer_findings(root, pin),
        *_dockerfile_findings(root, pin),
        *_dependabot_findings(root, pin),
    ]
    for line in found:
        print(f"  FAIL: {line}")
        failed.append(f"{_PREFIX}: {line}")
    if not found:
        print("  PASS: the image pin, Dockerfile, build context and Dependabot agree")
    registry.examined(_RULE_COUNT, unit="image_pin_rules")
