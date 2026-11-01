"""Licence-artefact consistency and class-(a) slug hygiene (Decision 171).

Two invariants, one check:

1. **Licence-file consistency.** LICENSE is parameterised BUSL-1.1 with every Parameter
   populated and no template placeholder surviving; its Licensed Work parameter names the
   ratified mark (Decision 198). LICENSE-APACHE holds the preserved Apache-2.0 text.
   LICENSING.md states the forward-only boundary, names a real 40-hex commit, and carries a
   single-line continuity note naming both the ratified mark and the former one (Decision 198).
   README agrees with all three.

2. **No class-(a) old-slug survivor.** The repository slug moved
   benjamin-blake/agent-platform -> benjamin-blake/theseus, but roughly twenty BARE
   `agent-platform` occurrences are AWS resource names (the `agent-platform-data-lake`
   bucket, `agent-platform-*` IAM roles, `/agent-platform/*` SSM paths, Glue database,
   Lambda names) that MUST NOT be rewritten. A repo-wide grep cannot express that, so
   this check carries an explicit path -> PATTERN map.

WHY THE PATTERN IS PER-PATH AND NOT ONE SHARED REGEX. The three classes are genuinely
different shapes, and collapsing them yields either a vacuous pass or a permanent red:

  - `escalate.py` needs FULL-SLUG matching: line 39 is class-(a) (`repos/<owner>/<slug>`)
    while lines 43 and 47 carry `s3://agent-platform-data-lake` in the SAME file. A
    bare-token pattern would flag the bucket forever.
  - `approvals.py` needs BARE-TOKEN matching: its defaults are `repo: str = "agent-platform"`
    with no owner prefix. A full-slug pattern would never match, passing vacuously.
  - `bin/setup-cloud-env.sh` needs a CONTEXT-QUALIFIED pattern: line 61's PAT scope is
    class-(a), but line 38's `cd /home/user/agent-platform` is an on-disk checkout path
    (rename-independent) and line 205's `s3://agent-platform-data-lake` is class-(b). A
    bare-token pattern matches all three, because `-` is a word boundary.

Decision 170: this check declares examined()/skipped() rather than relying on the frozen
check-accounting baseline. A pass with examined(0) is a VACUOUS pass -- the path map is
wrong or empty -- which is exactly what the declarative channel exists to make visible.
"""

from __future__ import annotations

import re
from pathlib import Path

from scripts.checks import registry

_REPO_ROOT = Path(__file__).resolve().parents[3]

_LICENSE = "LICENSE"
_LICENSE_APACHE = "LICENSE-APACHE"
_LICENSING = "LICENSING.md"
_README = "README.md"

# Class-(a) paths -> the pattern that identifies an old-slug REPOSITORY reference there.
# Each pattern must NOT match the class-(b) AWS names that legitimately live in the same file.
_CLASS_A_PATTERNS: dict[str, re.Pattern[str]] = {
    # Full-slug: owner-prefixed repository references only.
    "scripts/convergence_health/escalate.py": re.compile(r"benjamin-blake/agent-platform"),
    "scripts/session/github_readiness.py": re.compile(r"benjamin-blake/agent-platform"),
    "scripts/checks/misc/validate_ghas_probe.py": re.compile(r"benjamin-blake/agent-platform"),
    ".github/workflows/ghas-probe.yml": re.compile(r"benjamin-blake/agent-platform"),
    "docs/ROADMAP-PLATFORM.yaml": re.compile(r"benjamin-blake/agent-platform"),
    "docs/ROADMAP-PLATFORM-ARCHIVE.yaml": re.compile(r"benjamin-blake/agent-platform"),
    # Bare token: a quoted repo NAME with no owner prefix, as a parameter default.
    "scripts/convergence_health/approvals.py": re.compile(r"""repo:\s*str\s*=\s*["']agent-platform["']"""),
    # Context-qualified: the PAT repo-scope comment only. Must not match the checkout path
    # (`cd /home/user/agent-platform`) or the data-lake bucket (`s3://agent-platform-data-lake`).
    "bin/setup-cloud-env.sh": re.compile(r"single-repo\s*\(agent-platform\)"),
}

_BUSL_REQUIRED = (
    (re.compile(r"^Business Source License 1\.1", re.M), "BUSL-1.1 header"),
    (re.compile(r"^Licensor: +Benjamin Blake\s*$", re.M), "Licensor parameter"),
    (re.compile(r"^Licensed Work: +\S", re.M), "Licensed Work parameter"),
    (re.compile(r"^Change Date: +20\d{2}-\d{2}-\d{2}\s*$", re.M), "Change Date parameter"),
    (re.compile(r"^Change License: +\S", re.M), "Change License parameter"),
    (re.compile(r"^Additional Use Grant: +\S", re.M), "Additional Use Grant parameter"),
)

_PLACEHOLDER = re.compile(r"\[[^\]\n]*\]")
_SHA40 = re.compile(r"\b[0-9a-f]{40}\b")

# Licensed-work brand invariant (Decision 198). Two predicates, both read off text the licence
# leg already loaded -- neither re-reads a file, so this does not widen the examined() count.
_RATIFIED_MARK = "On The Loop"
_FORMER_MARK = "Theseus"
_LICENSED_WORK_MARK = re.compile(r"^Licensed Work: +" + re.escape(_RATIFIED_MARK) + r"\b", re.M)


def _read(rel: str) -> str | None:
    path = _REPO_ROOT / rel
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def _check_licence_files(failed: list[str]) -> int:
    """Assert the licence artefacts are mutually consistent. Returns files examined."""
    examined = 0

    licence = _read(_LICENSE)
    if licence is None:
        failed.append(f"licence consistency: {_LICENSE} is missing")
    else:
        examined += 1
        for pattern, label in _BUSL_REQUIRED:
            if not pattern.search(licence):
                failed.append(f"licence consistency: {_LICENSE} lacks a populated {label}")
        # The Additional Use Grant must carry its non-limitation clause INSIDE the parameter
        # value. A clause elsewhere in the file does not qualify the parameter, and an
        # enumerated grant without it invites an expressio unius reading that NARROWS the
        # baseline the Terms already grant.
        grant = re.search(r"^Additional Use Grant:(.*?)^Change Date:", licence, re.M | re.S)
        if grant is None:
            failed.append(f"licence consistency: {_LICENSE} has no Additional Use Grant block")
        elif not re.search(r"not exhaustive|without limitation|not limitation", grant.group(1), re.I):
            failed.append(
                f"licence consistency: {_LICENSE}'s Additional Use Grant lacks its non-limitation "
                "clause -- an enumerated grant without it can be read as exhaustive, narrowing the "
                "rights the licence body already grants"
            )
        # No unfilled bracket placeholder anywhere in the Parameters block. The canonical
        # template's alternative-licensing slot ("please visit: [ ]") sits AFTER Change License,
        # so the scanned range runs to the section rule, not to Change Date.
        params = re.search(r"^Licensor:.*?^-{10,}", licence, re.M | re.S)
        if params is not None and _PLACEHOLDER.search(params.group(0)):
            failed.append(
                f"licence consistency: {_LICENSE}'s Parameters block still contains a bracket "
                "placeholder -- an unfilled parameter makes the grant defective"
            )
        # Licensed-work brand invariant, predicate 1: the Licensed Work parameter names the
        # ratified mark, not merely SOME non-empty value (the _BUSL_REQUIRED check above).
        if not _LICENSED_WORK_MARK.search(licence):
            failed.append(
                f"licence consistency: {_LICENSE}'s Licensed Work parameter does not name the "
                f"ratified mark ({_RATIFIED_MARK!r})"
            )

    apache = _read(_LICENSE_APACHE)
    if apache is None:
        failed.append(f"licence consistency: {_LICENSE_APACHE} is missing")
    else:
        examined += 1
        if "Apache License" not in apache or "Version 2.0" not in apache:
            failed.append(
                f"licence consistency: {_LICENSE_APACHE} does not contain the Apache-2.0 text -- "
                "the forward-only claim in LICENSING.md is not backed by the actual prior licence"
            )

    licensing = _read(_LICENSING)
    if licensing is None:
        failed.append(f"licence consistency: {_LICENSING} is missing")
    else:
        examined += 1
        if not _SHA40.search(licensing):
            failed.append(
                f"licence consistency: {_LICENSING} names no 40-hex boundary commit -- "
                "the forward-only boundary has no durable referent"
            )
        if "as published by the Licensor" not in licensing:
            failed.append(
                f"licence consistency: {_LICENSING}'s self-verifying rule is not provenance-scoped "
                "-- unscoped, a post-flip licensee could revert LICENSE and self-certify Apache-2.0"
            )
        # Licensed-work brand invariant, predicate 2: a SINGLE LINE names both the ratified mark
        # and the former one, so a holder of a pre-rename copy can identify the work their grant
        # covers. Whole-file co-occurrence is NOT sufficient -- see the docstring note below.
        if not any(_RATIFIED_MARK in line and _FORMER_MARK in line for line in licensing.splitlines()):
            failed.append(
                f"licence consistency: {_LICENSING} carries no continuity note naming both "
                f"{_RATIFIED_MARK!r} and {_FORMER_MARK!r} on a single line -- a holder of a "
                "pre-rename copy could not identify the work their grant covers"
            )

    readme = _read(_README)
    if readme is None:
        failed.append(f"licence consistency: {_README} is missing")
    else:
        examined += 1
        if "Business Source License" not in readme:
            failed.append(f"licence consistency: {_README} does not state the BUSL-1.1 licence")
        if _LICENSING not in readme:
            failed.append(f"licence consistency: {_README} does not link {_LICENSING}")

    return examined


def _check_class_a_slugs(failed: list[str]) -> tuple[int, list[str]]:
    """Assert no class-(a) path carries an old-slug repository reference.

    Returns (files examined, paths that were absent). An absent path is reported so a
    silently-dropped entry cannot masquerade as a pass.
    """
    examined = 0
    missing: list[str] = []
    for rel, pattern in _CLASS_A_PATTERNS.items():
        text = _read(rel)
        if text is None:
            missing.append(rel)
            continue
        examined += 1
        hit = pattern.search(text)
        if hit is not None:
            line = text[: hit.start()].count("\n") + 1
            failed.append(
                f"licence consistency: {rel}:{line} still references the old repository slug "
                f"({hit.group(0)!r}) -- class-(a) references move with the rename"
            )
    return examined, missing


@registry.register(name="validate_licence_consistency")
def validate_licence_consistency(failed: list[str]) -> None:
    print("\n=== Licence consistency + class-(a) slug hygiene (Decision 171) ===")

    licence_examined = _check_licence_files(failed)
    slug_examined, missing = _check_class_a_slugs(failed)

    for rel in missing:
        failed.append(
            f"licence consistency: class-(a) path {rel} does not exist -- the path map is stale; "
            "a dropped entry is an unguarded surface, not a pass"
        )

    total = licence_examined + slug_examined
    registry.examined(total, unit="licence_and_class_a_files")
    print(
        f"  examined {licence_examined} licence artefact(s) and {slug_examined} "
        f"class-(a) path(s) of {len(_CLASS_A_PATTERNS)} mapped"
    )
    if not failed:
        print("  PASS: licence artefacts consistent; no class-(a) old-slug survivor.")
