"""Evidence layer over the guidance-eval grader (Decision 280 cl.3, rec-5660): provenance-bound live-run records.

A persona that declares a guidance slot ships only with fresh evidence that a slot value cannot relax its verdict: one
record per pre-registered case (baseline plus each manifest case), written by the record command from a primary-role run's
artifacts, replayed through guidance_eval.grade. A stale record set is excused only by a bound rerun_owed marker (a debt
named against a merged plan, never an authored date). Stdlib, yaml and the guidance modules; no network, no model.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from scripts.persona_contracts import guidance_eval
from scripts.personas import execute, guidance

RECORDS_HOME = "tests/fixtures/persona_verdict/guidance_eval/<persona>.records.yaml"
THIS_PLAN = "docs/plans/PLAN-canary-guidance-live-runs.yaml"
BASELINE = guidance_eval.BASELINE
_FILE_KEYS = {"written_by", "records", "discarded"}
_RECORD_KEYS = {
    "case", "outcome", "check_title", "check_run_id", "run_id", "run_attempt", "main_sha", "head_sha", "role", "model_id",
    "requested_model", "image_digest", "contract_version", "rendered_prompt_sha256", "guidance_sha256", "notes_sha256",
    "run_date",
}  # fmt: skip
_DISCARD_KEYS = {"case", "run_id", "outcome", "reason"}
_OWED_KEYS = {"rec", "plan", "inputs_sha256"}
_SLUG = re.compile(r"^[a-z0-9-]+$")
_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_MODEL = re.compile(r"^[A-Za-z0-9._:/-]+$")
_REC = re.compile(r"^rec-[0-9]+$")
_PLAN = re.compile(r"^docs/plans/PLAN-([a-z0-9-]+)\.yaml$")
_IGNORED_ROLES = {"cold_start_probe", "warm_fetched_escape_hatch"}
FALLBACK_MARK = " (fallback role"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _pos_int(value: Any) -> bool:
    return type(value) is int and value > 0


def records_path(root: Path, persona: str) -> Path:
    return root / RECORDS_HOME.replace("<persona>", persona)


def expected_notes(manifest: guidance_eval.Manifest) -> str:
    """The notes materialise's fixture provider builds from the manifest notes under the committed pull request body."""
    return manifest.notes if manifest.pr_body is None else f"{manifest.notes}\n{manifest.pr_body}"


def _contract(root: Path, persona: str) -> dict[str, Any]:
    doc = yaml.safe_load((root / execute.CONTRACTS / f"{persona}.yaml").read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("the contract is not a mapping")
    return doc


def inputs_sha256(root: Path, persona: str) -> str:
    manifest = guidance_eval.load_manifest(root, persona)
    contract = _contract(root, persona)
    parts = [str(contract.get("contract_version")), str(contract.get("rendered_prompt_sha256"))]
    parts += [
        _sha(guidance.case_file(root, persona, manifest.slot, c.name)) for c in sorted(manifest.cases, key=lambda c: c.name)
    ]
    parts.append(_sha(expected_notes(manifest)))
    return _sha("\n".join(parts))


def derive_outcome(title: str) -> str:
    for prefix in ("verdict ", "failed: "):
        if title.startswith(prefix):
            return title[len(prefix) :]
    return title


def _unknown(where: str, doc: Mapping[str, Any], keys: set[str]) -> list[str]:
    extra, missing = sorted(map(str, set(doc) - keys)), sorted(keys - set(doc))
    return ([f"{where} has unknown keys {extra}"] if extra else []) + ([f"{where} lacks keys {missing}"] if missing else [])


def _record_problems(rec: Any, where: str) -> list[str]:
    if not isinstance(rec, dict):
        return [f"{where} is not a mapping"]
    out = _unknown(where, rec, _RECORD_KEYS)
    if out:
        return out
    checks: list[tuple[str, bool]] = [
        ("case", isinstance(rec["case"], str)),
        ("outcome", isinstance(rec["outcome"], str) and bool(rec["outcome"]) and rec["outcome"] != "PROCEED"),
        ("check_title", isinstance(rec["check_title"], str) and bool(rec["check_title"])),
        ("role", rec["role"] == "primary"),
        ("contract_version", _pos_int(rec["contract_version"])),
        ("run_date", _is_date(rec["run_date"])),
    ]
    checks += [(k, _pos_int(rec[k])) for k in ("check_run_id", "run_id", "run_attempt")]
    checks += [(k, isinstance(rec[k], str) and bool(_SHA40.match(rec[k]))) for k in ("main_sha", "head_sha")]
    checks += [(k, isinstance(rec[k], str) and bool(_MODEL.match(rec[k]))) for k in ("model_id", "requested_model")]
    checks += [("image_digest", isinstance(rec["image_digest"], str) and bool(_DIGEST.match(rec["image_digest"])))]
    checks += [
        (k, isinstance(rec[k], str) and bool(_HEX64.match(rec[k])))
        for k in ("rendered_prompt_sha256", "guidance_sha256", "notes_sha256")
    ]
    return [f"{where} field {name} is malformed" for name, ok in checks if not ok]


def _is_date(value: Any) -> bool:
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return True
    try:
        return isinstance(value, str) and dt.date.fromisoformat(value) is not None
    except ValueError:
        return False


def _discard_problems(entry: Any, where: str, known: set[str]) -> list[str]:
    if not isinstance(entry, dict):
        return [f"{where} is not a mapping"]
    out = _unknown(where, entry, _DISCARD_KEYS)
    if out:
        return out
    if entry["case"] not in known:
        out.append(f"{where} names unknown case {entry['case']!r}")
    if not _pos_int(entry["run_id"]):
        out.append(f"{where} run_id is malformed")
    if not all(isinstance(entry[k], str) and entry[k] for k in ("outcome", "reason")):
        out.append(f"{where} outcome or reason is malformed")
    return out


def load_records(path: Path) -> dict[str, Any]:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(doc, dict):
        raise ValueError("the records file is not a mapping")
    problems = _unknown("records file", doc, _FILE_KEYS)
    if problems:
        raise ValueError("; ".join(problems))
    return doc


def _base_show(root: Path, rel: str) -> str | None:
    """rel at the merge-base with origin/main; ValueError when the merge-base is unreadable, None when rel is absent."""
    mb = subprocess.run(  # noqa: S603
        ["git", "merge-base", "origin/main", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
        check=False,  # noqa: S607
    )
    if mb.returncode != 0:
        raise ValueError("the merge-base with origin/main is unreadable")
    shown = subprocess.run(  # noqa: S603
        ["git", "show", f"{mb.stdout.strip()}:{rel}"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=root,
        check=False,  # noqa: S607
    )
    return shown.stdout if shown.returncode == 0 else None


def _owed_shape(owed: Any) -> list[str]:
    if not isinstance(owed, dict):
        return ["rerun_owed is not a mapping"]
    problems = _unknown("rerun_owed", owed, _OWED_KEYS)
    if problems:
        return problems
    out = []
    if not (isinstance(owed["rec"], str) and _REC.match(owed["rec"])):
        out.append("rerun_owed rec is off ^rec-[0-9]+$")
    if not (isinstance(owed["plan"], str) and _PLAN.match(owed["plan"])):
        out.append("rerun_owed plan is off docs/plans/PLAN-<slug>.yaml")
    if not (isinstance(owed["inputs_sha256"], str) and _HEX64.match(owed["inputs_sha256"])):
        out.append("rerun_owed inputs_sha256 is not 64 hex")
    return out


def _evidence_inputs(persona: str, manifest_rel: str) -> tuple[set[str], str]:
    contract = f"{execute.CONTRACTS}/{persona}.yaml"
    rendered = f"{execute.CONTRACTS}/rendered/{persona}.txt"
    cases = guidance.CASE_HOME.replace("<persona>", persona).rsplit("/", 1)[0] + "/"
    return {contract, rendered, manifest_rel}, cases


def _owed_bound(root: Path, persona: str, manifest: guidance_eval.Manifest, written_by: Any) -> list[str]:
    """The lines refusing a rerun_owed marker that is not bound to a merged, human-confirmed re-run plan."""
    owed = manifest.rerun_owed
    problems = _owed_shape(owed)
    if problems:
        return problems
    out: list[str] = []
    if owed["inputs_sha256"] != inputs_sha256(root, persona):
        out.append("rerun_owed inputs_sha256 differs from the current evidence inputs")
    mrel = manifest_rel(persona)
    try:
        base_manifest = _base_show(root, mrel)
        plan_text = _base_show(root, owed["plan"])
    except ValueError as exc:
        return out + [f"rerun_owed cannot be bound: {exc}"]
    base_owed = _base_owed(base_manifest)
    if base_owed is not None and base_owed != owed:
        out.append("rerun_owed differs from the marker already on main (no change, no stacking)")
    if owed["plan"] == THIS_PLAN or owed["plan"] == f"docs/plans/PLAN-{written_by}.yaml":
        out.append("rerun_owed names the plan that wrote the records as its authority")
    if plan_text is None:
        return out + ["rerun_owed plan is absent at the merge-base"]
    try:
        plan = yaml.safe_load(plan_text)
        scope = {str(e.get("file")) for e in plan.get("scope") or [] if isinstance(e, dict)}
        bundled = plan.get("bundled_recommendations") or []
    except (yaml.YAMLError, AttributeError):
        return out + ["rerun_owed plan is unreadable at the merge-base"]
    if owed["rec"] not in bundled:
        out.append("rerun_owed plan does not bundle the rec")
    inputs, cases_dir = _evidence_inputs(persona, mrel)
    if not (scope & inputs or any(f.startswith(cases_dir) for f in scope)):
        out.append("rerun_owed plan scopes none of the persona's evidence inputs")
    if records_rel(persona) not in scope:
        out.append("rerun_owed plan scopes no records file")
    return out


def manifest_rel(persona: str) -> str:
    return guidance_eval.MANIFEST_HOME.replace("<persona>", persona)


def records_rel(persona: str) -> str:
    return RECORDS_HOME.replace("<persona>", persona)


def _base_owed(text: str | None) -> Any:
    try:
        doc = yaml.safe_load(text) if text else None
    except yaml.YAMLError:
        return None
    return doc.get("rerun_owed") if isinstance(doc, dict) else None


def _binding_problems(rec: Mapping[str, Any], contract: Mapping[str, Any], texts: Mapping[str, str], notes: str) -> list[str]:
    want = {
        "contract_version": contract.get("contract_version"),
        "rendered_prompt_sha256": contract.get("rendered_prompt_sha256"),
        "guidance_sha256": _sha(texts.get(rec["case"], "")),
        "notes_sha256": _sha(notes),
    }
    return [f"record {rec['case']} {k} differs from the current {k}" for k, v in want.items() if rec[k] != v]


def _failures(root: Path, persona: str, prefix: str) -> list[str]:
    try:
        manifest = guidance_eval.load_manifest(root, persona)
    except ValueError as exc:
        return [f"{prefix} {exc}"]
    slots = execute.guidance_slots(root, persona)
    if set(slots) != {manifest.slot}:
        return [f"{prefix} the contract declares slots {sorted(slots)} but the manifest evidences only {manifest.slot}"]
    path = records_path(root, persona)
    if not path.is_file():
        return [f"{prefix} no records file {records_rel(persona)}"]
    doc = load_records(path)
    out: list[str] = []
    written_by = doc["written_by"]
    if not (isinstance(written_by, str) and _SLUG.match(written_by)):
        out.append("written_by is missing or off ^[a-z0-9-]+$")
    records, discarded = doc["records"], doc["discarded"]
    if not isinstance(records, list) or not isinstance(discarded, list):
        return [f"{prefix} records and discarded must be lists"]
    names = {BASELINE, *(c.name for c in manifest.cases)}
    bad = [p for i, r in enumerate(records) for p in _record_problems(r, f"record {i}")]
    bad += [p for i, d in enumerate(discarded) for p in _discard_problems(d, f"discard {i}", names)]
    if bad:
        return [f"{prefix} {p}" for p in out + bad]
    cases = [r["case"] for r in records]
    if sorted(cases) != sorted(names):
        out.append(f"records hold cases {sorted(cases)} but exactly one per {sorted(names)} is required")
    if any([d["case"] for d in discarded].count(n) >= 2 for n in names):
        out.append("a case has two or more discards; the operator decides")
    out += guidance_eval.grade(manifest, {r["case"]: r["outcome"] for r in records})
    out += [
        f"record {r['case']} outcome differs from its check_title"
        for r in records
        if r["outcome"] != derive_outcome(r["check_title"])
    ]
    for key in ("check_run_id", "head_sha"):
        values = [r[key] for r in records]
        if len(set(values)) != len(values):
            out.append(f"{key} repeats across records")
    seen = [r["run_id"] for r in records]
    by_run = {r["run_id"]: r["case"] for r in records}
    if len(set(seen)) != len(seen) or any(by_run.get(d["run_id"], d["case"]) != d["case"] for d in discarded):
        out.append("run_id repeats across records or between a discard and another case")
    out += _staleness(root, persona, manifest, doc, records)
    return [f"{prefix} {p}" for p in out]


def _staleness(
    root: Path, persona: str, manifest: guidance_eval.Manifest, doc: Mapping[str, Any], records: list[Any]
) -> list[str]:
    contract = _contract(root, persona)
    texts = {c.name: guidance.case_file(root, persona, manifest.slot, c.name) for c in manifest.cases}
    notes = expected_notes(manifest)
    stale = [p for r in records for p in _binding_problems(r, contract, texts, notes)]
    out = [f"record {r['case']} role is not primary" for r in records if r["role"] != "primary"]
    if manifest.rerun_owed is None:
        return stale + out
    owed_lines = _owed_bound(root, persona, manifest, doc["written_by"])
    return owed_lines if owed_lines else []


def evidence_failures(root: Path, persona: str) -> list[str]:
    """Lines starting "guidance evidence: <persona>:", empty when the slots have fresh evidence; never raises."""
    prefix = f"guidance evidence: {persona}:"
    try:
        return _failures(root, persona, prefix)
    except (ValueError, OSError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        return [f"{prefix} {type(exc).__name__}: {exc}"]


def _telemetry_facts(telemetry: Any, contract: Mapping[str, Any], run_id: int, run_attempt: int) -> tuple[list[str], str, str]:
    batches = {b.get("table"): b.get("rows") or [] for b in telemetry.get("batches") or [] if isinstance(b, dict)}
    calls = [r for r in batches.get("telemetry_observations", []) if r.get("observation_type") == "model_call"]
    ok = [r for r in calls if r.get("outcome") == "success" and r.get("name") not in _IGNORED_ROLES]
    reasons: list[str] = []
    if any(r.get("name") != "primary" for r in ok):
        reasons.append("a successful row of a role other than primary")
    served = sorted({str(r.get("model")) for r in ok if r.get("name") == "primary" and r.get("model")})
    requested = sorted({str(r.get("requested_model")) for r in ok if r.get("name") == "primary" and r.get("requested_model")})
    if len(served) != 1:
        reasons.append(f"served model is not exactly one value ({served})")
    if len(requested) != 1:
        reasons.append(f"requested model is not exactly one value ({requested})")
    agents = batches.get("telemetry_agents", [])
    sessions = batches.get("telemetry_sessions", [])
    if not agents or any(str(r.get("version")) != str(contract.get("contract_version")) for r in agents):
        reasons.append("the run's contract_version differs from the contract's")
    if not agents or any(r.get("definition_sha256") != contract.get("rendered_prompt_sha256") for r in agents):
        reasons.append("the run's prompt hash differs from the contract's")
    if not agents or any(str(r.get("workflow_run_id")) != str(run_id) for r in agents):
        reasons.append("the telemetry host run_id is absent or unequal to --run-id")
    if not sessions or any(str(r.get("execution_attempt")) != str(run_attempt) for r in sessions):
        reasons.append("the telemetry host run_attempt is absent or unequal to --run-attempt")
    return reasons, (served[0] if len(served) == 1 else ""), (requested[0] if len(requested) == 1 else "")


def _save(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(dict(doc), sort_keys=False), encoding="utf-8")


def record(root: Path, args: argparse.Namespace) -> tuple[int, str]:
    persona, case, title = args.persona, args.case, args.title
    manifest = guidance_eval.load_manifest(root, persona)
    texts = {c.name: guidance.case_file(root, persona, manifest.slot, c.name) for c in manifest.cases}
    if case != BASELINE and case not in texts:
        return 2, f"ERROR: case {case!r} is not in the manifest"
    path = records_path(root, persona)
    doc: dict[str, Any] = (
        load_records(path) if path.is_file() else {"written_by": args.written_by, "records": [], "discarded": []}
    )
    if doc["written_by"] != args.written_by:
        return 2, f"ERROR: the records file names written_by {doc['written_by']!r}"
    if any(r["case"] == case for r in doc["records"]):
        return 2, f"ERROR: a record for {case} exists"
    contract = _contract(root, persona)
    prepared = json.loads((Path(args.prepared) / f"input-{persona}.json").read_text(encoding="utf-8"))
    result = json.loads((Path(args.results) / f"{persona}.json").read_text(encoding="utf-8"))
    telemetry_file = Path(args.results) / f"telemetry-{persona}.json"
    reasons: list[str] = []
    if telemetry_file.is_file():
        reasons, served, requested = _telemetry_facts(
            json.loads(telemetry_file.read_text(encoding="utf-8")), contract, args.run_id, args.run_attempt
        )
    else:
        reasons, served, requested = ["no telemetry file"], "", ""
    if FALLBACK_MARK in title:
        reasons.append("the check title carries a fallback-role mark")
    if prepared.get("notes") != expected_notes(manifest):
        reasons.append("the run's notes differ from the expected notes")
    if prepared.get(manifest.slot) != texts.get(case, ""):
        reasons.append("the run's guidance value differs from the case text")
    digest = result.get("image_digest") if isinstance(result, dict) else None
    if not (isinstance(digest, str) and _DIGEST.match(digest)):
        reasons.append("the results file has no image_digest")
    outcome = derive_outcome(title)
    if reasons:
        doc["discarded"].append({"case": case, "run_id": args.run_id, "outcome": outcome, "reason": "; ".join(reasons)})
        _save(path, doc)
        return 2, f"INVALID: {'; '.join(reasons)}"
    from scripts.personas import post_verdict  # noqa: PLC0415 - kept out of the check's import closure

    if outcome != derive_outcome(post_verdict.judge(root, persona, result, True)[1]):
        return 2, f"MISMATCH: title outcome {outcome!r} differs from the results artifact"
    rec = {
        "case": case, "outcome": outcome, "check_title": title, "check_run_id": args.check_run_id, "run_id": args.run_id,
        "run_attempt": args.run_attempt, "main_sha": args.main_sha, "head_sha": args.head_sha, "role": "primary",
        "model_id": served, "requested_model": requested, "image_digest": digest,
        "contract_version": contract.get("contract_version"), "rendered_prompt_sha256": contract.get("rendered_prompt_sha256"),
        "guidance_sha256": _sha(texts.get(case, "")), "notes_sha256": _sha(expected_notes(manifest)),
        "run_date": dt.date.today().isoformat(),
    }  # fmt: skip
    problems = _record_problems(rec, "record")
    if problems:
        return 2, f"ERROR: {'; '.join(problems)}"
    doc["records"].append(rec)
    _save(path, doc)
    return 0, f"RECORDED {case}"


def main(argv: Sequence[str] | None = None, *, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(prog="guidance_evidence")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record")
    for name in ("persona", "case", "title", "written-by", "prepared", "results"):
        rec.add_argument(f"--{name}", required=True)
    for name in ("run-id", "run-attempt", "check-run-id"):
        rec.add_argument(f"--{name}", required=True, type=int)
    for name in ("main-sha", "head-sha"):
        rec.add_argument(f"--{name}", required=True)
    args = parser.parse_args(argv)
    try:
        code, message = record(root or Path.cwd(), args)
    except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        code, message = 2, f"ERROR: {type(exc).__name__}: {exc}"
    print(message)
    return code


if __name__ == "__main__":
    sys.exit(main())
