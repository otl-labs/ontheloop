"""rec-5504: CI RCA watchdog (scripts/ci_rca/watchdog.py, ci-rca-watchdog.yml, ci-rca.yml's watchdog job)."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from scripts.ci import persona_dispatch_gate
from scripts.ci_rca import watchdog as wd

ROOT = Path(__file__).resolve().parents[1]
REPO = "otl-labs/ontheloop"
NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
RCA_WORKFLOW = ROOT / ".github/workflows/ci-rca.yml"
WATCH_WORKFLOW = ROOT / ".github/workflows/ci-rca-watchdog.yml"


def red(rid: int = 1, name: str = "CI", updated: str = "2026-11-23T08:00:00Z", repo: str = REPO) -> dict:
    return {
        "id": rid,
        "name": name,
        "html_url": f"https://github.com/{REPO}/actions/runs/{rid}",
        "created_at": "2026-11-23T07:55:00Z",
        "run_started_at": "2026-11-23T07:55:00Z",
        "updated_at": updated,
        "head_repository": {"full_name": repo},
    }


def rca(rid: int, title: str, conclusion: str | None = "success", status: str = "completed", **extra) -> dict:
    return {
        "id": rid,
        "display_title": title,
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-11-23T08:00:30Z",
        "run_attempt": 1,
        "html_url": f"https://github.com/{REPO}/actions/runs/{rid}",
        **extra,
    }


def job(conclusion: str, step: str | None = None, name: str = "rca") -> dict:
    steps = [{"name": "Set up job", "conclusion": "success"}]
    if step:
        steps.append({"name": step, "conclusion": conclusion, "log": "SECRET-LOG-TEXT"})
    return {"name": name, "conclusion": conclusion, "steps": steps}


class Fake:
    def __init__(self) -> None:
        self.reds: list[dict] = []
        self.rca_runs: list[dict] = []
        self.jobs: dict[int, list[dict]] = {}
        self.runs_by_id: dict[int, dict] = {}
        self.issues: list[dict] = []
        self.comments: dict[int, list[dict]] = {}
        self.calls: list[tuple] = []
        self.writes: list[tuple] = []
        self.label_missing = False
        self.race_issue: dict | None = None
        self.fail_state_reason: set[str] = set()

    @staticmethod
    def _page(items: list, params: dict) -> list:
        size, page = int(params["per_page"]), int(params["page"])
        return items[(page - 1) * size : page * size]

    def __call__(self, method: str, path: str, params: dict | None = None, body: dict | None = None):
        params = params or {}
        self.calls.append((method, path, dict(params)))
        if method != "GET":
            return self._write(method, path, body or {})
        if path.endswith("/actions/runs"):
            return {"workflow_runs": self._page(self.reds, params)}
        if path.endswith("/ci-rca.yml/runs"):
            runs = self.rca_runs
            if "status" in params:
                runs = [r for r in runs if r.get("conclusion") == params["status"]]
            elif ".." in params.get("created", ""):
                lo, hi = params["created"].split("..")
                runs = [r for r in runs if lo <= r["created_at"] <= hi]
            return {"workflow_runs": self._page(runs, params)}
        if path.endswith("/jobs"):
            return {"jobs": self._page(self.jobs.get(int(path.split("/")[-2]), []), params)}
        if re.search(r"/actions/runs/\d+$", path):
            return self.runs_by_id[int(path.rsplit("/", 1)[1])]
        if path.endswith("/comments"):
            return self._page(self.comments.get(int(path.split("/")[-2]), []), params)
        if path.endswith("/issues"):
            return self._page(self.issues, params)
        if "/labels/" in path:
            if self.label_missing:
                raise wd.GhError("gh api GET labels failed: HTTP 404 Not Found")
            return {"name": wd.LABEL}
        raise AssertionError(path)

    def _write(self, method: str, path: str, body: dict):
        self.writes.append((method, path, body))
        if method == "PATCH" and body.get("state_reason") in self.fail_state_reason:
            raise wd.GhError("422")
        if method == "POST" and path.endswith("/issues"):
            if self.race_issue:
                self.issues.insert(0, self.race_issue)
            made = {"number": 50, "state": "open", "user": {"login": wd.BOT}, "body": body["body"]}
            self.issues.append(made)
            return made
        return {}


def bot_issue(number: int, body: str = "", state: str = "open", login: str = wd.BOT) -> dict:
    return {"number": number, "state": state, "body": body, "user": {"login": login}}


def go(fake: Fake, **kw):
    kw.setdefault("lookback_hours", 24)
    return wd.run(fake, repo=REPO, now=NOW, **kw)


def linked_fixture(conclusion: str | None, status: str = "completed", jobs: list[dict] | None = None) -> Fake:
    fake = Fake()
    fake.reds = [red()]
    fake.rca_runs = [rca(100, "CI RCA for run 1", conclusion, status)]
    fake.jobs[100] = jobs if jobs is not None else [job(conclusion or "success")]
    return fake


def test_watched_set_parse_equals_gate_parser() -> None:
    names = wd.watched_names(RCA_WORKFLOW.read_text("utf-8"))
    assert names == persona_dispatch_gate._workflow_names(str(RCA_WORKFLOW))[1]
    assert "CI" in names and "ci-rca-watchdog" not in names


def test_failed_ci_rca_run_alarms_without_a_join() -> None:
    fake = Fake()
    fake.rca_runs = [rca(200, "CI RCA for run 999", "failure")]
    fake.jobs[200] = [job("failure", "Run the agent"), job("skipped", name="watchdog")]
    summary, lines = go(fake)
    assert len(lines) == 1
    assert lines[0].startswith("- rca_failed: unknown run https://github.com/otl-labs/ontheloop/actions/runs/999 |")
    assert "failed step: Run the agent" in lines[0] and "<!-- ci-rca-watchdog:rca_failed:200 -->" in lines[0]
    assert summary == "watchdog: reds=0 ok=0 unlinkable=0 alarms=1 new=1"
    assert fake.writes[0][0] == "POST" and fake.writes[0][1].endswith("/issues")


@pytest.mark.parametrize(
    ("conclusion", "status", "kind"),
    [
        ("success", "completed", None),
        ("skipped", "completed", "rca_skipped"),
        (None, "in_progress", "rca_stuck"),
        ("failure", "completed", "rca_failed"),
        ("cancelled", "completed", "rca_failed"),
        ("timed_out", "completed", "rca_failed"),
    ],
)
def test_linked_red_kinds(conclusion: str | None, status: str, kind: str | None) -> None:
    fake = linked_fixture(conclusion, status, [job(conclusion or "success", "Agent" if conclusion != "success" else None)])
    summary, lines = go(fake, dry_run=True)
    assert [ln.split(":")[0] for ln in lines] == ([f"- {kind}"] if kind else [])
    assert ("ok=1" in summary) == (kind is None)
    if kind:
        assert lines[0].startswith(f"- {kind}: CI run https://github.com/{REPO}/actions/runs/1 | CI RCA run ")


def test_cancelled_step_as_a_timeout_kill_names_the_step() -> None:
    fake = linked_fixture(
        "failure",
        jobs=[{"name": "rca", "conclusion": "failure", "steps": [{"name": "Run the agent", "conclusion": "cancelled"}]}],
    )
    assert "failed step: Run the agent" in go(fake, dry_run=True)[1][0]


def test_job_without_a_bad_step_and_without_jobs() -> None:
    fake = linked_fixture("failure", jobs=[{"name": "rca", "conclusion": "failure", "steps": []}])
    assert "failed step: job rca: failure" in go(fake, dry_run=True)[1][0]
    fake = linked_fixture("failure", jobs=[])
    assert "failed step: none recorded" in go(fake, dry_run=True)[1][0]


def test_no_rca_run_and_unlinkable() -> None:
    fake = Fake()
    fake.reds = [red()]
    summary, lines = go(fake, dry_run=True)
    assert lines[0].startswith("- no_rca_run: CI run") and "ci-rca-watchdog:no_rca_run:1" in lines[0]
    fake.rca_runs = [rca(90, "CI RCA")]
    summary, lines = go(fake, dry_run=True)
    assert lines == [] and "unlinkable=1" in summary


def test_startup_failure_titled_otherwise_does_not_make_unlinkable() -> None:
    fake = Fake()
    fake.reds = [red()]
    fake.rca_runs = [rca(90, ".github/workflows/ci-rca.yml", "startup_failure")]
    fake.jobs[90] = []
    assert any(ln.startswith("- no_rca_run") for ln in go(fake, dry_run=True)[1])


def test_grace_fork_and_unwatched_are_ignored() -> None:
    fake = Fake()
    fake.reds = [red(1, updated="2026-11-23T11:30:00Z"), red(2, repo="someone/fork"), red(3, name="Not Watched")]
    summary, lines = go(fake, dry_run=True)
    assert lines == [] and summary.startswith("watchdog: reds=0 ")


def test_newest_linked_run_wins_over_an_older_failed_attempt() -> None:
    fake = linked_fixture("failure")
    fake.rca_runs.append(rca(101, "CI RCA for run 1", "success", created_at="2026-11-23T08:05:00Z"))
    fake.jobs[101] = [job("success")]
    summary, lines = go(fake, dry_run=True)
    assert "ok=1" in summary and len(lines) == 1 and lines[0].startswith("- rca_failed: unknown run")


def test_self_run_judged_by_self_result() -> None:
    fake = Fake()
    fake.reds = [red()]
    fake.rca_runs = [rca(100, "CI RCA for run 1", None, "in_progress")]
    fake.runs_by_id[100] = {"display_title": "CI RCA for run 1"}
    fake.jobs[100] = [job("failure", "Run the agent"), {"name": "watchdog", "conclusion": None, "steps": []}]
    _, lines = go(fake, self_run_id=100, self_result="failure", dry_run=True)
    assert len(lines) == 1 and lines[0].startswith("- rca_failed: CI run") and "Run the agent" in lines[0]
    fake.jobs[100] = [job("success")]
    summary, lines = go(fake, self_run_id=100, self_result="success", dry_run=True)
    assert lines == [] and "ok=1" in summary


def test_self_run_missing_from_listing_is_added_and_titled_by_get() -> None:
    fake = Fake()
    fake.runs_by_id[300] = {"display_title": "CI RCA for run 77"}
    fake.jobs[300] = [job("cancelled", "Run the agent")]
    _, lines = go(fake, self_run_id=300, self_result="cancelled", dry_run=True)
    assert len(lines) == 1 and "actions/runs/77 |" in lines[0]
    fake.runs_by_id[300] = {}
    assert "unknown run unknown" in go(fake, self_run_id=300, self_result="cancelled", dry_run=True)[1][0]
    assert go(fake, self_run_id=300, self_result="success", dry_run=True)[1] == []


def test_watchdog_job_failure_alone_is_its_own_kind() -> None:
    fake = linked_fixture("failure", jobs=[job("success"), job("failure", "Reconcile CI RCA outcomes", "watchdog")])
    assert go(fake, dry_run=True)[1][0].startswith("- watchdog_failed:")


def test_linked_failed_run_seen_by_join_and_sweep_is_one_line_with_the_red() -> None:
    fake = linked_fixture("failure", jobs=[job("failure", "Run the agent")])
    _, lines = go(fake, dry_run=True)
    assert len(lines) == 1 and lines[0].startswith("- rca_failed: CI run")


def test_zero_job_cancelled_alarms_only_when_its_named_run_failed() -> None:
    fake = Fake()
    fake.rca_runs = [rca(400, "CI RCA for run 5", "cancelled"), rca(401, "CI RCA", "cancelled")]
    fake.jobs[400] = fake.jobs[401] = []
    fake.runs_by_id[5] = {"conclusion": "success"}
    assert go(fake, dry_run=True)[1] == []
    fake.runs_by_id[5] = {"conclusion": "failure"}
    _, lines = go(fake, dry_run=True)
    assert len(lines) == 1 and "ci-rca-watchdog:rca_failed:400" in lines[0]


def test_reads_page_to_the_end() -> None:
    fake = Fake()
    fake.reds = [red()]
    filler = [rca(1000 + i, "CI RCA for run 7") for i in range(100)]
    fake.rca_runs = [*filler, rca(100, "CI RCA for run 1", "failure")]
    fake.jobs[100] = [job("failure", "Run the agent")]
    assert any("rca_failed:100" in ln for ln in go(fake, dry_run=True)[1])
    fake.issues = [bot_issue(i + 1) for i in range(100)] + [bot_issue(200)]
    fake.comments[200] = [{"user": {"login": wd.BOT}, "body": "<!-- ci-rca-watchdog:rca_failed:100 -->"}]
    assert wd.known_markers(fake, REPO, NOW) == {"<!-- ci-rca-watchdog:rca_failed:100 -->"}


def test_truncation_and_gh_failure_exit_one(capsys: pytest.CaptureFixture[str]) -> None:
    fake = Fake()
    fake.reds = [red(i) for i in range(1000)]
    assert wd.main(["--dry-run"], gh=fake) == 1
    assert "truncated read" in capsys.readouterr().err

    def boom(*_a, **_k):
        raise wd.GhError("down")

    assert wd.main(["--dry-run"], gh=boom) == 1


def test_marker_dedupe_is_bound_to_the_bot_author_and_spans_closed_issues() -> None:
    marker = "<!-- ci-rca-watchdog:rca_failed:200 -->"
    for issues, comments, new in [
        ([bot_issue(1, marker, "closed")], {}, 0),
        ([bot_issue(1)], {1: [{"user": {"login": wd.BOT}, "body": marker}]}, 0),
        ([bot_issue(1, marker, login="mallory")], {}, 1),
        ([bot_issue(1)], {1: [{"user": {"login": "mallory"}, "body": marker}]}, 1),
    ]:
        fake = Fake()
        fake.rca_runs = [rca(200, "CI RCA for run 9", "failure")]
        fake.jobs[200] = [job("failure", "Agent")]
        fake.issues, fake.comments = issues, comments
        summary, _ = go(fake, dry_run=True)
        assert summary.endswith(f"new={new}")
        sinces = [c[2].get("since") for c in fake.calls if c[1].endswith("/comments")]
        assert all(sinces)


def failed_fake() -> Fake:
    fake = Fake()
    fake.rca_runs = [rca(200, "CI RCA for run 9", "failure")]
    fake.jobs[200] = [job("failure", "Agent")]
    return fake


def test_open_issue_gets_one_comment_and_never_a_new_issue() -> None:
    fake = failed_fake()
    fake.issues = [bot_issue(7)]
    go(fake)
    assert [(m, p.rsplit("/", 2)[-2:]) for m, p, _ in fake.writes] == [("POST", ["7", "comments"])]


def test_closed_issue_is_reopened_and_commented_and_lowest_wins() -> None:
    fake = failed_fake()
    fake.issues = [bot_issue(9), bot_issue(4, state="closed"), bot_issue(2, login="mallory")]
    go(fake)
    assert [(m, p.rsplit("/", 1)[-1] if m == "PATCH" else p.rsplit("/", 2)[-2]) for m, p, _ in fake.writes] == [
        ("PATCH", "4"),
        ("POST", "4"),
    ]
    assert fake.writes[0][2] == {"state": "open"}


def test_first_alarm_creates_label_and_issue_with_header_and_no_log_text() -> None:
    fake = failed_fake()
    fake.label_missing = True
    go(fake)
    kinds = [(m, p.rsplit("/", 1)[-1]) for m, p, _ in fake.writes]
    assert kinds == [("POST", "labels"), ("POST", "issues")]
    body = fake.writes[1][2]["body"]
    assert body.startswith(wd.HEADER) and "SECRET-LOG-TEXT" not in body and fake.writes[1][2]["labels"] == [wd.LABEL]
    fake = failed_fake()
    go(fake)
    assert [p.rsplit("/", 1)[-1] for _, p, _ in fake.writes] == ["issues"]


def test_label_lookup_error_other_than_404_propagates() -> None:
    def gh(method, path, params=None, body=None):
        raise wd.GhError("HTTP 500")

    with pytest.raises(wd.GhError):
        wd._ensure_label(gh, REPO)


@pytest.mark.parametrize("fail_duplicate", [False, True])
def test_racing_create_comments_on_the_lower_issue_and_closes_its_own(fail_duplicate: bool) -> None:
    fake = failed_fake()
    fake.race_issue = bot_issue(40)
    fake.fail_state_reason = {"duplicate"} if fail_duplicate else set()
    go(fake)
    patches = [w for w in fake.writes if w[0] == "PATCH"]
    assert patches[-1][2]["state_reason"] == ("not_planned" if fail_duplicate else "duplicate")
    assert patches[-1][1].endswith("/issues/50")
    assert any(w[1].endswith("/issues/40/comments") for w in fake.writes)


def test_racing_close_that_fails_both_ways_raises() -> None:
    fake = failed_fake()
    fake.race_issue = bot_issue(40)
    fake.fail_state_reason = {"duplicate", "not_planned"}
    with pytest.raises(wd.GhError):
        go(fake)


def test_long_alarm_lists_split_under_the_body_limit() -> None:
    lines = [f"- line {i} " + "x" * 30000 for i in range(5)]
    bodies = wd._chunks(lines, wd.HEADER)
    assert len(bodies) > 1 and all(len(b) < wd.LIMIT for b in bodies)
    fake = Fake()
    wd.post(fake, REPO, lines)
    assert sum(1 for w in fake.writes if w[1].endswith("/comments")) == len(bodies) - 1


def test_dry_run_posts_nothing_and_main_prints_the_summary(capsys: pytest.CaptureFixture[str]) -> None:
    fake = failed_fake()
    assert wd.main(["--dry-run", "--lookback-hours", "48"], gh=fake) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[-1] == "watchdog: reds=0 ok=0 unlinkable=0 alarms=1 new=1" and not fake.writes
    assert wd.main(["--self-run-id", "1", "--self-result", "success", "--dry-run"], gh=Fake()) == 0


def test_gh_api_helper_builds_the_rest_call(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []

    def fake_run(cmd, **kw):
        seen.append((cmd, kw.get("input")))
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok": true}', stderr="")

    monkeypatch.setattr(wd.subprocess, "run", fake_run)
    assert wd.gh_api("GET", "repos/x/y", {"a": "b"}) == {"ok": True}
    assert seen[0][0] == ["gh", "api", "-X", "GET", "repos/x/y", "-f", "a=b"]
    wd.gh_api("POST", "repos/x/y", None, {"k": 1})
    assert seen[1][0][-2:] == ["--input", "-"] and seen[1][1] == '{"k": 1}'
    monkeypatch.setattr(wd.subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 0, stdout="", stderr=""))
    assert wd.gh_api("PATCH", "repos/x/y") is None
    monkeypatch.setattr(wd.subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 1, stdout="", stderr="HTTP 404"))
    with pytest.raises(wd.GhError, match="404"):
        wd.gh_api("GET", "repos/x/y")


def test_import_closure_is_stdlib_and_yaml_only() -> None:
    allowed_first_party = {"scripts.repo_identity"}
    for rel in ("scripts/ci_rca/watchdog.py", "scripts/repo_identity.py"):
        tree = ast.parse((ROOT / rel).read_text("utf-8"))
        for node in ast.walk(tree):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                names = [node.module or ""]
            for name in names:
                top = name.split(".")[0]
                assert name in allowed_first_party or top in sys.stdlib_module_names or top == "yaml", (rel, name)


def _jobs(path: Path) -> dict:
    return yaml.safe_load(path.read_text("utf-8"))["jobs"]


def _perms(job_def: dict) -> dict:
    return job_def["permissions"]


def test_watchdog_workflow_shape() -> None:
    text = WATCH_WORKFLOW.read_text("utf-8")
    doc = yaml.safe_load(text)
    on = doc[True]
    assert on["schedule"] and "lookback_hours" in on["workflow_dispatch"]["inputs"] and "workflow_run" not in on
    job_def = doc["jobs"]["watch"]
    assert job_def["if"] == "(github.event_name != 'workflow_dispatch' || github.actor != 'otl-workflow-dispatcher[bot]')"
    assert _perms(job_def) == {"contents": "read", "actions": "read", "issues": "write"}
    assert "id-token" not in text and "secrets." not in text and "claude" not in text.lower().replace("ci-rca", "")
    assert job_def["concurrency"] == {"group": "ci-rca-watchdog", "cancel-in-progress": False}


def test_ci_rca_run_name_and_final_watchdog_job() -> None:
    doc = yaml.safe_load(RCA_WORKFLOW.read_text("utf-8"))
    title = doc["run-name"]
    assert title == "CI RCA for run ${{ github.event.workflow_run.id || inputs.run_id }}"
    assert wd._TITLE.match("CI RCA for run 12345")
    job_def = doc["jobs"]["watchdog"]
    assert job_def["needs"] == "rca"
    assert job_def["if"].startswith("${{ (github.event_name != 'workflow_dispatch' || github.actor != ")
    assert "always() && needs.rca.result != 'skipped'" in job_def["if"]
    assert _perms(job_def) == {"contents": "read", "actions": "read", "issues": "write"}
    text = RCA_WORKFLOW.read_text("utf-8").split("\n  watchdog:\n", 1)[1]
    assert "secrets." not in text and "id-token" not in text
    assert '--self-run-id "$SELF_RUN_ID" --self-result "$SELF_RESULT"' in text
    assert "ci-rca-watchdog" not in "".join(doc[True]["workflow_run"]["workflows"])
