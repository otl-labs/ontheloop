#!/usr/bin/env python3
"""CI RCA watchdog (rec-5504): every watched red on main and every CI RCA run must end with an outcome, else an issue.

Deterministic: no LLM, no vendor CLI, no AWS, no portal. Imports only stdlib, yaml and scripts.repo_identity (the
workflows install pyyaml alone). Every GitHub call goes through one injectable `gh api` helper (REST only).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from scripts.repo_identity import full_name

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_FILE = "ci-rca.yml"
LABEL = "ci-rca-watchdog"
BOT = "github-actions[bot]"
TITLE = "CI RCA watchdog: reds on main without an outcome"
GRACE_MINUTES = 60
SLACK_HOURS = 6
MAX_PAGES = 10
PER_PAGE = 100
LIMIT = 65536
SWEEP_STATUSES = ("failure", "cancelled", "timed_out", "startup_failure", "action_required")
BAD_STEP = ("failure", "cancelled", "timed_out")
_TITLE = re.compile(r"^CI RCA for run (\d+)$")
_UNTITLED = "CI RCA"
HEADER = (
    "Reds on main (or CI RCA runs) that ended without an outcome. For each listed red, first check for an existing "
    "ci_rca rec (the fallback may have filed one); if none, have a maintainer re-run CI RCA for the watched run through "
    "its workflow_dispatch run_id input (Decision 74) or file it through the portal; then close this issue. The next "
    "orphan reopens it. A re-run attempt of an already-listed CI RCA run is not listed again; a fresh dispatch gets a "
    "new id."
)

Gh = Callable[..., Any]


class GhError(RuntimeError):
    pass


def gh_api(method: str, path: str, params: dict[str, str] | None = None, body: dict | None = None) -> Any:
    cmd = ["gh", "api", "-X", method, path]
    for key, value in (params or {}).items():
        cmd += ["-f", f"{key}={value}"]
    if body is not None:
        cmd += ["--input", "-"]
    done = subprocess.run(
        cmd,
        input=json.dumps(body) if body is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if done.returncode != 0:
        raise GhError(f"gh api {method} {path} failed: {done.stderr.strip()[:300]}")
    return json.loads(done.stdout) if done.stdout.strip() else None


def paged(gh: Gh, path: str, params: dict[str, str], key: str | None = None) -> list[dict]:
    items: list[dict] = []
    for page in range(1, MAX_PAGES + 1):
        data = gh("GET", path, {**params, "per_page": str(PER_PAGE), "page": str(page)})
        chunk = (data or {}).get(key, []) if key else (data or [])
        items.extend(chunk)
        if len(chunk) < PER_PAGE:
            return items
    raise GhError(f"truncated read: {path} filled {MAX_PAGES} pages of {PER_PAGE}")


def watched_names(workflow_text: str) -> list[str]:
    doc = yaml.safe_load(workflow_text) or {}
    trigger = doc.get(True) or doc.get("on") or {}
    return list((trigger.get("workflow_run") or {}).get("workflows") or [])


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clean(text: str) -> str:
    return re.sub(r"[\r\n|<>]+", " ", text).strip()[:100]


@dataclass
class Alarm:
    kind: str
    key: int
    workflow: str
    red_url: str
    rca_url: str
    step: str

    @property
    def marker(self) -> str:
        return f"<!-- {LABEL}:{self.kind}:{self.key} -->"

    @property
    def line(self) -> str:
        return (
            f"- {self.kind}: {self.workflow} run {self.red_url} | CI RCA run {self.rca_url} | "
            f"failed step: {self.step} {self.marker}"
        )


class Watchdog:
    def __init__(
        self, gh: Gh, repo: str, now: datetime, lookback_hours: int, self_run_id: int | None, self_result: str | None
    ) -> None:
        self.gh, self.repo, self.now = gh, repo, now
        self.lookback = lookback_hours
        self.since = now - timedelta(hours=lookback_hours)
        self.self_run_id, self.self_result = self_run_id, self_result
        self.reds = self.ok = self.unlinkable = 0
        self.alarms: dict[str, Alarm] = {}

    def _run(self, run: dict) -> dict:
        if self.self_run_id is not None and run.get("id") == self.self_run_id:
            return {**run, "status": "completed", "conclusion": self.self_result}
        return run

    def _jobs(self, run_id: int) -> list[dict]:
        return paged(self.gh, f"repos/{self.repo}/actions/runs/{run_id}/jobs", {}, "jobs")

    @staticmethod
    def _step(jobs: list[dict]) -> str:
        ordered = sorted(jobs, key=lambda j: j.get("name") != "rca")
        for job in ordered:
            for step in job.get("steps") or []:
                if step.get("conclusion") in BAD_STEP:
                    return _clean(f"{step.get('name')}")
        for job in ordered:
            if job.get("conclusion") in BAD_STEP:
                return _clean(f"job {job.get('name')}: {job.get('conclusion')}")
        return "none recorded"

    def _judge(self, run: dict) -> tuple[str, str] | None:
        """Kind and failed step for a CI RCA run, or None when it ended with an outcome."""
        run = self._run(run)
        if run.get("status") != "completed":
            return "rca_stuck", "none recorded"
        jobs = self._jobs(run["id"])
        rca = next((j for j in jobs if j.get("name") == "rca"), None)
        conclusion = rca.get("conclusion") if rca else run.get("conclusion")
        if conclusion == "success":
            return None if run.get("conclusion") == "success" else ("watchdog_failed", self._step(jobs))
        if conclusion == "skipped":
            return "rca_skipped", "none recorded"
        return "rca_failed", self._step(jobs)

    def _add(self, alarm: Alarm, prefer: bool) -> None:
        if prefer or alarm.marker not in self.alarms:
            self.alarms[alarm.marker] = alarm

    def _watched_reds(self, names: list[str]) -> list[dict]:
        floor = _iso(self.since - timedelta(hours=SLACK_HOURS))
        runs = paged(
            self.gh,
            f"repos/{self.repo}/actions/runs",
            {"branch": "main", "status": "failure", "created": f">={floor}"},
            "workflow_runs",
        )
        grace = self.now - timedelta(minutes=GRACE_MINUTES)
        return [
            r
            for r in runs
            if r.get("name") in names
            and (r.get("head_repository") or {}).get("full_name") == self.repo
            and _ts(r["updated_at"]) <= grace
        ]

    def join(self, names: list[str]) -> None:
        for red in self._watched_reds(names):
            self.reds += 1
            start = red.get("run_started_at") or red["created_at"]
            end = _iso(_ts(red["updated_at"]) + timedelta(minutes=30))
            window = paged(
                self.gh,
                f"repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}/runs",
                {"created": f"{_iso(_ts(start))}..{end}"},
                "workflow_runs",
            )
            linked = [r for r in window if (m := _TITLE.match(r.get("display_title") or "")) and int(m[1]) == red["id"]]
            if not linked:
                if any((r.get("display_title") or "") == _UNTITLED for r in window):
                    self.unlinkable += 1
                else:
                    self._add(Alarm("no_rca_run", red["id"], red["name"], red["html_url"], "none", "none recorded"), True)
                continue
            newest = max(linked, key=lambda r: (r["created_at"], r.get("run_attempt", 1)))
            verdict = self._judge(newest)
            if verdict is None:
                self.ok += 1
                continue
            self._add(Alarm(verdict[0], newest["id"], red["name"], red["html_url"], newest["html_url"], verdict[1]), True)

    def sweep(self) -> None:
        seen: dict[int, dict] = {}
        for status in SWEEP_STATUSES:
            for run in paged(
                self.gh,
                f"repos/{self.repo}/actions/workflows/{WORKFLOW_FILE}/runs",
                {"status": status, "created": f">={_iso(self.since)}"},
                "workflow_runs",
            ):
                seen[run["id"]] = run
        if self.self_run_id is not None and self.self_result != "success":
            seen.setdefault(self.self_run_id, {"id": self.self_run_id, "html_url": self._self_url()})
        for run in seen.values():
            if run["id"] == self.self_run_id:
                run = {**run, "display_title": self._self_title(run)}
            named = _TITLE.match(run.get("display_title") or "")
            if run.get("conclusion") == "cancelled" and not self._jobs(run["id"]):
                if not named or (self.gh("GET", f"repos/{self.repo}/actions/runs/{named[1]}") or {}).get("conclusion") != (
                    "failure"
                ):
                    continue
            red_url = f"https://github.com/{self.repo}/actions/runs/{named[1]}" if named else "unknown"
            if verdict := self._judge(run):
                self._add(Alarm(verdict[0], run["id"], "unknown", red_url, run["html_url"], verdict[1]), False)

    def _self_url(self) -> str:
        return f"https://github.com/{self.repo}/actions/runs/{self.self_run_id}"

    def _self_title(self, run: dict) -> str:
        data = self.gh("GET", f"repos/{self.repo}/actions/runs/{run['id']}") or {}
        return data.get("display_title") or ""


def known_markers(gh: Gh, repo: str, since: datetime) -> set[str]:
    """Markers carried by github-actions[bot] posts only, so a public commenter cannot suppress an alarm."""
    found: set[str] = set()
    pattern = re.compile(rf"<!-- {LABEL}:\w+:\d+ -->")
    issues = paged(gh, f"repos/{repo}/issues", {"labels": LABEL, "state": "all", "since": _iso(since)})
    for issue in issues:
        if (issue.get("user") or {}).get("login") == BOT:
            found.update(pattern.findall(issue.get("body") or ""))
        comments = paged(gh, f"repos/{repo}/issues/{issue['number']}/comments", {"since": _iso(since)})
        for comment in comments:
            if (comment.get("user") or {}).get("login") == BOT:
                found.update(pattern.findall(comment.get("body") or ""))
    return found


def _chunks(lines: list[str], head: str = "") -> list[str]:
    bodies, current = [], head
    for line in lines:
        if current and len(current) + len(line) + 2 > LIMIT:
            bodies.append(current)
            current = ""
        current = f"{current}\n\n{line}" if current else line
    bodies.append(current)
    return bodies


def _bot_issues(gh: Gh, repo: str) -> list[dict]:
    items = paged(gh, f"repos/{repo}/issues", {"labels": LABEL, "state": "all"})
    mine = [i for i in items if (i.get("user") or {}).get("login") == BOT and "pull_request" not in i]
    return sorted(mine, key=lambda i: i["number"])


def _ensure_label(gh: Gh, repo: str) -> None:
    try:
        gh("GET", f"repos/{repo}/labels/{LABEL}")
    except GhError as exc:
        if "404" not in str(exc):
            raise
        gh("POST", f"repos/{repo}/labels", None, {"name": LABEL, "color": "b60205", "description": "CI RCA watchdog"})


def _comment(gh: Gh, repo: str, number: int, lines: list[str]) -> None:
    for body in _chunks(lines):
        gh("POST", f"repos/{repo}/issues/{number}/comments", None, {"body": body})


def post(gh: Gh, repo: str, lines: list[str]) -> None:
    existing = _bot_issues(gh, repo)
    if existing:
        target = existing[0]
        if target.get("state") == "closed":
            gh("PATCH", f"repos/{repo}/issues/{target['number']}", None, {"state": "open"})
        _comment(gh, repo, target["number"], lines)
        return
    _ensure_label(gh, repo)
    first, *rest = _chunks(lines, HEADER)
    made = gh("POST", f"repos/{repo}/issues", None, {"title": TITLE, "body": first, "labels": [LABEL]})
    number = made["number"]
    lower = [i for i in _bot_issues(gh, repo) if i["number"] < number]
    if lower:
        _comment(gh, repo, lower[0]["number"], lines)
        for reason in ("duplicate", "not_planned"):
            try:
                gh("PATCH", f"repos/{repo}/issues/{number}", None, {"state": "closed", "state_reason": reason})
                break
            except GhError:
                if reason == "not_planned":
                    raise
        return
    for body in rest:
        gh("POST", f"repos/{repo}/issues/{number}/comments", None, {"body": body})


def run(
    gh: Gh,
    *,
    repo: str,
    now: datetime,
    lookback_hours: int,
    self_run_id: int | None = None,
    self_result: str | None = None,
    dry_run: bool = False,
    workflow_text: str | None = None,
) -> tuple[str, list[str]]:
    text = workflow_text if workflow_text is not None else (ROOT / ".github/workflows" / WORKFLOW_FILE).read_text("utf-8")
    dog = Watchdog(gh, repo, now, lookback_hours, self_run_id, self_result)
    dog.join(watched_names(text))
    dog.sweep()
    alarms = sorted(dog.alarms.values(), key=lambda a: (a.kind, a.key))
    known = known_markers(gh, repo, dog.since - timedelta(hours=48))
    fresh = [a.line for a in alarms if a.marker not in known]
    if fresh and not dry_run:
        post(gh, repo, fresh)
    summary = f"watchdog: reds={dog.reds} ok={dog.ok} unlinkable={dog.unlinkable} alarms={len(alarms)} new={len(fresh)}"
    return summary, [a.line for a in alarms]


def main(argv: list[str] | None = None, gh: Gh = gh_api) -> int:
    parser = argparse.ArgumentParser(description="Reconcile watched reds and CI RCA runs against their outcomes")
    parser.add_argument("--lookback-hours", type=int, default=24, choices=range(1, 721), metavar="1..720")
    parser.add_argument("--self-run-id", type=int)
    parser.add_argument("--self-result", choices=["success", "failure", "cancelled"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary, lines = run(
            gh,
            repo=full_name(),
            now=datetime.now(UTC),
            lookback_hours=args.lookback_hours,
            self_run_id=args.self_run_id,
            self_result=args.self_result,
            dry_run=args.dry_run,
        )
    except GhError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    print(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover - module entry point
    sys.exit(main())
