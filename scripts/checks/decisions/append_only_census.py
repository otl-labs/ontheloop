"""Post-hoc census of Decision 177's append-only dialect over main (rec-5266; a report, never a merge gate).

Replays every first-parent commit since ANCHOR that touched the Decision corpus files, comparing each with its first
parent through append_only_rule.compare, so a rewrite of a line added after any pin is seen at the merge that made it.
A finding is cleared only by a ledger row (config/agent/decision_census.yaml) keyed to the merge, the Decision and a
digest of the two block versions, whose disposition annotation (a dated '> **Update (' line naming the merge) has
landed. Exit 0: nothing open and the ledger clean. Exit 1: an open finding or a ledger defect. Exit 2, never a pass:
the census could not complete (anchor not an ancestor, git failure, ledger unreadable) or the self-test failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from scripts.checks import _common
from scripts.checks.decisions import _baseline
from scripts.checks.decisions.append_only_rule import compare, prepare_lines

ANCHOR = "7b67e21d70f733f7137875195a16e71f59822a9c"  # pragma: allowlist secret
_FIXTURE_MERGE = "660d5b54cb841110a1ae375a66f26cd3bf67a50d"  # pragma: allowlist secret
SELF_TEST_FIXTURE: tuple[str, tuple[int, ...]] = (_FIXTURE_MERGE, (65, 210))

_LIVE = "docs/DECISIONS.md"
_ARCHIVE = "docs/DECISIONS_ARCHIVE.md"
_CORPUS = (_LIVE, _ARCHIVE)
_LEDGER_REL = "config/agent/decision_census.yaml"
_PR_RE = re.compile(r"\(#(\d+)\)\s*$")
_UPDATE_PREFIX = "> **Update ("


class CensusError(Exception):
    """The census could not complete; mapped to exit 2."""


@dataclass
class Finding:
    merge: str
    decision: int
    digest: str
    line: Optional[str]
    pr: Optional[int]
    kinds: list[str] = field(default_factory=list)


@dataclass
class Result:
    merges: int = 0
    findings: list[Finding] = field(default_factory=list)
    acknowledged: list[Finding] = field(default_factory=list)
    open: list[Finding] = field(default_factory=list)
    defects: list[str] = field(default_factory=list)
    self_test_ok: bool = False
    head: str = ""
    replayed: list[str] = field(default_factory=list)


def _git(root: Path, args: list[str]) -> str:
    result = _common.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=root)
    if result.returncode != 0:
        raise CensusError(f"git {' '.join(args[:2])} failed: {result.stderr.strip()[:200]}")
    return result.stdout


def _ledger(root: Path) -> list[dict]:
    path = root / _LEDGER_REL
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        rows = data.get("acknowledged") or []
        parsed = [{"merge": str(r["merge"]), "decision": int(r["decision"]), "digest": str(r["digest"])} for r in rows]
    except (yaml.YAMLError, KeyError, TypeError, ValueError, AttributeError) as exc:
        raise CensusError(f"ledger unreadable: {exc}") from exc
    return parsed


def _digest(parent_block: str, merge_block: str) -> str:
    left = "\n".join(prepare_lines(parent_block))
    right = "\n".join(prepare_lines(merge_block))
    return hashlib.sha256(f"{left}\n\0\n{right}".encode("utf-8")).hexdigest()[:16]


def _block(bodies: dict[str, dict[int, str]], number: int) -> str:
    return bodies[_LIVE].get(number) or bodies[_ARCHIVE].get(number) or ""


def _has_annotation(bodies: dict[str, dict[int, str]], number: int, short: str) -> bool:
    for rel in _CORPUS:
        block = bodies[rel].get(number)
        if block and any(ln.startswith(_UPDATE_PREFIX) and short in ln for ln in block.splitlines()):
            return True
    return False


def _commits(root: Path, head: str) -> list[tuple[str, str]]:
    out = _git(root, ["log", "--first-parent", "--reverse", "--format=%H%x09%s", f"{ANCHOR}..{head}", "--", *_CORPUS])
    pairs = []
    for line in out.splitlines():
        sha, _, subject = line.partition("\t")
        pairs.append((sha, subject))
    return pairs


def _replay(root: Path, head: str, rows: list[dict]) -> tuple[Result, set[tuple[str, int]]]:
    commits = _commits(root, head)
    result = Result(merges=len(commits), replayed=[sha for sha, _ in commits])
    position = {sha: i for i, (sha, _) in enumerate(commits)}
    pending = {(r["merge"], r["decision"]) for r in rows if r["merge"] in position}
    satisfied: set[tuple[str, int]] = set()
    prev_sha: Optional[str] = None
    prev_bodies: Optional[dict[str, dict[int, str]]] = None
    for idx, (sha, subject) in enumerate(commits):
        parent = _git(root, ["rev-parse", f"{sha}^"]).strip()
        base = prev_bodies if prev_sha == parent and prev_bodies is not None else _baseline.decision_bodies_at(root, parent)
        cur = _baseline.decision_bodies_at(root, sha)
        if base is None or cur is None:
            raise CensusError(f"cannot read corpus at {sha[:8]} or its parent")
        prev_sha, prev_bodies = sha, cur
        for merge, number in list(pending):
            if position[merge] < idx and _has_annotation(cur, number, merge[:8]):
                satisfied.add((merge, number))
                pending.discard((merge, number))
        by_number: dict[int, Finding] = {}
        pr_match = _PR_RE.search(subject)
        pr = int(pr_match.group(1)) if pr_match else None
        for number, kind, line in compare(base[_LIVE], base[_ARCHIVE], cur[_LIVE], cur[_ARCHIVE]):
            found = by_number.get(number)
            if found is None:
                digest = _digest(_block(base, number), _block(cur, number))
                found = by_number[number] = Finding(sha, number, digest, line, pr)
            if kind not in found.kinds:
                found.kinds.append(kind)
        result.findings.extend(by_number.values())
    return result, satisfied


def census(root: Path, head: str = "HEAD") -> Result:
    if _common.run(
        ["git", "rev-parse", "--verify", "--quiet", f"{head}^{{commit}}"], capture_output=True, cwd=root
    ).returncode:
        raise CensusError(f"head {head} does not resolve to a commit")
    anc = _common.run(["git", "merge-base", "--is-ancestor", ANCHOR, head], capture_output=True, cwd=root).returncode
    if anc != 0:
        raise CensusError("anchor is not an ancestor of head (shallow clone or wrong head)")
    rows = _ledger(root)
    head_sha = _git(root, ["rev-parse", f"{head}^{{commit}}"]).strip()
    result, satisfied = _replay(root, head, rows)
    result.head = head_sha
    fixture_merge, fixture_numbers = SELF_TEST_FIXTURE
    flagged = {f.decision for f in result.findings if f.merge == fixture_merge}
    result.self_test_ok = set(fixture_numbers) <= flagged
    by_key = {(f.merge, f.decision): f for f in result.findings}
    used: set[tuple[str, int, str]] = set()
    for row in rows:
        key = (row["merge"], row["decision"])
        finding = by_key.get(key)
        if finding is None:
            result.defects.append(
                f"row merge={row['merge'][:8]} decision={row['decision']}: matches no finding (stale or never flagged)"
            )
        elif finding.digest != row["digest"]:
            result.defects.append(
                f"row merge={row['merge'][:8]} decision={row['decision']}: stale digest, census now computes {finding.digest}"
            )
        elif key not in satisfied:
            result.defects.append(
                f"row merge={row['merge'][:8]} decision={row['decision']}: disposition annotation never landed"
            )
        else:
            used.add((*key, row["digest"]))
    for finding in result.findings:
        (result.acknowledged if (finding.merge, finding.decision, finding.digest) in used else result.open).append(finding)
    return result


def _finding_line(f: Finding, status: str) -> str:
    pr = f" pr=#{f.pr}" if f.pr is not None else ""
    line = f" line={f.line[:120]!r}" if f.line is not None else ""
    return f"FINDING merge={f.merge[:8]} decision={f.decision} kinds={','.join(f.kinds)}{pr} digest={f.digest} {status}{line}"


def render(result: Result) -> list[str]:
    ack = {id(f) for f in result.acknowledged}
    lines = [_finding_line(f, "acknowledged" if id(f) in ack else "open") for f in result.findings]
    lines += [f"LEDGER_DEFECT {d}" for d in result.defects]
    st = "ok" if result.self_test_ok else "FAIL"
    lines.append(
        f"CENSUS merges={result.merges} findings={len(result.findings)} acknowledged={len(result.acknowledged)} "
        f"open={len(result.open)} self_test={st}"
    )
    return lines


def result_payload(result: Result) -> dict:
    grouped: dict[str, dict] = {}
    for f in result.open:
        entry = grouped.setdefault(f.merge, {"merge": f.merge, "pr": f.pr, "decisions": []})
        entry["decisions"].append({"decision": f.decision, "digest": f.digest, "kinds": list(f.kinds), "line": f.line})
    order = {sha: i for i, sha in enumerate(result.replayed)}
    return {
        "schema": 1,
        "head": result.head,
        "merges": result.merges,
        "replayed": list(result.replayed),
        "acknowledged": len(result.acknowledged),
        "open": sorted(grouped.values(), key=lambda e: order.get(e["merge"], 0)),
        "ledger_defects": list(result.defects),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Decision 177 append-only census over main")
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--summary", default=None)
    parser.add_argument("--result", default=None)
    args = parser.parse_args(argv)
    try:
        result = census(_common.ROOT, args.head)
    except CensusError as exc:
        print(f"CENSUS ERROR: {exc}")
        return 2
    lines = render(result)
    print("\n".join(lines))
    if args.summary:
        with open(args.summary, "a", encoding="utf-8") as fh:
            fh.write("### Decision append-only census\n\n" + "\n".join(f"- `{ln}`" for ln in lines) + "\n")
    if not result.self_test_ok:
        print("CENSUS ERROR: self-test failed, the detector is blind")
        return 2
    if args.result:
        try:
            Path(args.result).write_text(json.dumps(result_payload(result), indent=1) + "\n", encoding="utf-8")
        except OSError as exc:
            print(f"CENSUS ERROR: cannot write result: {exc}")
            return 2
    return 1 if (result.open or result.defects) else 0


if __name__ == "__main__":
    sys.exit(main())
