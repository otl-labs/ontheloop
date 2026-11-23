"""Deterministic fallback filer for CI RCA runs whose agent left a failed bundle without a record (rec-4678).

No LLM and no guessed cause: every text field is a fixed template over bundle and run facts. The workflow step
keeps the run red (exit 1) when anything had to be filed, so the malfunction stays visible.

CLI:
    file --agent-output P --bundles-dir D --jobs P --run-id N --head-sha S --workflow-name W --run-url U
         --force-rca true|false --step-outcomes STR --out P    (stdout: one `marked=true|false` line only)
    pairs AGENT_OUTPUT OUT_FILE    (the agent's FILED pairs plus the out file's lines, one rec_id,category per line)
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import shlex
import sys
from pathlib import Path
from typing import Any, Callable, Optional

from scripts.ci_rca import dedup
from scripts.ci_rca.filing import extract_filed_recs

_SUBTYPE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
_DEFAULT_WORKFLOW_FILE = ".github/workflows/ci-rca.yml"
_STEP_MARKER = "id: fallback"
_LAST_RESORT_CITATION = "scripts/ci_rca/fallback.py:1"


def _clamp(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def terminal_subtype(agent_output: Path) -> str:
    """The agent's JSON envelope subtype, else no_output (absent or empty file) or unparseable_output."""
    try:
        raw = agent_output.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "no_output"
    if not raw.strip():
        return "no_output"
    try:
        data = json.loads(raw)
    except ValueError:
        return "unparseable_output"
    subtype = data.get("subtype") if isinstance(data, dict) else None
    return subtype if isinstance(subtype, str) and _SUBTYPE_RE.match(subtype) else "unparseable_output"


def _load_bundles(bundles_dir: Path) -> list[tuple[Optional[dict], str]]:
    out: list[tuple[Optional[dict], str]] = []
    if not bundles_dir.is_dir():
        return out
    for path in sorted(bundles_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(
                f"::error::CI RCA fallback: bundle {path.name} is not valid JSON; filing it as bundle-absent", file=sys.stderr
            )
            out.append((None, path.name))
            continue
        if not isinstance(data, dict):
            print(f"::error::CI RCA fallback: bundle {path.name} is not a JSON object", file=sys.stderr)
            out.append((None, path.name))
            continue
        out.append((data, path.name))
    return out


def _workflow_file(workflow_name: str) -> str:
    try:
        from scripts.ci_rca.taxonomy import enumerate_workflow_name_paths  # noqa: PLC0415

        for name, path in enumerate_workflow_name_paths():
            if name == workflow_name:
                p = Path(path)
                root = Path(__file__).resolve().parents[2]
                return p.resolve().relative_to(root).as_posix() if p.is_absolute() else p.as_posix()
    except Exception:  # noqa: BLE001
        pass
    return _DEFAULT_WORKFLOW_FILE


def _citation(root: Optional[Path] = None) -> str:
    wf = (root or Path(__file__).resolve().parents[2]) / _DEFAULT_WORKFLOW_FILE
    try:
        for i, line in enumerate(wf.read_text(encoding="utf-8").splitlines(), start=1):
            if line.strip().endswith(_STEP_MARKER):
                return f"{_DEFAULT_WORKFLOW_FILE}:{i}"
    except OSError:
        pass
    return _LAST_RESORT_CITATION


def _evidence_ref(sha: str, pending_dir: Path) -> dict[str, str]:
    if (pending_dir / f"{sha}.json").exists():
        return {"sha256": sha, "s3_uri": "", "upload_status": "upload_failed"}
    from scripts.ci_rca.evidence import _resolve_bucket  # noqa: PLC0415

    bucket = _resolve_bucket()
    if not bucket:
        return {"sha256": sha, "s3_uri": "", "upload_status": "upload_failed"}
    return {"sha256": sha, "s3_uri": f"s3://{bucket}/ci-rca-evidence/{sha}.json", "upload_status": "ok"}


def _acceptance(bundle: Optional[dict], workflow_file: str) -> str:
    nodeids = [str(n) for n in ((bundle or {}).get("affected_nodeids") or [])][:5]
    if nodeids:
        return "bin/venv-python -m pytest " + " ".join(shlex.quote(n) for n in nodeids) + " -q"
    base = Path(workflow_file).name
    return (
        f"gh run list --workflow {shlex.quote(base)} --branch main --limit 1 --json conclusion "
        "--jq '.[0].conclusion' | grep -qx success"
    )


def _unobserved(bundle: Optional[dict]) -> list[dict[str, int]]:
    from scripts.ops_portal.ci_rca_runtime import _unobserved_pairs_from_scope  # noqa: PLC0415

    evidence = (bundle or {}).get("retrieval_evidence")
    scope = evidence.get("scope") if isinstance(evidence, dict) else None
    return _unobserved_pairs_from_scope(scope) if isinstance(scope, list) else []


def _jobs_without_steps(jobs_path: Path) -> int:
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, list):
        return 0
    return sum(1 for j in jobs if isinstance(j, dict) and j.get("conclusion") == "failure" and not (j.get("steps") or []))


def compose(  # noqa: PLR0913
    bundle: Optional[dict],
    *,
    bundle_name: str,
    subtype: str,
    run: dict[str, str],
    workflow_file: str,
    pending_dir: Path,
    citation: str,
    jobs_no_step: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build (rec fields, CiRcaContext dict) from fixed templates over bundle and run facts."""
    bundle = bundle or {}
    category = str(bundle.get("failure_category") or "unknown")
    check = str(bundle.get("failed_check") or "unknown")
    signature = _clamp(bundle.get("error_signature") or "none recorded", 200)
    bundle_sha = str(bundle.get("sha256") or "")
    sha7 = run["head_sha"][:7]
    subtype = subtype if _SUBTYPE_RE.match(subtype) else "unparseable_output"
    actual_gate = bundle.get("actual_gate_that_caught_it")
    if actual_gate not in ("pre", "presubmit", "CI"):
        actual_gate = "unknown"
    bundle_phrase = f"bundle {bundle_sha[:12]}" if bundle_sha else "no evidence bundle"
    why_chain = [
        f"The workflow run {run['run_id']} failed a check and its evidence is recorded in {bundle_phrase}.",
        f"The CI RCA agent ended with terminal subtype {subtype} and filed no diagnosis record for this failure.",
        f"The deterministic fallback recorded this undiagnosed red so it is never left without a record ({citation}).",
    ]
    detection_gap: dict[str, Any] = {
        "earliest_viable_gate": "undetermined",
        "actual_gate_that_caught_it": actual_gate,
        "gap_explanation": (
            f"The earliest viable gate is not determined because no diagnosis exists for run "
            f"{run['run_id']} ({bundle_phrase}); "
            f"the CI RCA agent ended {subtype} and the fallback recorded it at {citation}. "
            "A human reviewer determines the gate."
        ),
    }
    if bundle.get("escape_mode"):
        detection_gap["escape_mode"] = bundle["escape_mode"]
    ctx: dict[str, Any] = {
        "schema_version": 2,
        "agent_failure": subtype,
        "rca_confidence": "undetermined",
        "proximate_cause": (
            f"Run {run['run_id']} at {run['head_sha']} failed check {_clamp(check, 80)} (category {category}, signature "
            f"{signature}); the CI RCA agent ended {subtype} with no diagnosis, so no cause is recorded."
        )[:600],
        "why_chain": why_chain,
        "why_chain_terminus_override": {
            "reason": (
                "No diagnosis exists, so no file:line terminus can be named; "
                "the mandatory human review of this undetermined record supplies it."
            )
        },
        "detection_gap": detection_gap,
        "recurrence_class": "novel",
        "corrective_action": (
            "Diagnose the failure from the run and the evidence bundle, then record the cause or fix forward. "
            "When the acceptance is the gh run list placeholder, replace it through update_rec with the fix's "
            "own discriminating test before writing "
            "the Resolves trailer (Decision 201); never close this record by hand."
        ),
        "preventive_action": (
            f"Find why the agent filed nothing (terminal subtype {subtype}, "
            f"step outcomes {run['step_outcomes']}) and fix that "
            "path; the CI RCA smoke run is the standing check that the agent files."
        ),
    }
    unobserved = _unobserved(bundle)
    if unobserved:
        ctx["unobserved_steps"] = unobserved
    elif isinstance((bundle.get("retrieval_evidence") or {}).get("scope"), list):
        ctx["unobserved_steps"] = []
    if bundle_sha:
        ctx["evidence_bundle_ref"] = _evidence_ref(bundle_sha, pending_dir)
    context = (
        f"CI RCA fallback: run {run['run_id']} ({run['run_url']}), head {run['head_sha']}, failed check {_clamp(check, 80)}, "
        f"category {category}, bundle {bundle_sha or 'absent'}, error signature {signature}, "
        f"agent terminal subtype {subtype}, "
        f"step outcomes {run['step_outcomes']}, {jobs_no_step} failing job(s) ran no step."
    )
    fields: dict[str, Any] = {
        "title": _clamp(
            f"{run['workflow_name']} red at {sha7}: {check if bundle else category} not diagnosed (CI RCA fallback)", 80
        ),
        "file": workflow_file,
        "status": "open",
        "source": "ci_rca",
        "effort": "M",
        "risk": "medium",
        "tags": ["ci-rca-fallback"],
        "context": context,
        "acceptance": _acceptance(bundle if bundle_sha else None, workflow_file),
    }
    return fields, ctx


def _covered_by_markers(bundles: list[tuple[Optional[dict], str]], pairs: list[tuple[str, str]]) -> set[int]:
    ids: dict[str, set[str]] = {}
    for rec_id, cat in pairs:
        if cat:
            ids.setdefault(cat, set()).add(rec_id)
    counts: dict[str, int] = {}
    for bundle, _ in bundles:
        if bundle is not None:
            cat = str(bundle.get("failure_category") or "unknown")
            counts[cat] = counts.get(cat, 0) + 1
    full = {cat for cat, n in counts.items() if len(ids.get(cat, set())) >= n}
    return {i for i, (b, _) in enumerate(bundles) if b is not None and str(b.get("failure_category") or "unknown") in full}


def _classify(  # noqa: PLR0913
    bundle: dict,
    *,
    force_rca: bool,
    filed_ids: set[str],
    finder: Optional[dedup.FinderFn],
    resolver: Optional[Callable[[str], Any]],
) -> tuple[bool, Optional[str]]:
    """(covered, rec_id_to_mark). Never bumps."""
    fps = dedup.sibling_fingerprints(bundle)
    entry: dedup.Entry = fps[0] if len(fps) == 1 else fps or ""
    result = dedup.decide(
        [entry],
        force_rca=False,
        finder=finder,
        bumper=lambda _rec_id: None,
        closed_head_resolver=resolver or dedup._default_closed_head_resolver,
    )
    verdict = result.verdicts[0]
    if verdict.matched_rec_id:
        if force_rca and verdict.matched_rec_id not in filed_ids:
            return False, None
        return True, verdict.matched_rec_id
    if verdict.fingerprint and not verdict.run_agent:
        return True, None
    return False, None


def run_file(  # noqa: PLR0913
    *,
    agent_output: Path,
    bundles_dir: Path,
    jobs: Path,
    run: dict[str, str],
    force_rca: bool,
    out: Path,
    filer: Optional[Callable[..., Any]] = None,
    finder: Optional[dedup.FinderFn] = None,
    resolver: Optional[Callable[[str], Any]] = None,
    pending_dir: Optional[Path] = None,
    citation: Optional[str] = None,
) -> tuple[bool, int]:
    """Returns (marked, exit_code). Writes covered/filed rec ids to out as `rec_id,category` lines."""
    pairs = extract_filed_recs(agent_output)
    filed_ids = {r for r, _ in pairs}
    bundles = _load_bundles(bundles_dir)
    covered = _covered_by_markers(bundles, pairs)
    lines: list[str] = []

    def flush() -> None:
        out.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")

    uncovered: list[int] = []
    work: list[int] = [i for i in range(len(bundles)) if i not in covered]
    try:
        for i in work:
            bundle = bundles[i][0]
            if bundle is None:
                uncovered.append(i)
                continue
            ok, rec_id = _classify(bundle, force_rca=force_rca, filed_ids=filed_ids, finder=finder, resolver=resolver)
            if ok:
                if rec_id:
                    lines.append(f"{rec_id},{bundle.get('failure_category') or 'unknown'}")
            else:
                uncovered.append(i)
    except Exception as exc:  # noqa: BLE001
        print(f"::error::CI RCA fallback: reader lookup failed ({type(exc).__name__}: {exc}); nothing filed", file=sys.stderr)
        flush()
        return bool(lines), 1
    if not bundles and not pairs:
        uncovered.append(-1)
    if not uncovered:
        flush()
        return bool(lines), 0

    _file_uncovered(
        [bundles[i] if i >= 0 else (None, "none") for i in uncovered],
        lines,
        agent_output=agent_output,
        jobs=jobs,
        run=run,
        filer=filer,
        pending_dir=pending_dir,
        citation=citation,
    )
    flush()
    return bool(lines), 1


def _file_uncovered(  # noqa: PLR0913
    items: list[tuple[Optional[dict], str]],
    lines: list[str],
    *,
    agent_output: Path,
    jobs: Path,
    run: dict[str, str],
    filer: Optional[Callable[..., Any]],
    pending_dir: Optional[Path],
    citation: Optional[str],
) -> None:
    if filer is None:
        from scripts.ops_data_portal import file_rec_with_outcome  # noqa: PLC0415

        file_fn: Callable[..., Any] = file_rec_with_outcome
    else:
        file_fn = filer

    subtype = terminal_subtype(agent_output)
    wf_file = _workflow_file(run["workflow_name"])
    cite = citation or _citation()
    pending = pending_dir or Path(__file__).resolve().parents[2] / "logs" / ".ci-rca-evidence-pending"
    jobs_no_step = _jobs_without_steps(jobs)
    for bundle, name in items:
        fields, ctx = compose(
            bundle,
            bundle_name=name,
            subtype=subtype,
            run=run,
            workflow_file=wf_file,
            pending_dir=pending,
            citation=cite,
            jobs_no_step=jobs_no_step,
        )
        category = str((bundle or {}).get("failure_category") or "unknown")
        try:
            outcome = file_fn(fields, context_v2_json=ctx)
        except Exception as exc:  # noqa: BLE001
            print(f"::error::CI RCA fallback: filing failed for {name} ({type(exc).__name__}: {exc})", file=sys.stderr)
            continue
        if outcome.inserted or outcome.reason == "ci_rca_open_duplicate":
            lines.append(f"{outcome.rec_id},{category}")


def run_pairs(agent_output: Path, out: Path) -> list[str]:
    seen: list[str] = []
    for rec_id, cat in extract_filed_recs(agent_output):
        seen.append(f"{rec_id},{cat}")
    try:
        seen.extend(line.strip() for line in out.read_text(encoding="utf-8").splitlines() if line.strip())
    except OSError:
        pass
    return list(dict.fromkeys(seen))


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="CI RCA agent-failure fallback filer (rec-4678).")
    sub = parser.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("file")
    f.add_argument("--agent-output", type=Path, required=True)
    f.add_argument("--bundles-dir", type=Path, required=True)
    f.add_argument("--jobs", type=Path, required=True)
    f.add_argument("--run-id", required=True)
    f.add_argument("--head-sha", required=True)
    f.add_argument("--workflow-name", required=True)
    f.add_argument("--run-url", default="")
    f.add_argument("--force-rca", default="false")
    f.add_argument("--step-outcomes", default="")
    f.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("pairs")
    p.add_argument("agent_output", type=Path)
    p.add_argument("out", type=Path)
    args = parser.parse_args(argv)

    if args.cmd == "pairs":
        for line in run_pairs(args.agent_output, args.out):
            print(line)
        return 0
    run = {
        "run_id": re.sub(r"\D", "", args.run_id) or "unknown",
        "head_sha": args.head_sha or "unknown",
        "workflow_name": args.workflow_name,
        "run_url": args.run_url,
        "step_outcomes": _clamp(args.step_outcomes, 200) or "unknown",
    }
    with contextlib.redirect_stdout(sys.stderr):
        marked, code = run_file(
            agent_output=args.agent_output,
            bundles_dir=args.bundles_dir,
            jobs=args.jobs,
            run=run,
            force_rca=str(args.force_rca).lower() in ("1", "true"),
            out=args.out,
        )
    print(f"marked={'true' if marked else 'false'}")
    return code


if __name__ == "__main__":
    sys.exit(main())
