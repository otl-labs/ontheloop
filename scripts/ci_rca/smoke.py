"""Verbs for the post-merge CI RCA smoke (rec-5493): select, stage, outputs, verify.

select runs in a job that has only pyyaml and networkx installed, so module-level imports here stay on the
standard library, scripts.ci_rca.coverage and scripts.ci_rca.filing. No verb touches the network.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import subprocess
import sys
from pathlib import Path

from scripts.ci_rca import coverage, filing

SMOKE_WORKFLOW = ".github/workflows/ci-rca-smoke.yml"
_STAGE_TARGETS = ("/tmp/ci-rca-failed.log", "/tmp/ci-rca-jobs.json", "/tmp/ci-rca-log-evidence.json")


def _warn(message: str) -> None:
    print(f"::warning::{message}", file=sys.stderr)


def _fail(message: str) -> int:
    print(f"::error::{message}", file=sys.stderr)
    return 1


def _changed_files(before: str, after: str) -> list[str] | None:
    if not before or set(before) == {"0"}:
        _warn("CI RCA smoke select: no usable BEFORE sha; running the smoke")
        return None
    try:
        proc = subprocess.run(
            ["git", "diff", "--name-only", before, after],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
    except OSError as exc:
        _warn(f"CI RCA smoke select: git failed ({exc}); running the smoke")
        return None
    if proc.returncode != 0:
        _warn(f"CI RCA smoke select: git diff {before[:12]}..{after[:12]} failed; running the smoke")
        return None
    return [line for line in proc.stdout.splitlines() if line]


def _meets(changed: list[str], inputs: set[str]) -> bool:
    for path in changed:
        for item in inputs:
            if path == item or (item.endswith("/") and path.startswith(item)):
                return True
    return False


def select(before: str, after: str, changed: list[str] | None) -> str:
    files = changed if changed is not None else _changed_files(before, after)
    if files is None:
        return "run=true"
    try:
        inputs = coverage.load().workflow_inputs(SMOKE_WORKFLOW)
    except Exception as exc:  # noqa: BLE001
        _warn(f"CI RCA smoke select: input derivation failed ({type(exc).__name__}: {exc}); running the smoke")
        return "run=true"
    return f"run={'true' if _meets(files, inputs) else 'false'}"


def stage(sources: list[str]) -> int:
    if len(sources) != len(_STAGE_TARGETS):
        return _fail(f"CI RCA smoke stage: expected {len(_STAGE_TARGETS)} sources, got {len(sources)}")
    for src in sources:
        if not Path(src).is_file():
            return _fail(f"CI RCA smoke stage: fixture {src} is missing")
    for src, dest in zip(sources, _STAGE_TARGETS, strict=True):
        shutil.copyfile(src, dest)
    return 0


def _bundle_shas(bundles_dir: Path) -> list[str]:
    shas: list[str] = []
    for path in sorted(glob.glob(str(bundles_dir / "*.json"))):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        sha = data.get("sha256") if isinstance(data, dict) else None
        shas.append(sha if isinstance(sha, str) and sha else Path(path).stem)
    return shas


def outputs(out_file: Path, bundles_dir: Path) -> list[str]:
    values: dict[str, str] = {"BUNDLE_SHA": "", "BUNDLE_S3_URI": "", "BUNDLE_PATH": "", "BUNDLE_LOCAL": ""}
    try:
        lines = out_file.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []
    for key in values:
        for line in lines:
            if line.startswith(f"{key}="):
                values[key] = line.split("=", 1)[1]
                break
    absent = not values["BUNDLE_SHA"] and not glob.glob(str(bundles_dir / "*.json"))
    result = [f"{k.lower()}={v}" for k, v in values.items()]
    result.append(f"bundle_absent={'true' if absent else 'false'}")
    return result


def _sink_records(path: Path) -> list[dict]:
    records: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return records
    for line in lines:
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        record = data.get("record")
        if not (data.get("action") == "file_ops" and data.get("table") == "ops_recommendations" and isinstance(record, dict)):
            continue
        if record.get("source") != "ci_rca":
            continue
        raw = record.get("context_v2_json")
        try:
            ctx = json.loads(raw) if isinstance(raw, str) else raw
        except ValueError:
            continue
        if isinstance(ctx, dict):
            records.append(ctx)
    return records


def verify(smoke_id: str, bundles_dir: Path, sink: Path, fallback_sink: Path, agent_output: Path) -> int:
    shas = _bundle_shas(bundles_dir)
    if not shas:
        return _fail("CI RCA smoke verify: the evidence generator emitted no bundle")
    agent_ctx = _sink_records(sink)
    if not any((ctx.get("evidence_bundle_ref") or {}).get("sha256") in shas for ctx in agent_ctx):
        return _fail(
            "CI RCA smoke verify: no agent filing in the sink cites an emitted bundle sha "
            "(evidence_bundle_ref is optional in the agent's context, so the agent left it out or named another bundle)"
        )
    if not any(ctx.get("rca_confidence") == "undetermined" for ctx in _sink_records(fallback_sink)):
        return _fail("CI RCA smoke verify: the fallback sink holds no undetermined filing")
    if not any(rec_id == smoke_id for rec_id, _ in filing.extract_filed_recs(agent_output)):
        return _fail(f"CI RCA smoke verify: no FILED: {smoke_id} marker in the agent output")
    print(f"CI RCA smoke verify: ok ({len(shas)} bundle(s), {len(agent_ctx)} agent filing(s))")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.ci_rca.smoke")
    sub = parser.add_subparsers(dest="command", required=True)
    p_sel = sub.add_parser("select")
    p_sel.add_argument("--before", default="")
    p_sel.add_argument("--after", default="HEAD")
    p_sel.add_argument("--changed", nargs="*", default=None)
    sub.add_parser("stage").add_argument("sources", nargs="*")
    p_out = sub.add_parser("outputs")
    p_out.add_argument("out_file", type=Path)
    p_out.add_argument("bundles_dir", type=Path)
    p_ver = sub.add_parser("verify")
    p_ver.add_argument("--smoke-id", required=True)
    p_ver.add_argument("--bundles-dir", type=Path, required=True)
    p_ver.add_argument("sink", type=Path)
    p_ver.add_argument("fallback_sink", type=Path)
    p_ver.add_argument("agent_output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "select":
        print(select(args.before, args.after, args.changed))
        return 0
    if args.command == "stage":
        return stage(args.sources)
    if args.command == "outputs":
        print("\n".join(outputs(args.out_file, args.bundles_dir)))
        return 0
    return verify(args.smoke_id, args.bundles_dir, args.sink, args.fallback_sink, args.agent_output)


if __name__ == "__main__":
    sys.exit(main())
