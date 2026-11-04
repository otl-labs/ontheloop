"""Plan-built evaluators for the wording check: derive them, attribute a red fast tier to them, stand them in.

A sidecar can create a Class D contract whose evaluator check the same plan's build creates, or an existing check the
build extends to read it. In the scratch tree that evaluator does not exist yet, or does not read the contract yet, so
validate_contract_drift is red for a reason the build itself resolves. This module derives such pairs from the plan's
scope, decides whether a red fast tier is explained by them alone, and runs a sub-run in the scratch tree with exactly
those pairs stood in: a created evaluator's whole resolution, a modified one's reading only. It lives only on the
wording check's side: nothing under scripts/checks, scripts/validate.py or the workflows names it (Decision 235
clause 5).

CLI (run inside the scratch tree):
python -m scripts.roadmap.wording_check_pending [--pending NAME.yaml:CHECK[:modify] ...] [-- TESTS]
"""

from __future__ import annotations

import argparse
import contextlib
import subprocess
import sys
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import Any, Literal

import yaml

Kind = Literal["create", "modify"]
Pair = tuple[str, str, str, Kind]
Runner = Callable[[list[str], Path, int], "subprocess.CompletedProcess[str]"]

_CONTRACTS = "docs/contracts/"
_CHECKS = "scripts/checks/"
_ADMITTED_GATES = frozenset({"validate_contract_drift", "pytest_diff"})
_DRIFT = "validate_contract_drift"
_TAIL_LINES = 20
_MODULE = "scripts.roadmap.wording_check_pending"
_CREATE: Kind = "create"
_MODIFY: Kind = "modify"


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


def _action(entry: Any) -> str:
    return str(entry.action).rsplit(".", 1)[-1].lower()


def _unread_module(tree: Path, basename: str, check: str, modified: list[str]) -> str | None:
    from scripts.checks.contracts import _population

    candidates = [f for f in modified if f.startswith(_CHECKS) and Path(f).name == f"{check}.py" and (tree / f).is_file()]
    if not candidates:
        return None
    registered = _population._find_check_module(check, tree)
    for rel in candidates:
        module = tree / rel
        if registered != module:
            continue
        files = [module, *_population.one_hop_scripts_imports(module, tree)]
        if not any(_population.module_contains_literal(f, basename) for f in files):
            return rel
    return None


def pending_pairs(tree: Path, sidecar_files: set[str], plan: Any) -> list[Pair]:
    """(contract basename, evaluator check, module, "create" or "modify") for each Class D evaluator the plan builds.

    A Create row counts when T lacks its module. A Modify row counts when T registers the check in that module and
    neither the module nor its one-hop scripts/ imports read the contract yet (Decision 168 clause 2's evidence leg).
    """
    if plan is None:
        return []
    created = [e.file for e in plan.scope if _action(e) == _CREATE]
    modified = [e.file for e in plan.scope if _action(e) == _MODIFY]
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
            pairs.append((parts[2], check, modules[0], _CREATE))
            continue
        module = _unread_module(tree, parts[2], check, modified)
        if module is not None:
            pairs.append((parts[2], check, module, _MODIFY))
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
        return f"the red fast tier does not include {_DRIFT}, which a plan-built evaluator always reddens"
    if not failed <= _ADMITTED_GATES:
        return "the red fast tier includes checks a plan-built evaluator cannot explain: " + ", ".join(
            sorted(failed - _ADMITTED_GATES)
        )
    tests: list[str] = []
    if "pytest_diff" in failed:
        tests = _failed_test_files(tree, output)
        if not tests:
            return "pytest_diff failed and no failing test file under tests/ can be named from its summary lines"
    args = [f"{name}:{check}:{_MODIFY}" if action == _MODIFY else f"{name}:{check}" for name, check, _, action in pairs]
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
def stand_in(pairs: list[tuple[str, str]], modified: Iterable[tuple[str, str]] = ()) -> Iterator[None]:
    """Stand in validate_contract_drift's evaluator resolution for exactly the listed (basename, check) pairs.

    A created pair's whole resolution is stood in. A modified pair's evidence leg only: the original resolve_evaluator
    runs with module_contains_literal answering True for that basename, so registration, sequencing and the module
    lookup still decide.
    """
    from scripts.checks.contracts import _population

    original = _population.resolve_evaluator
    original_literal = _population.module_contains_literal
    listed, reads = set(pairs), set(modified)

    def wrapper(contract_basename: str, evaluator: Any, **kwargs: Any) -> tuple[bool, str]:
        check = getattr(evaluator, "check", None)
        if check is not None and (contract_basename, check) in listed:
            return True, f"stood in: the plan creates check {check}"
        if check is None or (contract_basename, check) not in reads:
            return original(contract_basename, evaluator, **kwargs)

        def reads_it(module_path: Path, literal: str) -> bool:
            return literal == contract_basename or original_literal(module_path, literal)

        _population.module_contains_literal = reads_it
        try:
            ok, detail = original(contract_basename, evaluator, **kwargs)
        finally:
            _population.module_contains_literal = original_literal
        return (True, f"stood in: the plan modifies check {check} to read it") if ok else (False, detail)

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


def _parse_pairs(raw: list[str]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]] | None:
    created: list[tuple[str, str]] = []
    modified: list[tuple[str, str]] = []
    for item in raw:
        fields = item.split(":")
        if len(fields) not in (2, 3) or not all(fields) or fields[2:] not in ([], [_MODIFY]):
            return None
        (modified if len(fields) == 3 else created).append((fields[0], fields[1]))
    return created, modified


def main(argv: list[str] | None = None, test_runner: Callable[[list[str]], int] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    files: list[str] = []
    if "--" in args:
        split = args.index("--")
        args, files = args[:split], args[split + 1 :]
    parser = argparse.ArgumentParser(prog=f"python -m {_MODULE}")
    parser.add_argument("--pending", nargs="*", default=[])
    ns = parser.parse_args(args)
    parsed = _parse_pairs(ns.pending)
    if parsed is None:
        print("malformed --pending argument: expected NAME.yaml:CHECK or NAME.yaml:CHECK:modify", file=sys.stderr)
        return 2
    from scripts.checks import registry

    with stand_in(*parsed):
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
