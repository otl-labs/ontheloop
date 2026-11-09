"""Standing re-run of graduated verification records, full tier only (rec-4831).

Every live record is a standing regression guard. Records the test suite already runs (test_selector, and
command_exit_zero over exactly [bin/venv-python|python, -m, pytest, ...]) are SUITE-COVERED: only their pinned
node ids are resolved statically, unless an argument can select an integration-marked test (the full tier's
unit run passes -m "not integration"), in which case the record is RUN. RUN records execute in a bounded pool;
command-slot records run in a child process under a wall-clock limit whose process group is killed on expiry.
A non-PASS record is re-run once, alone; red only if that also fails (otherwise reported load-sensitive).
Red records not on known_red.yaml fail; the list is strict (a listed record that passes, is not live or is
duplicated fails). The shrink-only leg lives in validate_known_red_shrinks (fast tier).
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import yaml

from scripts.checks import _common, registry
from scripts.checks.verification import _graduated_rerun_nodes as nodes
from scripts.checks.verification.validate_known_red_shrinks import KNOWN_RED_REL
from scripts.verification_graduation import GraduationError, load_entries, materialize_check_in_tree

RECORD_TIMEOUT_S = 300
HEAD_ROOT = Path(__file__).resolve().parents[3]
_CHILD_SLOTS = frozenset({"command_exit_zero", "command_output_matches", "metric_under_threshold", "test_selector"})
_MODULE = "scripts.checks.verification.validate_graduated_rerun"
_PYTEST_HEADS = ("bin/venv-python", "python")
_RG_STUB = '#!/bin/sh\necho "rg: command not found (stubbed: the CI runner has no ripgrep)" >&2\nexit 127\n'


@dataclass(frozen=True)
class Outcome:
    check_id: str
    guard_target: str
    red: bool
    detail: str = ""
    load_sensitive: bool = False


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:300]
    return ""


def _pytest_args(row: dict) -> list[str] | None:
    """The pytest argument list of a suite-covered shape, else None."""
    spec = row.get("check_spec") or {}
    slot = row.get("primitive_slot")
    if slot == "test_selector":
        return [str(spec.get("node_id", ""))]
    if slot == "command_exit_zero":
        argv = [str(a) for a in spec.get("command") or []]
        if len(argv) >= 3 and argv[0] in _PYTEST_HEADS and argv[1:3] == ["-m", "pytest"]:
            return argv[3:]
    return None


def _static_verdict(row: dict, root: Path) -> Outcome | None:
    """An Outcome for a suite-covered record (green or unresolved-red), or None when it must be RUN."""
    args = _pytest_args(row)
    if args is None or nodes.can_select_integration(args, root):
        return None
    ident = (row.get("check_id", "?"), row.get("guard_target", ""))
    missing = nodes.unresolved_nodes(args, root)
    if missing:
        return Outcome(*ident, red=True, detail=f"pinned test id does not resolve: {missing[0]}")
    return Outcome(*ident, red=False)


def _run_in_process(row: dict, root: Path) -> tuple[bool, str]:
    try:
        result = materialize_check_in_tree(row, root).run()
    except GraduationError as exc:
        return False, _first_line(str(exc))
    return result.status.value == "PASS", _first_line(result.message or result.actual)


def _make_rg_stub_dir() -> str:
    """A directory holding an executable rg that fails as on the CI runner, which never installs ripgrep."""
    directory = tempfile.mkdtemp(prefix="rg-stub-")
    stub = Path(directory) / "rg"
    stub.write_text(_RG_STUB, encoding="utf-8")
    stub.chmod(0o755)
    return directory


def _run_child(row: dict, root: Path, rg_stub_dir: str | None = None) -> tuple[bool, str]:
    owned = rg_stub_dir is None
    stub_dir = _make_rg_stub_dir() if rg_stub_dir is None else rg_stub_dir
    try:
        return _run_child_with_stub(row, root, stub_dir)
    finally:
        if owned:
            shutil.rmtree(stub_dir, ignore_errors=True)


def _run_child_with_stub(row: dict, root: Path, stub_dir: str) -> tuple[bool, str]:
    cmd = [sys.executable, "-m", _MODULE, "--check-id", str(row["check_id"]), "--root", str(root)]
    proc = subprocess.Popen(
        cmd,
        cwd=str(HEAD_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=True,
        env={**os.environ, "PATH": stub_dir + os.pathsep + os.environ.get("PATH", "")},
    )
    try:
        out, err = proc.communicate(timeout=RECORD_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        return False, f"timed out after {RECORD_TIMEOUT_S} s"
    try:
        payload = json.loads(out.strip().splitlines()[-1])
    except (IndexError, ValueError):
        return False, _first_line(err) or f"child exited {proc.returncode} without a verdict"
    return bool(payload["passed"]), str(payload.get("detail", ""))


def _run_record(row: dict, root: Path, rg_stub_dir: str | None = None) -> tuple[bool, str]:
    if row.get("primitive_slot") in _CHILD_SLOTS:
        return _run_child(row, root, rg_stub_dir)
    return _run_in_process(row, root)


def evaluate(root: Path, *, select: Callable[[dict], bool] | None = None) -> list[Outcome]:
    """Every live record's outcome at `root`, in check_id order; with `select`, only the rows it accepts."""
    rows = sorted(load_entries(root), key=lambda r: str(r.get("check_id", "")))
    if select is not None:
        rows = [r for r in rows if select(r)]
    outcomes: dict[str, Outcome] = {}
    to_run: list[dict] = []
    for row in rows:
        verdict = _static_verdict(row, root)
        if verdict is None:
            to_run.append(row)
        else:
            outcomes[verdict.check_id] = verdict
    stub_dir = _make_rg_stub_dir()
    try:
        with ThreadPoolExecutor(max_workers=min(8, os.cpu_count() or 1)) as pool:
            pooled = list(pool.map(lambda r: _run_record(r, root, stub_dir), to_run))
        for row, (passed, detail) in zip(to_run, pooled, strict=True):
            ident = (row.get("check_id", "?"), row.get("guard_target", ""))
            if passed:
                outcomes[ident[0]] = Outcome(*ident, red=False)
                continue
            again, detail2 = _run_record(row, root, stub_dir)
            outcomes[ident[0]] = (
                Outcome(*ident, red=False, load_sensitive=True)
                if again
                else Outcome(*ident, red=True, detail=detail2 or detail)
            )
    finally:
        shutil.rmtree(stub_dir, ignore_errors=True)
    return [outcomes[str(r.get("check_id", "?"))] for r in rows]


def red_records(root: Path) -> set[str]:
    return {o.check_id for o in evaluate(Path(root)) if o.red}


def live_ids(root: Path) -> set[str]:
    return {str(r.get("check_id")) for r in load_entries(Path(root))}


def _load_known_red(root: Path) -> tuple[list[str] | None, str]:
    path = root / KNOWN_RED_REL
    if not path.is_file():
        return None, f"{KNOWN_RED_REL} is missing"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        entries = data["entries"]
        ids = [e["check_id"] for e in entries]
    except (yaml.YAMLError, KeyError, TypeError):
        return None, f"{KNOWN_RED_REL} is malformed (expected entries: [{{check_id, owner_rec, entered_at}}])"
    return ids, ""


@registry.register("validate_graduated_rerun", owner="platform")
def validate_graduated_rerun(failed: list[str], root: Path | None = None) -> None:
    tree = Path(root) if root is not None else _common.ROOT
    print("=== Graduated record re-run ===")
    outcomes = evaluate(tree)
    listed, problem = _load_known_red(tree)
    red = {o.check_id: o for o in outcomes if o.red}
    sensitive = [o for o in outcomes if o.load_sensitive]
    for o in sensitive:
        print(f"load-sensitive (failed in the pool, passed alone): {o.check_id}")
    if listed is None:
        failed.append(problem)
        listed = []
    live = {o.check_id for o in outcomes}
    seen: set[str] = set()
    for check_id in listed:
        if check_id in seen:
            failed.append(f"known_red.yaml lists {check_id} twice; remove the duplicate")
        seen.add(check_id)
        if check_id not in live:
            failed.append(f"known_red.yaml lists {check_id}: retired or renamed; remove it")
        elif check_id not in red:
            failed.append(f"known_red.yaml lists {check_id}: passes; remove it from known_red.yaml")
    for check_id in sorted(set(red) - seen):
        o = red[check_id]
        failed.append(f"graduated record {check_id} (guard_target {o.guard_target}) is red: {o.detail}")
    listed_red = len(set(red) & seen)
    print(f"{len(outcomes)} graduated_records examined, {len(red)} red ({listed_red} listed), {len(sensitive)} load-sensitive")
    registry.examined(len(outcomes), unit="graduated_records")


def _child_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-id", required=True)
    parser.add_argument("--root", required=True)
    ns = parser.parse_args(argv)
    root = Path(ns.root)
    row = next((r for r in load_entries(root) if r.get("check_id") == ns.check_id), None)
    if row is None:
        print(json.dumps({"passed": False, "detail": f"{ns.check_id} not found under {root}"}))
        return 0
    passed, detail = _run_in_process(row, root)
    print(json.dumps({"passed": passed, "detail": detail}))
    return 0


if __name__ == "__main__":
    sys.exit(_child_main(sys.argv[1:]))
