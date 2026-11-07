"""The arrival oracle for telemetry_sessions (Decision 250 cl.5, rec-4940)."""

from __future__ import annotations

import copy
import subprocess
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.ci_rca import fingerprint
from scripts.telemetry_verify import completeness
from scripts.telemetry_verify import session_presence as sp

NOW = datetime(2026, 11, 10, 12, tzinfo=timezone.utc)
EXPECT = {"kind": "enforced", "sla": "60m", "check": {"workflow": "telemetry-arrival", "module": sp.MODULE}}
CONFIG = {
    "judge_days": 3,
    "window_days": 7,
    "max_lookback_days": 56,
    "max_pr_lookups": 60,
    "collapse_min_commits": 3,
    "lost_windows": [{"to": "2026-10-28T00:00:00Z", "owner": "Decision 250", "reason": "x"}],
    "excused_sessions": [],
}
ROOT = Path(__file__).resolve().parents[2]


def commit(n: int, key: str | None, hours_ago: float = 10, *, pr: int | None = None, extra: str = "") -> sp.Commit:
    body = f"plan\n\nClaude-Session: https://claude.ai/code/session_{key}\n" if key else "plan\n"
    body += extra
    return sp.Commit(f"{n:040x}", NOW - timedelta(hours=hours_ago), f"msg ({'#' + str(pr if pr else n)})", body)


class Roots:
    def __init__(self, keys: list[str] | dict[str, list[str]], fail: int = 0) -> None:
        self.by_start = keys if isinstance(keys, dict) else {"": keys}
        self.fail, self.calls = fail, []

    def __call__(self, day_from: str, day_to: str) -> list[dict[str, Any]]:
        if (span := (date.fromisoformat(day_to) - date.fromisoformat(day_from)).days + 1) > 7:
            raise AssertionError(f"window of {span} days exceeds the verb's 7")
        self.calls.append((day_from, day_to))
        if self.fail:
            self.fail -= 1
            raise RuntimeError("planted https://secret-host body")
        keys = self.by_start.get(day_from, self.by_start.get("", []))
        return [{"harness_session_key": k if i % 2 else f"cse_{k}"} for i, k in enumerate(keys)]


def created(table: dict[int, str]) -> Any:
    def lookup(n: int) -> datetime:
        if n not in table:
            raise OSError("no such pull request")
        return datetime.fromisoformat(table[n])

    return lookup


def run(commits: Any, roots: Any, config: Any = CONFIG, expectation: Any = EXPECT, pr: Any = None) -> sp.ArrivalResult:
    return sp.judge(commits, roots, NOW, expectation, config, pr or created({}))


def test_a_captured_key_passes() -> None:
    result = run([commit(1, "AAA")], Roots(["AAA"]))
    assert (result.status, result.judged, result.captured, result.missing) == ("green", 1, 1, 0)
    assert result.exit_code() == 0


def test_merged_session_absent_after_sla_fails() -> None:
    result = run([commit(1, "AAA", pr=7)], Roots([]))
    assert (result.status, result.missing, result.missing_shas) == ("red", 1, [f"{1:040x}"])
    assert result.exit_code() == 1 and result.missing_day == "2026-11-10"


def test_unreadable_reader_fails_not_skips() -> None:
    result = run([commit(1, "AAA")], Roots(["AAA"], fail=2))
    assert (result.status, result.cause) == ("unreadable", "RuntimeError") and result.exit_code() == 1


def test_one_retry_rescues_a_first_window_failure() -> None:
    assert run([commit(1, "AAA")], Roots(["AAA"], fail=1)).status == "green"


def test_a_key_merged_inside_the_sla_is_pending() -> None:
    result = run([commit(1, "AAA", hours_ago=0.5), commit(2, "BBB")], Roots(["BBB"]))
    assert (result.status, result.judged, result.pending) == ("green", 1, 1)


def test_an_untrailered_commit_is_counted_not_red() -> None:
    result = run([commit(1, None), commit(2, "AAA")], Roots(["AAA"]))
    assert (result.status, result.untrailered) == ("green", 1)


def test_a_squash_with_two_trailers_judges_both_and_distinct_keys_once() -> None:
    both = commit(1, "AAA", extra="Claude-Session: https://claude.ai/code/session_BBB\n")
    result = run([both, commit(2, "AAA", hours_ago=20)], Roots(["AAA", "BBB"]))
    assert (result.judged, result.captured) == (2, 2)
    earliest = run([both, commit(2, "AAA", hours_ago=20)], Roots([]))
    assert earliest.missing_shas[0] == f"{2:040x}"


def test_a_key_found_only_in_an_older_window_stops_the_walk_early() -> None:
    older = (date(2026, 11, 10) - timedelta(days=13)).isoformat()
    roots = Roots({older: ["AAA"]})
    result = run([commit(1, "AAA")], roots)
    assert result.status == "green" and len(roots.calls) == 2 and result.windows_read == 2


def test_a_key_unfound_after_max_lookback_is_missing() -> None:
    roots = Roots([])
    result = run([commit(1, "AAA")], roots)
    assert result.missing == 1 and len(roots.calls) == 9 and result.windows_read == 9


def test_lost_window_both_shapes_and_excused_classify() -> None:
    config = copy.deepcopy(CONFIG)
    config["lost_windows"] = [
        {"to": "2026-10-28T00:00:00Z", "owner": "Decision 250", "reason": "x"},
        {"from": "2026-11-03T00:49:00Z", "to": "2026-11-06T00:56:31Z", "owner": "rec-4939", "reason": "y"},
    ]
    config["excused_sessions"] = [{"key": "EEE", "owner": "rec-4939"}]
    pr = created({1: "2026-10-20T00:00:00Z", 2: "2026-11-04T00:00:00Z", 3: "2026-11-08T00:00:00Z"})
    commits = [commit(1, "AAA"), commit(2, "BBB"), commit(3, "CCC"), commit(4, "EEE"), commit(5, "FFF")]
    result = run(commits, Roots([]), config, pr=pr)
    assert (result.lost_window, result.excused, result.missing, result.pr_lookups, result.pr_lookup_failed) == (2, 1, 2, 4, 1)
    assert result.status == "red"


def test_a_pr_lookup_failure_leaves_the_key_missing_and_the_cap_bounds_lookups() -> None:
    config = {**CONFIG, "max_pr_lookups": 1}
    result = run([commit(1, "AAA"), commit(2, "BBB")], Roots([]), config)
    assert (result.missing, result.pr_lookups, result.pr_lookup_failed) == (2, 1, 1)


def test_no_lost_windows_makes_no_lookup_and_a_subject_without_a_pr_is_missing() -> None:
    config = {**CONFIG, "lost_windows": []}
    boom = created({})
    result = run([commit(1, "AAA")], Roots([]), config, pr=boom)
    assert (result.missing, result.pr_lookups) == (1, 0)
    plain = sp.Commit("a" * 40, NOW - timedelta(hours=9), "no number", "Claude-Session: https://x/session_ZZZ\n")
    assert run([plain], Roots([])).missing == 1


def test_an_empty_range_is_skipped_never_green() -> None:
    result = run([], Roots([]))
    assert result.status == "skipped" and result.exit_code() == 0
    few = [commit(1, None), commit(2, None)]
    assert run(few, Roots([])).status == "skipped"


def test_trailerless_denominator_is_unreadable_not_skipped() -> None:
    result = run([commit(n, None) for n in (1, 2, 3)], Roots([]))
    assert (result.status, result.cause, result.exit_code()) == ("unreadable", "DenominatorCollapse", 1)


def test_a_commit_outside_the_judged_range_is_ignored() -> None:
    assert run([commit(1, "AAA", hours_ago=24 * 5)], Roots([])).status == "skipped"


def test_a_git_failure_is_unreadable() -> None:
    def broken() -> list[sp.Commit]:
        raise subprocess.CalledProcessError(128, "git")

    assert run(broken, Roots([])).cause == "CalledProcessError"


@pytest.mark.parametrize(
    "expectation",
    [
        None,
        {**EXPECT, "kind": "residual"},
        {**EXPECT, "check": {"module": "other.py"}},
        {**EXPECT, "check": "x"},
        {**EXPECT, "sla": "soon"},
    ],
)
def test_a_malformed_or_mismatched_expectation_is_unreadable(expectation: Any) -> None:
    result = run([commit(1, "AAA")], Roots(["AAA"]), expectation=expectation)
    assert result.status == "unreadable"


@pytest.mark.parametrize(
    "config",
    [
        None,
        {**CONFIG, "unknown": 1},
        {**CONFIG, "judge_days": 0},
        {**CONFIG, "judge_days": True},
        {**CONFIG, "lost_windows": [{"to": "2026-10-28T00:00:00Z", "owner": "me"}]},
        {**CONFIG, "lost_windows": [{"from": "2026-11-05T00:00:00Z", "to": "2026-11-04T00:00:00Z", "owner": "rec-4939"}]},
        {**CONFIG, "lost_windows": [{"to": "2026-10-28T00:00:00Z", "owner": "rec-4939", "extra": 1}]},
        {**CONFIG, "lost_windows": [{"owner": "rec-4939"}]},
        {**CONFIG, "lost_windows": [{"to": "nope", "owner": "rec-4939"}]},
        {**CONFIG, "excused_sessions": [{"key": "X", "owner": "Decision 250"}]},
        {**CONFIG, "excused_sessions": [{"key": "X"}]},
        {**CONFIG, "excused_sessions": ["X"]},
    ],
)
def test_a_malformed_config_is_unreadable(config: Any) -> None:
    assert run([commit(1, "AAA")], Roots(["AAA"]), config=config).status == "unreadable"


def test_the_output_line_never_contains_a_session_key() -> None:
    result = run([commit(1, "SECRETKEY123", pr=9)], Roots([]))
    text = "\n".join(sp.lines(result))
    assert "SECRETKEY123" not in text and "claude.ai" not in text
    assert text.startswith("ARRIVAL status=red judged=1 captured=0 missing=1 lost_window=0 excused=0 pending=0 untrailered=0")
    assert f"ARRIVAL_MISSING sha={1:040x}"[:32] in text and f"{1:040x}" not in text


def _signature(result: sp.ArrivalResult, path: Path) -> tuple[str, list[str]]:
    sp.write_junit(path, result)
    return fingerprint.error_signature_from_junit(path, ROOT)[0]


def test_one_outage_day_is_one_fingerprint_and_two_days_are_two(tmp_path: Path) -> None:
    one = run([commit(1, "AAA", hours_ago=10), commit(2, "BBB", hours_ago=11)], Roots([]))
    two = run([commit(1, "AAA", hours_ago=10), commit(3, "CCC", hours_ago=11), commit(4, "DDD", hours_ago=12)], Roots([]))
    sig_one, nodes = _signature(one, tmp_path / "1.xml")
    sig_two, _ = _signature(two, tmp_path / "2.xml")
    assert sig_one == sig_two and nodes == [f"{sp.MODULE}::arrival_missing"]
    earlier = commit(6, "FFF", hours_ago=30)
    other = run([earlier], Roots([]), {**CONFIG, "judge_days": 5})
    assert _signature(other, tmp_path / "3.xml")[0] != sig_one


def test_unreadable_junit_head_names_the_class_only(tmp_path: Path) -> None:
    result = run([commit(1, "AAA")], Roots(["AAA"], fail=2))
    sig, nodes = _signature(result, tmp_path / "u.xml")
    assert "ArrivalUnreadable" in sig and "RuntimeError" in sig and "secret-host" not in sig
    assert nodes == [f"{sp.MODULE}::arrival_unreadable"]


def test_green_and_skipped_junit_have_no_failures(tmp_path: Path) -> None:
    green = tmp_path / "g.xml"
    sp.write_junit(green, run([commit(1, "AAA")], Roots(["AAA"])))
    assert ET.parse(green).getroot().find(".//failure") is None
    skipped = tmp_path / "s.xml"
    sp.write_junit(skipped, run([], Roots([])))
    assert len(ET.parse(skipped).getroot().findall(".//skipped")) == 2


def test_the_open_start_lost_window_ends_on_the_production_first_judged_day() -> None:
    config = yaml.safe_load((ROOT / sp.CONFIG).read_text(encoding="utf-8"))
    open_start = [w for w in config["lost_windows"] if "from" not in w]
    assert len(open_start) == 1
    assert open_start[0]["to"].startswith(completeness.PRODUCTION_FIRST_JUDGED_DAY)
    sp.check_config(config)
    expectation = sp.load_expectation(ROOT)
    assert sp.check_expectation(expectation) == timedelta(minutes=60)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def test_git_commits_reads_first_parent_history(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "a@b.c")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    (tmp_path / "f").write_text("1")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-q", "-m", "work (#12)", "-m", "Claude-Session: https://claude.ai/code/session_ABC")
    commits = sp.git_commits(tmp_path, "main")
    assert len(commits) == 1 and commits[0].subject == "work (#12)"
    assert sp._session_keys(commits[0]) == ["ABC"]
    with pytest.raises(subprocess.CalledProcessError):
        sp.git_commits(tmp_path, "nope")


def test_github_adapter_reads_created_at_with_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Response:
        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self, *_: Any) -> bytes:
            return b'{"created_at": "2026-11-04T01:02:03Z"}'

    def fake(request: urllib.request.Request, timeout: int) -> Response:
        seen.update(url=request.full_url, auth=request.get_header("Authorization"), timeout=timeout)
        return Response()

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    monkeypatch.setenv("GITHUB_TOKEN", "tok")
    assert sp.github_pr_created_at("o/r", 7) == datetime(2026, 11, 4, 1, 2, 3, tzinfo=timezone.utc)
    assert seen == {"url": "https://api.github.com/repos/o/r/pulls/7", "auth": "Bearer tok", "timeout": 10}
    monkeypatch.delenv("GITHUB_TOKEN")
    sp.github_pr_created_at("o/r", 7)
    assert seen["auth"] is None


def test_load_helpers_refuse_unreadable_inputs(tmp_path: Path) -> None:
    with pytest.raises(sp.Unreadable):
        sp.load_expectation(tmp_path)
    with pytest.raises(sp.Unreadable):
        sp.load_config(tmp_path)
    (tmp_path / "config/telemetry").mkdir(parents=True)
    (tmp_path / sp.CONFIG).write_text("lost_windows: []\n", encoding="utf-8")
    assert sp.load_config(tmp_path, no_lost_windows=True)["lost_windows"] == []
    (tmp_path / sp.CONFIG).write_text("- a\n", encoding="utf-8")
    assert sp.load_config(tmp_path, no_lost_windows=True) == ["a"]
    (tmp_path / sp.CONFIG).write_text("a: [unclosed\n", encoding="utf-8")
    with pytest.raises(sp.Unreadable):
        sp.load_config(tmp_path)


def test_run_reads_the_repository_and_reports_an_unreadable_expectation(tmp_path: Path) -> None:
    class Reader:
        def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
            assert verb == "telemetry_root_sessions"
            return [{"harness_session_key": "AAA"}]

    ok = sp.run(ROOT, Reader(), NOW, commits=[commit(1, "AAA")], pr_created_at=created({}))
    assert ok.status == "green"
    assert sp.run(tmp_path, Reader(), NOW).status == "unreadable"
    seen: list[int] = []
    sp.run(ROOT, Reader(), NOW, commits=[commit(1, "ZZZ", pr=44)], pr_created_at=lambda n: seen.append(n) or NOW)
    assert seen == [44]


def test_run_default_commit_source_is_git_up_to_now(monkeypatch: pytest.MonkeyPatch) -> None:
    class Reader:
        def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
            return []

    monkeypatch.setattr(
        sp, "git_commits", lambda root: [commit(1, "AAA"), sp.Commit("f" * 40, NOW + timedelta(days=1), "x", "")]
    )
    seen: list[str] = []
    monkeypatch.setattr(sp, "github_pr_created_at", lambda repo, n: seen.append(repo) or NOW)
    result = sp.run(ROOT, Reader(), NOW, repo="o/r")
    assert result.status in ("red", "green") and result.judged == 1 and seen == ["o/r"]
