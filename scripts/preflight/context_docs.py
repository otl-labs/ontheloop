"""Context-document and health concern for session_preflight."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from scripts import decisions_md
from scripts.preflight import _common

# Verdicts that earn an attributed stderr line, drawn from the SIX run verdicts
# scripts/data_quality_execute.py's run_checks can write to dq-latest.json: its aggregate
# expression yields HARD_GATE, FAIL, DEGRADED or PASS, its two early returns yield SKIP and its
# empty-results branch yields ERROR. PASS and SKIP are excluded because neither names a
# degradation -- they are exactly the verdicts data_quality_runner's main exits ZERO for, minus
# DEGRADED, which exits zero while naming an unavailable backend and so still owes a line.
# UNAVAILABLE and WARN are per-CHECK verdicts and unreachable at that key, so neither belongs here.
# SKIP is deliberately stdout-only here while the orient gauge scores it WATCH: the two surfaces
# disagree on purpose -- a skipped run is worth a glance in /orient, not a session-open alarm.
# An unreadable artifact deliberately gets NO verdict literal of its own: no consumer row maps an
# invented seventh value, so it degrades to a null verdict plus a read_error the renderer speaks
# and the orient gauge already scores as absent.
_DQ_ALARMING_VERDICTS = frozenset({"FAIL", "DEGRADED", "ERROR", "HARD_GATE"})


def _one_line(exc: object) -> str:
    """Collapse an exception (or any object) to a single whitespace-normalised line.

    A multi-line exception message would fragment one attributed WARN into several unattributed
    continuation lines, which is the opposite of the attribution this module owes.
    """
    return " ".join(str(exc).split())


def _unreadable_dq_run(why: str) -> dict[str, Any]:
    """The named degraded last_run: a NULL verdict plus a read_error naming what could not be read.

    Deliberately not a new verdict LITERAL. .claude/skills/orient/SKILL.md scores
    data_quality.last_run.verdict against the runner's six run verdicts and reads a null or absent
    verdict as GAP, so an invented seventh literal would leave that gauge with no defined state,
    while a null verdict plus a named read_error is both mapped and strictly more informative.
    Built per call rather than copied from a module constant, because each carries its own reason.
    """
    return {
        "verdict": None,
        "read_error": _one_line(why),
        "passed": 0,
        "failed": 0,
        "warned": 0,
        "errored": 0,
        "unavailable": 0,
        "timestamp": "",
    }


def read_context_files(open_recs_count: int | None = None) -> dict:
    """Read key context documents and return a summary dict for plan.prompt.md.

    Args:
        open_recs_count: Open-recs count from the open_recs_tally row preflight already fetched, or None
            when unavailable. No reader call is made here (Decision 215).

    Returns:
        Dict with keys: roadmap_phase, open_decisions_count,
        recommendations_count.
    """
    # roadmap_phase: extract current phase header from ROADMAP_FILE (docs/ROADMAP-PLATFORM.yaml).
    # The YAML has no "## Phase" markdown headers, so this resolves to "unknown" in production --
    # an honest result; the phase-equivalent signal today is the platform `active_tier`.
    roadmap_phase = "unknown"
    if _common.ROADMAP_FILE.exists():
        content = _common.ROADMAP_FILE.read_text(encoding="utf-8", errors="replace")
        # Look for "## Phase X.Y: ..." headers that are not completed/archived
        phase_matches = re.findall(r"^## (Phase [^\n]+)", content, re.MULTILINE)
        if phase_matches:
            roadmap_phase = phase_matches[0].strip()

    # open_decisions_count: count decision headers not marked Decided/Resolved/Closed.
    # Enumeration is the shared decisions_md.iter_decision_headings() grammar (DAF-03 /
    # PLAN-daf-authoring-grammar) -- the local open/closed paren heuristic below is unchanged.
    open_decisions_count = 0
    if _common.DECISIONS_FILE.exists():
        content = _common.DECISIONS_FILE.read_text(encoding="utf-8", errors="replace")
        for heading_match in decisions_md.iter_decision_headings(content):
            header = heading_match.group(0)
            if not re.search(r"\(Decided\)|\(Resolved\)|\(Closed\)|\(Done\)", header, re.IGNORECASE):
                open_decisions_count += 1

    # recommendations_count comes from the tally preflight already fetched; None when unavailable.
    recommendations_count = open_recs_count

    return {
        "roadmap_phase": roadmap_phase,
        "open_decisions_count": open_decisions_count,
        "recommendations_count": recommendations_count,
    }


def _read_last_dq_run(last_run_file: Path) -> dict | None:
    """Read logs/debug/dq-latest.json, or None when it is absent.

    A malformed payload degrades to a NULL verdict plus a read_error naming the path and the
    reason, rather than vanishing, so a broken local artifact is a named degraded state a consumer
    can see instead of an absent last_run indistinguishable from "never ran".

    Keeps the module's broad except deliberately: this path can raise IsADirectoryError or
    PermissionError (both OSError, neither ValueError) as well as JSONDecodeError, and
    print_data_quality_health is the FIRST call in main(), so a narrowed catch would put a new
    raise through session open. The exists() probe sits INSIDE that guard rather than beside it:
    Path.exists re-raises any OSError whose errno is outside (ENOENT, ENOTDIR, EBADF, ELOOP), so
    an unstattable path -- ENAMETOOLONG, or EACCES on a parent directory under a non-root runner --
    reaches it, and an unguarded probe would put that raise on main()'s thread.

    The verdict is COERCED to str here -- at the one boundary, never at the membership site --
    because a well-formed JSON mapping whose verdict is a list or a mapping would otherwise reach
    the _DQ_ALARMING_VERDICTS frozenset test as an unhashable value.
    """
    try:
        if not last_run_file.exists():
            return None
        data = json.loads(last_run_file.read_text(encoding="utf-8", errors="replace"))
        if not isinstance(data, dict):
            return _unreadable_dq_run(f"{last_run_file} does not carry a JSON object")
        # A readable object with NO verdict key is a degraded state, not the literal "unknown":
        # that literal is neither one of the runner's six run verdicts nor null, so no consumer
        # gauge maps it; the null verdict plus a read_error IS mapped (GAP), and the counts survive.
        # A missing key, a JSON null and an empty string are one condition -- no run verdict -- so
        # they take one route: a null verdict plus a read_error the gauge maps as GAP.
        missing = data.get("verdict") in (None, "")
        return {
            "verdict": None if missing else str(data["verdict"]),
            "read_error": _one_line(f"{last_run_file} carries no verdict key") if missing else None,
            "passed": data.get("passed", 0),
            "failed": data.get("failed", 0),
            "warned": data.get("warned", 0),
            "errored": data.get("errored", 0),
            "unavailable": data.get("unavailable", 0),
            "timestamp": data.get("timestamp", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return _unreadable_dq_run(f"{last_run_file} could not be read ({exc})")


def check_data_quality_coverage() -> dict:
    """Report data quality check coverage from config/agent/data_quality/ YAML files.

    This does NOT execute the checks against the warehouse (that is slow and requires AWS).
    It reports: how many checks are defined, which tables are covered, and
    whether a recent run result exists in logs/debug/dq-latest.json.

    PRINT-FREE by design (print_data_quality_health renders what this records), because main()
    calls it twice -- once through the renderer and once for the report dict -- and a print here
    would double-emit.

    Returns a dict with:
        tables_covered: int | None (None == UNKNOWN, never a hard zero)
        checks_defined: int | None (None == UNKNOWN, never a hard zero)
        coverage_error: str | None (names the path, the directory or the loader that could not be
            read -- a present-but-unreadable directory is an UNKNOWN, never a zero)
        load_errors: list[str] (one collapsed line per check file that would not parse)
        last_run: dict | None (verdict, passed, failed, timestamp from last run)
    """
    dq_dir = _common.ROOT / "config" / "agent" / "data_quality"
    last_run_file = _common.ROOT / "logs" / "debug" / "dq-latest.json"
    last_run = _read_last_dq_run(last_run_file)

    def _unknown(reason: str) -> dict:
        return {
            "tables_covered": None,
            "checks_defined": None,
            "coverage_error": reason,
            "load_errors": [],
            "last_run": last_run,
        }

    # os.scandir, guarded BY NAME, rather than Path.glob: CPython's glob swallows the OSError its
    # selector raises, so a present-but-unreadable directory returned a silent hard zero -- the
    # same "zero means unknown" shape this function exists to remove, in a smaller domain. The
    # is_dir probe sits INSIDE that same guard rather than beside it: Path.is_dir re-raises any
    # OSError whose errno is outside (ENOENT, ENOTDIR, EBADF, ELOOP), so an unstattable ROOT
    # reaches it and an unguarded probe would raise at the FIRST call in main().
    try:
        if not dq_dir.is_dir():
            return _unknown(f"dq config dir missing: {dq_dir}")
        with os.scandir(dq_dir) as scan:
            check_files = sorted(Path(entry.path) for entry in scan if entry.name.endswith(".yaml"))
    except OSError as exc:
        return _unknown(f"dq config dir unreadable: {dq_dir} ({_one_line(exc)})")

    try:
        from scripts.data_quality_runner import load_checks  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return _unknown(f"dq check loader unavailable: {_one_line(exc)}")

    tables: set[str] = set()
    checks_defined = 0
    load_errors: list[str] = []
    for yf in check_files:
        # The per-file guard covers the CONSUMPTION of load_checks' return as well as the call:
        # draining the table generator is where a check object without a .table raises, and a
        # loader defect is a PARTIAL corpus (one load_errors line) rather than a raise.
        try:
            checks, _ = load_checks(yf)
            covered = {c.table for c in checks if c.table} if checks else set()
            count = len(checks) if checks else 0
        except Exception as exc:  # noqa: BLE001
            load_errors.append(f"{yf.name}: {_one_line(exc)}")
            continue
        if checks:
            tables.update(covered)
            checks_defined += count

    return {
        "tables_covered": len(tables),
        "checks_defined": checks_defined,
        "coverage_error": None,
        "load_errors": load_errors,
        "last_run": last_run,
    }


def print_data_quality_health() -> None:
    """Print a compact data-quality coverage summary for the ops tables.

    A zero count from a directory that EXISTS earns its own WARN, and an unreadable last-run
    artifact prints a null-verdict UNKNOWN line naming its read_error rather than a verdict literal
    no consumer gauge maps.

    The section and the last-run verdict ALWAYS print. The former `checks_defined <= 0` early
    return suppressed the whole block -- including a live FAIL verdict -- whenever coverage was
    unavailable, which was the exact production configuration for as long as the coverage glob
    pointed at a directory that never existed.
    """
    dq = check_data_quality_coverage()
    checks_defined = dq.get("checks_defined")
    coverage_error = dq.get("coverage_error")
    if checks_defined is None:
        print(f"\n  Data quality: coverage UNKNOWN -- {coverage_error}")
        print(
            f"[WARN] data-quality coverage is UNKNOWN, not zero -- {coverage_error} "
            "(run: bin/venv-python -m scripts.data_quality_runner)",
            file=sys.stderr,
        )
    else:
        print(f"\n  Data quality: {checks_defined} checks across {dq.get('tables_covered')} tables")
        if not checks_defined:
            print(
                "[WARN] data-quality coverage is ZERO from a directory that EXISTS and was readable -- "
                "the corpus defines no checks at all, which is an empty corpus rather than a healthy zero "
                "(run: ls config/agent/data_quality)",
                file=sys.stderr,
            )
    for load_error in dq.get("load_errors") or []:
        print(
            f"[WARN] data-quality coverage is PARTIAL -- a check file did not load ({load_error}); "
            "the counts above understate the corpus",
            file=sys.stderr,
        )

    last_run = dq.get("last_run")
    if last_run:
        read_error = last_run.get("read_error")
        if read_error:
            print(f"  Last run: verdict UNKNOWN -- the run artifact could not be read ({read_error})")
            print(
                "[WARN] data-quality last run is UNKNOWN, not clean -- the run artifact could not be read "
                f"({read_error}) (run: bin/venv-python -m scripts.data_quality_runner)",
                file=sys.stderr,
            )
            print()
            return
        # NOT re-coerced here: _read_last_dq_run already str()-coerced this value at the one
        # boundary, and a second coercion at the membership site would make the boundary's
        # coercion untested -- the shape matrix must be able to kill its removal.
        verdict = last_run.get("verdict", "unknown")
        unavailable = last_run.get("unavailable", 0)
        unavail_str = f"/{unavailable}U" if unavailable else ""
        verdict_tag = " [DEGRADED -- backend unavailable]" if verdict == "DEGRADED" else ""
        print(
            f"  Last run: {verdict}{verdict_tag} "
            f"({last_run.get('passed', 0)}P/{last_run.get('failed', 0)}F/{last_run.get('warned', 0)}W{unavail_str}) "
            f"at {last_run.get('timestamp', '')}"
        )
        if verdict in _DQ_ALARMING_VERDICTS:
            print(
                f"[WARN] data-quality last run verdict={verdict} "
                f"(errored={last_run.get('errored', 0)}, failed={last_run.get('failed', 0)}) "
                "(run: bin/venv-python -m scripts.data_quality_runner)",
                file=sys.stderr,
            )
    else:
        print("  Last run: never (run: python -m scripts.data_quality_runner)")
    print()


def _check_endstate_drift() -> dict:
    """Advisory drift check: compare the sha256 fingerprint stamped in PROJECT_CONTEXT.md
    against the current sha256 of the sorted ROADMAP-PLATFORM.yaml tier_item ID set.

    Returns a dict {stale, synthesized_hash, current_hash, new_ids, stamp_ref, reason}. ``reason``
    is drawn from a CLOSED seven-value vocabulary -- ok, stamp_absent, parse_error,
    stamp_ref_not_a_commit, stamp_ref_unresolvable, stamp_ref_hash_mismatch,
    stamp_ref_new_ids_named -- so the renderer can tell three previously indistinguishable stale
    causes apart instead of collapsing them into one line.

    Fail-open: any parse/IO error returns a non-stale result with reason parse_error.
    Never raises, never changes the preflight exit code.
    """
    try:
        from scripts.contract_rules import parse_yaml  # noqa: PLC0415

        context_text = (_common.ROOT / "docs" / "PROJECT_CONTEXT.md").read_text(encoding="utf-8")
        stamp_match = re.search(r"roadmap_tier_id_set sha256:\s*([a-f0-9]{64})", context_text)
        commit_match = re.search(r"ROADMAP-PLATFORM\.yaml\s*@\s*([0-9a-f]{7,40})", context_text)
        stamp_ref = commit_match.group(1) if commit_match else None
        if not stamp_match:
            return {
                "stale": False,
                "synthesized_hash": None,
                "current_hash": None,
                "new_ids": [],
                "stamp_ref": stamp_ref,
                "reason": "stamp_absent",
                "note": "stamp absent",
            }
        stamped_hash = stamp_match.group(1)

        roadmap = parse_yaml((_common.ROOT / "docs" / "ROADMAP-PLATFORM.yaml").read_text(encoding="utf-8"))
        _items = roadmap.get("tier_items", [])
        current_ids = sorted({str(i["id"]) for i in _items if isinstance(i, dict) and "id" in i})
        current_hash = hashlib.sha256("\n".join(current_ids).encode()).hexdigest()

        if current_hash == stamped_hash:
            return {
                "stale": False,
                "synthesized_hash": current_hash,
                "current_hash": current_hash,
                "new_ids": [],
                "stamp_ref": stamp_ref,
                "reason": "ok",
            }

        new_ids: list[str] = []
        if stamp_ref is None:
            reason = "stamp_ref_not_a_commit"
        else:
            reason = "stamp_ref_unresolvable"
            try:
                result = subprocess.run(
                    ["git", "show", f"{stamp_ref}:docs/ROADMAP-PLATFORM.yaml"],
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=10,
                    cwd=str(_common.ROOT),
                )
                if result.returncode == 0:
                    old_roadmap = parse_yaml(result.stdout) or {}
                    _old_items = old_roadmap.get("tier_items", [])
                    old_ids = sorted({str(i["id"]) for i in _old_items if isinstance(i, dict) and "id" in i})
                    if hashlib.sha256("\n".join(old_ids).encode()).hexdigest() == stamped_hash:
                        new_ids = sorted(set(current_ids) - set(old_ids))
                        reason = "stamp_ref_new_ids_named"
                    else:
                        reason = "stamp_ref_hash_mismatch"
            except Exception:  # noqa: BLE001
                pass

        return {
            "stale": True,
            "synthesized_hash": stamped_hash,
            "current_hash": current_hash,
            "new_ids": new_ids,
            "stamp_ref": stamp_ref,
            "reason": reason,
        }
    except Exception:  # noqa: BLE001
        return {
            "stale": False,
            "synthesized_hash": None,
            "current_hash": None,
            "new_ids": [],
            "stamp_ref": None,
            "reason": "parse_error",
            "note": "parse error",
        }


def _scan_provisional_contracts(
    contracts_dir: Path | None = None,
    metrics_provider: Callable[[Any], dict[str, Any] | None] | None = None,
) -> list[str]:
    """Return contract ids whose provisional_v0 re_ratification_trigger is met.

    Reads local docs/contracts/ files only -- no warehouse reader, no credentials.
    ``metrics_provider`` is called PER CONTRACT with the doc to obtain a metrics dict;
    when absent (default), default_provisional_metrics supplies the live days-since metric.

    Deliberate exception to this module's compute/render split: the return type is list[str] and
    report[provisional_contracts_due] is a frozen top-level key whose VALUE type must not move, so
    the attribution is printed here rather than returned. Exactly one production call site, so
    printing in place is single-emission by construction.

    The per-contract try is INSIDE the loop, so one raising contract truncates nothing silently;
    the function-local imports are guarded separately from the directory and the loader, because
    either can raise for a malformed contracts tree and would otherwise abort session open. The
    directory probe and the mapping CONSUMPTION are guarded alongside the loader call rather than
    beside it, so an unstattable directory and a non-mapping return are named rather than raised.
    A healthy scan prints nothing.
    """
    unavailable = (
        "[WARN] provisional-contract scan UNAVAILABLE -- {why}; "
        "the due list is empty for that reason, not because nothing is due"
    )
    try:
        from scripts.contracts import load_all_contracts  # noqa: PLC0415
        from scripts.contracts_enforcement import (  # noqa: PLC0415
            default_provisional_metrics,
            evaluate_provisional_trigger,
        )
    except Exception as exc:  # noqa: BLE001
        print(unavailable.format(why=f"the contract machinery could not be imported ({_one_line(exc)})"), file=sys.stderr)
        return []

    target_dir = contracts_dir if contracts_dir is not None else _common.ROOT / "docs" / "contracts"

    # The is_dir probe sits in its OWN guard: Path.is_dir re-raises any OSError outside (ENOENT,
    # ENOTDIR, EBADF, ELOOP), and a stat that failed is not a loader that failed, so the two get
    # distinct attributions. The loader call and its .items() CONSUMPTION (a non-mapping return
    # raises AttributeError there) share the second guard, whose one clause names the loader.
    try:
        if not target_dir.is_dir():
            print(unavailable.format(why=f"{target_dir} is not a directory"), file=sys.stderr)
            return []
    except OSError as exc:
        print(unavailable.format(why=f"{target_dir} could not be read ({_one_line(exc)})"), file=sys.stderr)
        return []
    try:
        contracts = [(str(contract_id), doc) for contract_id, doc in load_all_contracts(target_dir).items()]
    except Exception as exc:  # noqa: BLE001
        print(unavailable.format(why=f"load_all_contracts failed ({_one_line(exc)})"), file=sys.stderr)
        return []

    due: list[str] = []
    failures: list[str] = []
    for contract_id, doc in contracts:
        try:
            metrics = metrics_provider(doc) if metrics_provider else default_provisional_metrics(doc)
            met, _ = evaluate_provisional_trigger(doc, metrics)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{contract_id}: {_one_line(exc)}")
            continue
        if met:
            due.append(contract_id)

    if failures:
        print(
            f"[WARN] provisional-contract scan INCOMPLETE -- {len(failures)} of {len(contracts)} contract(s) "
            f"failed to evaluate (first: {failures[0]}); the due list is partial, not clean",
            file=sys.stderr,
        )
    return due
