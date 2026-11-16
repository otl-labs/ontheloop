#!/usr/bin/env python3
"""Persona dispatch gate (Decision 205 cl.1; PLAN-persona-trigger-policy).

Stops a workflow_dispatch that is not a maintainer's own user dispatch from reaching an LLM step.
Module-scope imports are stdlib only: the reconcile and sandbox apply jobs run plain python3. yaml is
imported inside the --own-failure path only (ci-rca, which runs under bin/venv-python).

Exit 0: admitted or not a dispatch (refused=false). Exit 1: deliberate refusal (refused=true, fail
closed, including any unreadable input). Exit 2: unexpected exception (writes nothing; the marker is
GATE_ERROR, never the refusal marker).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

REFUSED = "PERSONA_DISPATCH_REFUSED"
ADMITTED = "PERSONA_DISPATCH_ADMITTED"
ERROR = "PERSONA_DISPATCH_GATE_ERROR"
BOT_ACTOR = "github-actions[bot]"


class Refusal(Exception):
    pass


def _load_json(path: str | None, what: str) -> Any:
    if not path:
        raise Refusal(f"{what} not supplied")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Refusal(f"{what} unreadable") from exc


def _workflow_names(workflow_file: str) -> tuple[str, list[str]]:
    import yaml

    try:
        doc = yaml.safe_load(Path(workflow_file).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Refusal(f"watched-from workflow {workflow_file} unreadable") from exc
    if not isinstance(doc, dict):
        raise Refusal("watched-from workflow is not a mapping")
    on = doc.get("on", doc.get(True))
    wr = on.get("workflow_run") if isinstance(on, dict) else None
    names = wr.get("workflows") if isinstance(wr, dict) else None
    if not isinstance(names, list) or not names:
        raise Refusal("watched-from workflow lists no workflow_run workflows")
    return str(doc.get("name", "")), [str(n) for n in names]


def _run_workflow_name(run: dict[str, Any]) -> str:
    import yaml

    path = str(run.get("path") or "").split("@", 1)[0]
    if not path.startswith(".github/workflows/"):
        raise Refusal("run path is not a workflow file")
    try:
        doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Refusal("run's workflow file unreadable") from exc
    if not isinstance(doc, dict) or not doc.get("name"):
        raise Refusal("run's workflow file has no name")
    return str(doc["name"])


def _failed(entry: dict[str, Any]) -> bool:
    return entry.get("conclusion") == "failure"


def _jobs_show_failure(jobs: Any) -> bool:
    items = jobs.get("jobs") if isinstance(jobs, dict) else jobs
    if not isinstance(items, list):
        raise Refusal("jobs-json has no jobs list")
    for job in items:
        if _failed(job) or any(_failed(s) for s in job.get("steps") or []):
            return True
    return False


def _own_failure(args: argparse.Namespace, event: dict[str, Any]) -> str:
    if os.environ.get("GITHUB_ACTOR") != BOT_ACTOR:
        raise Refusal("own-failure admission needs the workflow token actor")
    run = _load_json(args.run_json, "run-json")
    if not isinstance(run, dict):
        raise Refusal("run-json is not an object")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_repo = (run.get("repository") or {}).get("full_name")
    if not repo or run_repo != repo:
        raise Refusal("run is not of this repository")
    default = (event.get("repository") or {}).get("default_branch")
    if not default or run.get("head_branch") != default:
        raise Refusal("run is not on the default branch")
    if not args.watched_from:
        raise Refusal("watched-from not supplied")
    _, watched = _workflow_names(args.watched_from)
    if _run_workflow_name(run) not in watched:
        raise Refusal("run is of an unwatched workflow")
    if run.get("conclusion") == "failure":
        return "own-failure"
    if run.get("status") == "in_progress" and _jobs_show_failure(_load_json(args.jobs_json, "jobs-json")):
        return "own-failure"
    raise Refusal("run has not failed")


def decide(args: argparse.Namespace) -> str | None:
    """Return the admission kind, None for a non-dispatch event; raise Refusal otherwise."""
    if os.environ.get("GITHUB_EVENT_NAME") != "workflow_dispatch":
        return None
    event = _load_json(os.environ.get("GITHUB_EVENT_PATH"), "event payload")
    if not isinstance(event, dict):
        raise Refusal("event payload is not an object")
    triggering = os.environ.get("GITHUB_TRIGGERING_ACTOR", "")
    sender_type = (event.get("sender") or {}).get("type")
    if sender_type == "User" and triggering and not triggering.endswith("[bot]"):
        return "user"
    if args.own_failure:
        return _own_failure(args, event)
    raise Refusal(f"dispatch by sender.type={sender_type}, triggering actor {triggering or 'unknown'}")


def _emit(refused: bool, line: str, *, error: bool = False) -> None:
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"refused={'true' if refused else 'false'}\n")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and refused:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    print(line, file=sys.stderr if refused else sys.stdout)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--own-failure", action="store_true")
    parser.add_argument("--run-json")
    parser.add_argument("--jobs-json")
    parser.add_argument("--watched-from")
    args = parser.parse_args(argv)
    try:
        kind = decide(args)
    except Refusal as exc:
        _emit(True, f"{REFUSED}: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - not a refusal: ci-rca must still file a gate crash
        print(f"{ERROR}: {type(exc).__name__}", file=sys.stderr)
        return 2
    if kind is None:
        _emit(False, "persona dispatch gate: not a workflow_dispatch event")
    else:
        _emit(False, f"{ADMITTED}: {kind} {os.environ.get('GITHUB_ACTOR', '')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
