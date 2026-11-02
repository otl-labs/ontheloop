"""Plan-created evaluators for the wording check: derive them, attribute a red fast tier to them, stand them in.

A sidecar can create a Class D contract whose evaluator check the same plan's build creates. In the scratch tree that
evaluator does not exist yet, so validate_contract_drift is red for a reason the build itself resolves. This module
derives such pairs from the plan's scope, decides whether a red fast tier is explained by them alone, and runs a
sub-run in the scratch tree with exactly those pairs stood in. It lives only on the wording check's side: nothing under
scripts/checks, scripts/validate.py or the workflows names it (Decision 235 clause 5).

CLI (run inside the scratch tree): python -m scripts.roadmap.wording_check_pending [--pending NAME.yaml:CHECK ...] [-- TESTS]
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import yaml

Pair = tuple[str, str, str]
Runner = Callable[[list[str], Path, int], "subprocess.CompletedProcess[str]"]

_CONTRACTS = "docs/contracts/"
_CHECKS = "scripts/checks/"
_ADMITTED_GATES = frozenset({"validate_contract_drift", "pytest_diff"})
_DRIFT = "validate_contract_drift"
_TAIL_LINES = 20
_MODULE = "scripts.roadmap.wording_check_pending"


def _contract_check(tree: Path, rel: str) -> str | None:
    try:
        data = yaml.safe_load((tree / rel).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    contract = data.get("contract") if isinstance(data, dict) else None
    if not isinstance(contract, dict) or contract.get("class") != "D":
        return None
    evaluator = contract.get("evaluator")
    check = evaluator.get("check") if isinstance(evaluator, dict) else None
    return check if isinstance(check, str) and check else None


def pending_pairs(tree: Path, sidecar_files: set[str], plan: Any) -> list[Pair]:
    """(contract basename, evaluator check, the created module) for each Class D evaluator the plan Creates and T lacks."""
    if plan is None:
        return []
    created = [e.file for e in plan.scope if str(e.action).rsplit(".", 1)[-1].lower() == "create"]
    pairs: list[Pair] = []
    for rel in sorted(sidecar_files):
        parts = rel.split("/")
        if not rel.startswith(_CONTRACTS) or len(parts) != 3 or not parts[2].endswith(".yaml"):
            continue
        check = _contract_check(tree, rel)
        if check is None:
            continue
        modules = [f for f in created if f.startswith(_CHECKS) and Path(f).name == f"{check}.py"]
        if modules and not any((tree / f).exists() for f in modules):
            pairs.append((parts[2], check, modules[0]))
    return pairs


def _tail(text: str) -> str:
    return "\n".join(text.strip().splitlines()[-_TAIL_LINES:])


def _failed_test_files(tree: Path, output: str) -> list[str]:
    from scripts.checks import _pytest_diff

    files: set[str] = set()
    for match in _pytest_diff._FAILED_SUMMARY_LINE_RE.finditer(output):
        token = match.group(1).split("::", 1)[0]
        if token.startswith("tests/") and (tree / token).is_file():
            files.add(token)
    return sorted(files)


def attribute(tree: Path, output: str, pairs: list[Pair], runner: Runner, timeout: int) -> str | None:
    """None when the red fast tier is explained by `pairs` alone (checked by a stand-in sub-run), else the reason."""
    from src.turn_capture.markers import parse_markers

    failed = {m.signature for m in parse_markers(output) if m.source == "gate"}
    if _DRIFT not in failed:
        return f"the red fast tier does not include {_DRIFT}, which a plan-created evaluator always reddens"
    if not failed <= _ADMITTED_GATES:
        return "the red fast tier includes checks a plan-created evaluator cannot explain: " + ", ".join(
            sorted(failed - _ADMITTED_GATES)
        )
    tests: list[str] = []
    if "pytest_diff" in failed:
        tests = _failed_test_files(tree, output)
        if not tests:
            return "pytest_diff failed and no failing test file under tests/ can be named from its summary lines"
    args = [f"{name}:{check}" for name, check, _ in pairs]
    cmd = ["bin/venv-python", "-m", _MODULE, "--pending", *args, "--", *tests]
    try:
        proc = runner(cmd, tree, timeout)
    except subprocess.TimeoutExpired:
        return f"the stand-in sub-run timed out after {timeout} s"
    except OSError as exc:
        return f"the stand-in sub-run could not start: {exc}"
    if proc.returncode == 0:
        return None
    return "the stand-in sub-run is still red:\n" + _tail((proc.stdout or "") + (proc.stderr or ""))


@contextlib.contextmanager
def stand_in(pairs: list[tuple[str, str]]) -> Iterator[None]:
    """Make validate_contract_drift's evaluator resolution succeed for exactly the listed (basename, check) pairs."""
    from scripts.checks.contracts import _population

    original = _population.resolve_evaluator
    listed = set(pairs)

    def wrapper(contract_basename: str, evaluator: Any, **kwargs: Any) -> tuple[bool, str]:
        check = getattr(evaluator, "check", None)
        if check is not None and (contract_basename, check) in listed:
            return True, f"stood in: the plan creates check {check}"
        return original(contract_basename, evaluator, **kwargs)

    _population.resolve_evaluator = wrapper
    try:
        yield
    finally:
        _population.resolve_evaluator = original


def _pytest_args(files: list[str]) -> list[str]:
    from scripts.checks import _pytest_diff

    return [
        *files,
        "-m",
        "not integration",
        "-q",
        "--timeout",
        "60",
        "--timeout-method=thread",
        f"--randomly-seed={_pytest_diff._PYTEST_RANDOMLY_SEED}",
    ]


def _run_pytest(args: list[str]) -> int:
    import pytest

    return int(pytest.main(args))


def _parse_pairs(raw: list[str]) -> list[tuple[str, str]] | None:
    pairs: list[tuple[str, str]] = []
    for item in raw:
        name, sep, check = item.partition(":")
        if not sep or not name or not check:
            return None
        pairs.append((name, check))
    return pairs


def main(argv: list[str] | None = None, test_runner: Callable[[list[str]], int] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    files: list[str] = []
    if "--" in args:
        split = args.index("--")
        args, files = args[:split], args[split + 1 :]
    parser = argparse.ArgumentParser(prog=f"python -m {_MODULE}")
    parser.add_argument("--pending", nargs="*", default=[])
    ns = parser.parse_args(args)
    pairs = _parse_pairs(ns.pending)
    if pairs is None:
        print("malformed --pending argument: expected NAME.yaml:CHECK", file=sys.stderr)
        return 2
    from scripts.checks import registry

    with stand_in(pairs):
        failed: list[str] = []
        registry.resolve(_DRIFT)(failed)
        for label in failed:
            print(label)
        status = 1 if failed else 0
        if files:
            tests_rc = (test_runner or _run_pytest)(_pytest_args(files))
            status = status or (0 if tests_rc == 0 else 1)
    return status


if __name__ == "__main__":
    sys.exit(main())
