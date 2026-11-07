"""Arrival oracle for telemetry_sessions (Decision 250 cl.5, rec-4940).

Every distinct Claude-Session trailer on a first-parent squash commit merged to main more than the declared SLA ago must
resolve to a captured root session (set inclusion on harness_session_key). A merged session that never reached the
warehouse is red; a source that cannot be read is unreadable (red, never a pass); an empty denominator is skipped, never
green. The SLA is read from the data-modeling standard's arrival_expectations declaration, never restated here. Output
is counts and commit shas only (Decision 101). Imports only the standard library, yaml, read_route and completeness.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from scripts.telemetry_verify import completeness

MODULE = "scripts/telemetry_verify/session_presence.py"
STANDARD = "docs/contracts/data-modeling-standard.yaml"
CONFIG = "config/telemetry/arrival.yaml"
TABLE = "telemetry_sessions"
DEFAULT_REPO = "otl-labs/ontheloop"
_PARAMS = {"judge_days", "window_days", "max_lookback_days", "max_pr_lookups", "collapse_min_commits"}
_LISTS = {"lost_windows", "excused_sessions"}
_SLA = re.compile(r"^([1-9][0-9]*)([mhd])$")
_UNITS = {"m": 60, "h": 3600, "d": 86400}
_TRAILER = re.compile(r"^Claude-Session:[ \t]*(\S+)[ \t]*$", re.MULTILINE)
_PR = re.compile(r"\(#([0-9]+)\)\s*$")
_LOST_OWNER = re.compile(r"^(rec-[0-9]{4,}|Decision [0-9]+)$")
_EXCUSED_OWNER = re.compile(r"^rec-[0-9]{4,}$")
_SEP = "\x1f"
_END = "\x1e"


class Unreadable(Exception):
    """A source or expectation the oracle cannot judge; the class name alone is reported."""


@dataclass(frozen=True)
class Commit:
    sha: str
    committed_at: datetime
    subject: str
    body: str


@dataclass
class ArrivalResult:
    status: str = "skipped"
    judged: int = 0
    captured: int = 0
    missing: int = 0
    lost_window: int = 0
    excused: int = 0
    pending: int = 0
    untrailered: int = 0
    windows_read: int = 0
    pr_lookups: int = 0
    pr_lookup_failed: int = 0
    elapsed_s: float = 0.0
    cause: str = ""
    missing_shas: list[str] = field(default_factory=list)
    missing_day: str = ""

    def exit_code(self) -> int:
        return 0 if self.status in ("green", "skipped") else 1


def _utc(text: Any) -> datetime:
    return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)


def parse_sla(value: Any) -> timedelta:
    match = _SLA.match(str(value))
    if match is None:
        raise Unreadable("BadSla")
    return timedelta(seconds=int(match.group(1)) * _UNITS[match.group(2)])


def check_expectation(expectation: Any) -> timedelta:
    ok = isinstance(expectation, Mapping) and expectation.get("kind") == "enforced"
    check = expectation.get("check") if isinstance(expectation, Mapping) else None
    if not ok or not isinstance(check, Mapping) or check.get("module") != MODULE:
        raise Unreadable("ExpectationMismatch")
    return parse_sla(expectation.get("sla"))


def check_config(config: Any) -> list[tuple[datetime | None, datetime, str]]:
    if not isinstance(config, Mapping) or set(config) - _PARAMS - _LISTS:
        raise Unreadable("BadConfig")
    if any(not isinstance(config.get(k), int) or isinstance(config.get(k), bool) or config[k] < 1 for k in _PARAMS):
        raise Unreadable("BadConfig")
    windows: list[tuple[datetime | None, datetime, str]] = []
    try:
        for row in config.get("lost_windows") or []:
            if set(row) - {"from", "to", "owner", "reason"} or not _LOST_OWNER.match(str(row["owner"])):
                raise Unreadable("BadConfig")
            start = _utc(row["from"]) if "from" in row else None
            end = _utc(row["to"])
            if start is not None and end <= start:
                raise Unreadable("BadConfig")
            windows.append((start, end, str(row["owner"])))
        for row in config.get("excused_sessions") or []:
            if set(row) != {"key", "owner"} or not _EXCUSED_OWNER.match(str(row["owner"])) or not row["key"]:
                raise Unreadable("BadConfig")
    except (TypeError, KeyError, ValueError, AttributeError) as exc:
        raise Unreadable("BadConfig") from exc
    return windows


def _session_keys(commit: Commit) -> list[str]:
    keys = (completeness.normalise_key(url.rstrip("/").rsplit("/", 1)[-1]) for url in _TRAILER.findall(commit.body))
    return [k for k in keys if k]


def _in_window(created: datetime, windows: list[tuple[datetime | None, datetime, str]]) -> bool:
    return any((start is None or created >= start) and created < end for start, end, _ in windows)


def _walk(
    roots_for: Callable[[str, str], list[Mapping[str, Any]]],
    remaining: set[str],
    newest: date,
    cfg: Mapping[str, Any],
    r: ArrivalResult,
) -> None:
    day_to, floor = newest, newest - timedelta(days=cfg["max_lookback_days"])
    while remaining and day_to >= floor:
        day_from = day_to - timedelta(days=cfg["window_days"] - 1)
        for attempt in (0, 1):
            try:
                rows = roots_for(day_from.isoformat(), day_to.isoformat())
                break
            except Exception as exc:  # noqa: BLE001 -- the class alone is reported
                if attempt:
                    raise Unreadable(type(exc).__name__) from exc
        r.windows_read += 1
        found = {completeness.normalise_key(row.get("harness_session_key")) for row in rows}
        remaining -= found
        day_to = day_from - timedelta(days=1)


def _judge(
    commits: list[Commit] | Callable[[], list[Commit]],
    roots_for: Callable[[str, str], list[Mapping[str, Any]]],
    now: datetime,
    expectation: Any,
    config: Mapping[str, Any],
    pr_created_at: Callable[[int], datetime],
    r: ArrivalResult,
) -> None:
    sla = check_expectation(expectation)
    windows = check_config(config)
    try:
        listed = commits() if callable(commits) else commits
    except Exception as exc:  # noqa: BLE001
        raise Unreadable(type(exc).__name__) from exc
    start, end = now - timedelta(days=config["judge_days"]), now - sla
    first: dict[str, Commit] = {}
    pending_set: set[str] = set()
    for commit in sorted(listed, key=lambda c: c.committed_at):
        keys = _session_keys(commit)
        if commit.committed_at > end:
            pending_set.update(keys)
        if not start <= commit.committed_at <= end:
            continue
        if not keys:
            r.untrailered += 1
        for key in keys:
            first.setdefault(key, commit)
    r.pending = len(pending_set - set(first))
    r.judged = len(first)
    if not first:
        if r.untrailered >= config["collapse_min_commits"]:
            raise Unreadable("DenominatorCollapse")
        r.status = "skipped"
        return
    remaining = set(first)
    _walk(roots_for, remaining, max(c.committed_at for c in first.values()).date(), config, r)
    r.captured = r.judged - len(remaining)
    excused = {row["key"] for row in config.get("excused_sessions") or []}
    for key in sorted(remaining, key=lambda k: first[k].committed_at):
        commit = first[key]
        if key in excused:
            r.excused += 1
            continue
        number = _PR.search(commit.subject) if windows else None
        if number and r.pr_lookups < config["max_pr_lookups"]:
            r.pr_lookups += 1
            try:
                if _in_window(pr_created_at(int(number.group(1))), windows):
                    r.lost_window += 1
                    continue
            except Exception:  # noqa: BLE001 -- a failed lookup leaves the key missing
                r.pr_lookup_failed += 1
        r.missing += 1
        r.missing_shas.append(commit.sha)
        r.missing_day = r.missing_day or commit.committed_at.date().isoformat()
    r.status = "red" if r.missing else "green"


def judge(
    commits: list[Commit] | Callable[[], list[Commit]],
    roots_for: Callable[[str, str], list[Mapping[str, Any]]],
    now: datetime,
    expectation: Any,
    config: Any,
    pr_created_at: Callable[[int], datetime],
) -> ArrivalResult:
    began = time.monotonic()
    result = ArrivalResult()
    try:
        _judge(commits, roots_for, now, expectation, config, pr_created_at, result)
    except Unreadable as exc:
        result.status, result.cause = "unreadable", str(exc)
    result.elapsed_s = round(time.monotonic() - began, 1)
    return result


def lines(r: ArrivalResult) -> list[str]:
    head = (
        f"ARRIVAL status={r.status} judged={r.judged} captured={r.captured} missing={r.missing} lost_window={r.lost_window} "
        f"excused={r.excused} pending={r.pending} untrailered={r.untrailered} windows_read={r.windows_read} "
        f"pr_lookups={r.pr_lookups} pr_lookup_failed={r.pr_lookup_failed} elapsed_s={r.elapsed_s}"
    )
    return [head, *(f"ARRIVAL_MISSING sha={sha[:12]}" for sha in r.missing_shas)]


def write_junit(path: Path | str, r: ArrivalResult) -> None:
    suite = ET.Element("testsuite", name="telemetry-arrival", tests="2")
    for name in ("arrival_missing", "arrival_unreadable"):
        case = ET.SubElement(suite, "testcase", file=MODULE, name=name, classname="telemetry_arrival")
        if r.status == "skipped":
            ET.SubElement(case, "skipped", message="no commit in the judged range")
        elif name == "arrival_missing" and r.status == "red":
            fail = ET.SubElement(case, "failure", type=f"ArrivalMissing_{r.missing_day.replace('-', '_')}")
            fail.set("message", "merged sessions absent from telemetry_sessions past the SLA")
        elif name == "arrival_unreadable" and r.status == "unreadable":
            fail = ET.SubElement(case, "failure", type="ArrivalUnreadable")
            fail.set("message", f"arrival source unreadable: {r.cause}")
    ET.ElementTree(suite).write(path, encoding="utf-8", xml_declaration=True)


def git_commits(root: Path, ref: str = "origin/main") -> list[Commit]:
    proc = subprocess.run(
        ["git", "log", "--first-parent", f"--format=%H{_SEP}%cI{_SEP}%s{_SEP}%B{_END}", ref],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    )  # fmt: skip
    out = []
    for record in proc.stdout.split(_END):
        if record.strip():
            sha, stamp, subject, body = record.strip("\n").split(_SEP, 3)
            out.append(Commit(sha.strip(), _utc(stamp), subject, body))
    return out


def github_pr_created_at(repo: str, number: int) -> datetime:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['GITHUB_TOKEN']}"
    request = urllib.request.Request(f"https://api.github.com/repos/{repo}/pulls/{number}", headers=headers)
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 -- fixed https host
        return _utc(json.load(response)["created_at"])


def load_expectation(root: Path) -> Any:
    try:
        standard = yaml.safe_load((root / STANDARD).read_text(encoding="utf-8"))
        return standard["arrival_expectations"]["tables"][TABLE]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as exc:
        raise Unreadable(type(exc).__name__) from exc


def load_config(root: Path, no_lost_windows: bool = False) -> Any:
    try:
        config = yaml.safe_load((root / CONFIG).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise Unreadable(type(exc).__name__) from exc
    if no_lost_windows and isinstance(config, dict):
        config = {**config, "lost_windows": []}
    return config


def run(
    root: Path,
    reader: Any,
    now: datetime,
    *,
    repo: str = DEFAULT_REPO,
    no_lost_windows: bool = False,
    commits: list[Commit] | Callable[[], list[Commit]] | None = None,
    pr_created_at: Callable[[int], datetime] | None = None,
) -> ArrivalResult:
    """Read the expectation and config, then judge; an unreadable expectation or config is an unreadable result."""
    began = time.monotonic()
    try:
        expectation, config = load_expectation(root), load_config(root, no_lost_windows)
    except Unreadable as exc:
        return ArrivalResult(status="unreadable", cause=str(exc), elapsed_s=round(time.monotonic() - began, 1))
    return judge(
        commits if commits is not None else (lambda: [c for c in git_commits(root) if c.committed_at <= now]),
        lambda day_from, day_to: reader.named("telemetry_root_sessions", day_from=day_from, day_to=day_to),
        now,
        expectation,
        config,
        pr_created_at if pr_created_at is not None else (lambda n: github_pr_created_at(repo, n)),
    )
