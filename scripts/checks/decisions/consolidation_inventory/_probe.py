"""probe(root, inv): stub the victims in a detached worktree of HEAD and run what reads them, before and after."""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from scripts import decisions_md
from scripts.checks import _process_group
from scripts.checks.decisions.consolidation_inventory._model import (
    STUB_TEMPLATE,
    STUB_TEMPLATE_ID,
    Inventory,
    with_probe,
)
from scripts.checks.decisions.validate_supersession_annotations import extract_supersession_edges

NODE_TIMEOUT_S = 900
GIT_TIMEOUT_S = 120
FIXED_NOTE = (
    "registered scripts.validate checks are not probed (scripts.validate has no single-check mode); "
    "the structured kinds carry the effects those checks enforce, each by rule_id"
)
_RG_STUB = '#!/bin/sh\necho "rg: command not found (stubbed: the CI runner has no ripgrep)" >&2\nexit 127\n'
_RERUN_MODULE = "scripts.checks.verification.validate_graduated_rerun"
_SEPARATOR = re.compile(r"\n---\s*\Z")
_DATE = re.compile(r"\*\*Date:\*\*\s*(\S+)")
_NOT_STUBBED = "victim has no live section in docs/DECISIONS.md (archive-only or already a compacted stub)"


class ProbeError(Exception):
    """A worktree or subprocess failure: every hit stays not_run and the CLI exits 1."""


def _git(cwd: Path, *args: str) -> str:
    try:
        done = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProbeError(f"git {' '.join(args)}: {exc}") from exc
    if done.returncode != 0:
        raise ProbeError(f"git {' '.join(args)} failed: {(done.stderr or done.stdout).strip()[:200]}")
    return done.stdout


def _run_group(cmd: list[str], cwd: Path, env: dict[str, str], timeout: float) -> tuple[bool, str, str, int | None]:
    """Run cmd in its own session; (timed_out, stdout, stderr, returncode). A timeout kills and reaps the process group."""
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        out, err = _process_group.kill_and_reap(proc)
        return True, out or "", err or "", None
    return False, out or "", err or "", proc.returncode


def _env(extra_path: str | None = None) -> dict[str, str]:
    env = {**os.environ, "OTL_VENV_PYTHON": sys.executable}
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    return env


def _amenders(wt: Path, victims: tuple[int, ...]) -> dict[int, list[int]]:
    """Each victim's superseders: the numbers a real stub must name (point_map, validate_supersession_annotations)."""
    found: dict[int, set[int]] = {v: set() for v in victims}
    for superseder, victim, _ in extract_supersession_edges(wt):
        if victim in found:
            found[victim].add(superseder)
    return {v: sorted(found[v]) for v in victims}


def _stub_victims(wt: Path, victims: tuple[int, ...]) -> tuple[set[int], int]:
    """Replace each victim's live section with STUB_TEMPLATE and add a placeholder successor; (stubbed, successor)."""
    amenders = _amenders(wt, victims)
    path = wt / "docs" / "DECISIONS.md"
    text = path.read_text(encoding="utf-8")
    paths = [p for p in (path, wt / "docs" / "DECISIONS_ARCHIVE.md") if p.exists()]
    successor = max(decisions_md.decision_header_numbers(paths) or {0}) + 1
    sections = decisions_md.iter_decision_sections(text)
    stubbed: set[int] = set()
    pieces: list[str] = []
    cursor = 0
    for match, block in sections:
        number = int(match.group(1))
        pieces.append(text[cursor : match.start()])
        cursor = match.start() + len(block)
        if number in victims and not decisions_md.is_compacted_stub(block):
            date = _DATE.search(block)
            envelope = decisions_md.extract_entry_envelope(block) or {}
            when = date.group(1) if date else str(envelope.get("decided_date", "2026-01-01"))
            header = block.split("\n", 1)[0]
            separator = "---\n\n" if _SEPARATOR.search(block) else ""
            named = ", ".join(f"Decision {n}" for n in amenders[number])
            clause = f" and names its amenders: {named}" if named else ""
            pieces.append(STUB_TEMPLATE.format(header=header, date=when, successor=successor, amenders=clause) + separator)
            stubbed.add(number)
        else:
            pieces.append(block)
    pieces.append(text[cursor:])
    body = "".join(pieces)
    placeholder = (
        f"## Decision {successor}: Consolidation probe placeholder (supersedes "
        + ", ".join(f"Decisions {v}" for v in sorted(stubbed))
        + ") (Decided)\n\n```yaml\n"
        f'number: {successor}\nstatus: Decided\ndecided_date: "2026-01-01"\n'
        "kind:\n  value: direction\n  justification: probe placeholder\n"
        f"supersedes: {sorted(stubbed)}\n```\n\n"
        f"**Decision:** Placeholder successor naming Decisions {', '.join(str(v) for v in sorted(stubbed))}.\n\n---\n\n"
    )
    first = decisions_md.iter_decision_sections(body)
    head = first[0][0].start() if first else 0
    path.write_text(body[:head] + placeholder + body[head:], encoding="utf-8")
    return stubbed, successor


def _regenerate_index(wt: Path, info: dict[str, Any]) -> None:
    if not (wt / "scripts" / "decisions_index.py").exists():
        info["notes"].append("worktree has no scripts/decisions_index.py: index regeneration skipped")
        return
    timed_out, _, err, rc = _run_group([sys.executable, "-m", "scripts.decisions_index", "--write"], wt, _env(), GIT_TIMEOUT_S)
    if timed_out:
        raise ProbeError("scripts.decisions_index --write timed out")
    if rc != 0:
        raise ProbeError(f"scripts.decisions_index --write failed: {err.strip()[:200]}")


def _node_key(classname: str, name: str, wt: Path) -> str:
    parts = classname.split(".") if classname else []
    base = name.split("[", 1)[0]
    for i in range(len(parts), 0, -1):
        rel = "/".join(parts[:i]) + ".py"
        if (wt / rel).is_file():
            rest = parts[i:]
            return "::".join([rel, *rest, base])
    return f"{classname}::{base}"


def _junit(wt: Path, targets: list[str], scratch: Path, tag: str) -> dict[str, bool]:
    """Run pytest on the targets (files or node ids): node key -> red. A missing or unparseable report is a ProbeError."""
    if not targets:
        return {}
    report = scratch / f"{tag}.xml"
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        *targets,
        "-q",
        f"--junitxml={report}",
        "-o",
        f"cache_dir={scratch / (tag + '-cache')}",
    ]
    timed_out, _, _, _ = _run_group(cmd, wt, _env(), NODE_TIMEOUT_S)
    if timed_out:
        raise ProbeError(f"the test-node pytest run timed out after {NODE_TIMEOUT_S} s")
    try:
        tree = ET.parse(report)
    except (OSError, ET.ParseError) as exc:
        raise ProbeError(f"the {tag} pytest run left no readable junit report: {exc}") from exc
    seen: dict[str, bool] = {}
    for case in tree.iter("testcase"):
        key = _node_key(case.get("classname", ""), case.get("name", ""), wt)
        red = case.find("failure") is not None or case.find("error") is not None
        seen[key] = seen.get(key, False) or red
    return seen


def _run_nodes(wt: Path, nodes: list[str], scratch: Path, tag: str) -> set[str]:
    """The set of node ids that are red (any parametrized case failing, or absent from the report)."""
    seen = _junit(wt, nodes, scratch, tag)
    return {n for n in nodes if seen.get(n, True)}


def _run_record(wt: Path, check_id: str, stub_dir: str) -> bool:
    from scripts.checks.verification import validate_graduated_rerun as rerun

    cmd = [sys.executable, "-m", _RERUN_MODULE, "--check-id", check_id, "--root", str(wt)]
    timed_out, out, _, _ = _run_group(cmd, rerun.HEAD_ROOT, _env(stub_dir), rerun.RECORD_TIMEOUT_S)
    if timed_out:
        raise ProbeError(f"record {check_id} timed out after {rerun.RECORD_TIMEOUT_S} s")
    try:
        return bool(json.loads(out.strip().splitlines()[-1])["passed"])
    except (IndexError, ValueError, KeyError) as exc:
        raise ProbeError(f"record {check_id} printed no parseable verdict") from exc


def _run_records(wt: Path, ids: list[str], stub_dir: str) -> set[str]:
    """The record ids that are red."""
    if not ids:
        return set()
    with ThreadPoolExecutor(max_workers=min(4, len(ids))) as pool:
        results = list(pool.map(lambda c: _run_record(wt, c, stub_dir), ids))
    return {c for c, ok in zip(ids, results, strict=True) if not ok}


def _candidates(inv: Inventory) -> tuple[list[str], list[str]]:
    nodes = sorted({h.locator for h in inv.hits if h.kind == "test_node"})
    records = sorted({h.locator.rsplit(":", 1)[1] for h in inv.hits if h.kind == "graduated_record"})
    return nodes, records


def _label(red_before: bool, red_after: bool) -> str:
    return "red_before" if red_before else ("breaks" if red_after else "survives")


def probe(root: Path, inv: Inventory) -> Inventory:
    """Fills each test_node and graduated_record hit's probe state; a failure leaves every hit not_run (probe_failed set)."""
    started = time.monotonic()
    info: dict[str, Any] = {
        "stub_template": STUB_TEMPLATE_ID,
        "head": "",
        "dirty": False,
        "note": FIXED_NOTE,
        "notes": [],
        "reasons": {},
        "extra_breaks": [],
    }
    inv.probe_info = info
    scratch = Path(tempfile.mkdtemp(prefix="consolidation-probe-"))
    wt = scratch / "wt"
    added = False
    stub_dir = ""
    try:
        info["head"] = _git(root, "rev-parse", "HEAD").strip()
        info["dirty"] = bool(_git(root, "status", "--porcelain").strip())
        if info["dirty"]:
            info["notes"].append("git status is not clean: the probe reads HEAD, not the working tree")
        _git(root, "worktree", "add", "--detach", str(wt), "HEAD")
        added = True
        stub_dir = tempfile.mkdtemp(prefix="rg-stub-")
        rg = Path(stub_dir) / "rg"
        rg.write_text(_RG_STUB, encoding="utf-8")
        rg.chmod(0o755)
        nodes, records = _candidates(inv)
        files = sorted({n.split("::", 1)[0] for n in nodes})
        seen_before = _junit(wt, files, scratch, "before")
        red_nodes_before = {n for n in nodes if seen_before.get(n, True)}
        red_records_before = _run_records(wt, records, stub_dir)
        stubbed, _ = _stub_victims(wt, inv.victims)
        _regenerate_index(wt, info)
        seen_after = _junit(wt, files, scratch, "after")
        red_nodes_after = {n for n in nodes if seen_after.get(n, True)}
        fresh = sorted(n for n in red_nodes_after if n not in red_nodes_before)
        extras = sorted(k for k, red in seen_after.items() if red and not seen_before.get(k, False) and k not in nodes)
        red_again = _run_nodes(wt, fresh + extras, scratch, "again")
        red_nodes_after -= set(fresh) - red_again
        info["extra_breaks"] = [k for k in extras if k in red_again]
        red_records_after = _run_records(wt, records, stub_dir)
        again = sorted(r for r in red_records_after if r not in red_records_before)
        red_records_after = (red_records_after - set(again)) | _run_records(wt, again, stub_dir)
    except (ProbeError, OSError, ValueError, KeyError) as exc:
        inv.probe_failed = str(exc)
        info["failed"] = str(exc)
        info["notes"].append("probe failed: every hit stays not_run")
        return inv
    finally:
        if added:
            with contextlib.suppress(ProbeError):
                _git(root, "worktree", "remove", "--force", str(wt))
            with contextlib.suppress(ProbeError):
                _git(root, "worktree", "prune")
        shutil.rmtree(scratch, ignore_errors=True)
        if stub_dir:
            shutil.rmtree(stub_dir, ignore_errors=True)
        print(f"probe elapsed {time.monotonic() - started:.1f} s", file=sys.stderr)
    updated = []
    for hit in inv.hits:
        if hit.victim not in stubbed:
            if hit.kind in {"test_node", "graduated_record"}:
                info["reasons"][hit.locator] = _NOT_STUBBED
            updated.append(hit)
        elif hit.kind == "test_node":
            updated.append(with_probe(hit, _label(hit.locator in red_nodes_before, hit.locator in red_nodes_after)))
        elif hit.kind == "graduated_record":
            check_id = hit.locator.rsplit(":", 1)[1]
            updated.append(with_probe(hit, _label(check_id in red_records_before, check_id in red_records_after)))
        else:
            updated.append(hit)
    inv.hits = updated
    return inv


__all__ = ["FIXED_NOTE", "ProbeError", "probe"]
