"""Mirror test for scripts/convergence_health/dispatch_integrity.py (PLAN-scheduled-workflow-dispatch PR 2)."""

from __future__ import annotations

import io
import urllib.error
from datetime import datetime, timezone
from typing import Any, Callable

import pytest

from scripts.convergence_health import dispatch_integrity as di
from scripts.executor.acceptance_lint import lint_acceptance_command

API = "https://api.github.com/repos/x/y"
BOT = "otl-workflow-dispatcher[bot]"
NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


def _run(
    run_id: int,
    path: str,
    event: str = "workflow_dispatch",
    actor: str = "someone",
    trigger: str | None = None,
    branch: str = "main",
    attempt: int = 1,
) -> dict[str, Any]:
    return {
        "id": run_id,
        "path": f".github/workflows/{path}",
        "event": event,
        "actor": {"login": actor},
        "triggering_actor": {"login": trigger or actor},
        "head_branch": branch,
        "run_attempt": attempt,
    }


class Fake:
    def __init__(self, oidc: Any = None, actor_runs: Any = None, all_runs: Any = None, workflows: Any = None) -> None:
        self.oidc = {"use_default": True} if oidc is None else oidc
        self.actor_runs = actor_runs if actor_runs is not None else [[]]
        self.all_runs = all_runs if all_runs is not None else [[]]
        self.workflows = workflows if workflows is not None else [{"path": ".github/workflows/a.yml", "state": "active"}]
        self.urls: list[str] = []

    def __call__(self, url: str) -> Any:
        self.urls.append(url)
        if "oidc/customization/sub" in url:
            return self._value(self.oidc)
        if "/actions/workflows?" in url:
            return self._value({"workflows": self.workflows})
        pages = self.actor_runs if "actor=" in url else self.all_runs
        page = int(url.rsplit("page=", 1)[1])
        value = pages[page - 1] if page <= len(pages) else []
        return self._value(value if isinstance(value, Exception) else {"workflow_runs": value})

    @staticmethod
    def _value(value: Any) -> Any:
        if isinstance(value, Exception):
            raise value
        return value


def _http(code: int, github: bool) -> urllib.error.HTTPError:
    headers = {"X-GitHub-Request-Id": "abc"} if github else {"Content-Type": "text/plain"}
    return urllib.error.HTTPError("u", code, "x", headers, io.BytesIO(b""))  # type: ignore[arg-type]


class Portal:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.fail = fail

    def __call__(self, action: str, fields: dict[str, Any]) -> Any:
        if self.fail:
            raise RuntimeError("writer unreachable")
        self.calls.append((action, fields))
        return "rec-1"


def _tick(
    fake: Callable[[str], Any], rows: list[dict[str, Any]] | None = None, portal: Portal | None = None, diagnose: bool = False
) -> tuple[dict[str, str], Portal]:
    portal = portal or Portal()
    return di.run_tick(fake, API, now=NOW, diagnose=diagnose, portal=portal, rows=rows if rows is not None else []), portal


def test_clear_state_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    actions, portal = _tick(Fake())
    assert portal.calls == [] and actions == {"integrity_blind": "none"}
    out = capsys.readouterr().out
    assert "dispatch_integrity: oidc_subject=clear off_register_run=clear disabled_workflow=clear" in out


@pytest.mark.parametrize(
    ("fake", "cls"),
    [
        (Fake(oidc={"use_default": False, "include_claim_keys": ["repo"]}), "oidc_subject"),
        (Fake(actor_runs=[[_run(5, "ghas-probe.yml", actor=BOT)]]), "off_register_run"),
        (Fake(all_runs=[[_run(6, "ci.yml", event="push", trigger=BOT, attempt=2)]]), "off_register_run"),
        (Fake(all_runs=[[_run(6, "ghas-probe.yml", actor="person", trigger=BOT)]]), "off_register_run"),
        (Fake(actor_runs=[[_run(7, "terraform-drift.yml", actor=BOT, branch="dev")]]), "off_register_run"),
        (Fake(workflows=[{"path": ".github/workflows/ci-rca.yml", "state": "disabled_manually"}]), "disabled_workflow"),
    ],
    ids=["oidc", "bot-dispatch", "bot-rerun", "bot-trigger-dispatch", "other-branch", "disabled"],
)
def test_each_breach_files_its_class_once(fake: Fake, cls: str) -> None:
    actions, portal = _tick(fake)
    assert actions[cls] == "file"
    filed = [f for a, f in portal.calls if a == "file"]
    assert len(filed) == 1 and filed[0]["priority"] == "High" and f"class={cls} " in filed[0]["context"]
    assert filed[0]["source"] == "workflow_dispatch_integrity"


def test_ci_rca_child_of_bot_canary_is_clear_but_its_rerun_is_not() -> None:
    child = _run(8, "ci-rca.yml", event="workflow_run", actor=BOT)
    actions, portal = _tick(Fake(actor_runs=[[child]]))
    assert portal.calls == []
    rerun = _run(8, "ci-rca.yml", event="workflow_run", actor=BOT, attempt=2)
    actions, portal = _tick(Fake(actor_runs=[[rerun]]))
    assert actions["off_register_run"] == "file"


def test_register_dispatch_on_main_is_clear() -> None:
    _, portal = _tick(Fake(actor_runs=[[_run(9, "terraform-drift.yml", actor=BOT)]]))
    assert portal.calls == []


def _open(cls: str, findings: str) -> dict[str, Any]:
    return {"id": "rec-9", "status": "open", "source": di.SOURCE, "context": f"class={cls} findings={findings}"}


def test_update_only_on_changed_finding_set() -> None:
    fake = Fake(workflows=[{"path": ".github/workflows/a.yml", "state": "disabled_inactivity"}])
    same = _open("disabled_workflow", "workflow:a.yml:disabled_inactivity")
    actions, portal = _tick(fake, rows=[same])
    assert actions["disabled_workflow"] == "unchanged" and portal.calls == []
    actions, portal = _tick(fake, rows=[_open("disabled_workflow", "workflow:b.yml:disabled_manually")])
    assert actions["disabled_workflow"] == "update" and portal.calls[0][0] == "update"


def test_breach_episode_is_never_closed_by_the_sensor() -> None:
    actions, portal = _tick(Fake(), rows=[_open("oidc_subject", "oidc_subject:custom")])
    assert "oidc_subject" not in actions and portal.calls == []


@pytest.mark.parametrize(
    "fake",
    [Fake(oidc=_http(403, github=True)), Fake(actor_runs=[RuntimeError("boom")])],
    ids=["github-403", "read-error"],
)
def test_failed_read_files_blind_without_raising(fake: Fake) -> None:
    actions, portal = _tick(fake)
    assert actions["integrity_blind"] == "file"
    filed = portal.calls[0][1]
    assert filed["priority"] == "Medium" and "class=integrity_blind" in filed["context"]


def test_dead_payload_is_blind() -> None:
    fake = Fake()
    fake.oidc = {}
    original = fake.__call__

    def caller(url: str) -> Any:
        return None if "oidc" in url else original(url)

    actions, _ = _tick(caller)
    assert actions["integrity_blind"] == "file"


def test_next_clear_tick_closes_blind() -> None:
    actions, portal = _tick(Fake(), rows=[_open("integrity_blind", "oidc_subject:blind")])
    assert actions["integrity_blind"] == "close" and portal.calls[0][0] == "close"


def test_five_full_actor_pages_is_blind() -> None:
    full = [_run(i, "terraform-drift.yml", actor=BOT) for i in range(100)]
    result = di.assess(Fake(actor_runs=[full] * 5), API, now=NOW, checks=("off_register_run",))
    assert result["off_register_run"][0] == "blind"


def test_tenth_full_all_page_is_blind_ninth_is_not() -> None:
    full = [_run(i, "terraform-drift.yml", actor=BOT) for i in range(100)]
    assert di.assess(Fake(all_runs=[full] * 10), API, now=NOW, checks=("off_register_run",))["off_register_run"][0] == "blind"
    assert di.assess(Fake(all_runs=[full] * 9), API, now=NOW, checks=("off_register_run",))["off_register_run"][0] == "clear"


def test_listing_queries_window_and_quoted_bot() -> None:
    fake = Fake()
    di.assess(fake, API, now=NOW, checks=("off_register_run",))
    assert fake.urls[0] == (
        f"{API}/actions/runs?actor=otl-workflow-dispatcher%5Bbot%5D&created=>=2026-11-16T09:00:00Z&per_page=100&page=1"
    )
    assert fake.urls[1] == f"{API}/actions/runs?created=>=2026-11-16T09:00:00Z&per_page=100&page=1"


def test_proxy_403_is_its_own_verdict_and_blind_in_status() -> None:
    result = di.assess(Fake(oidc=_http(403, github=False)), API, now=NOW, checks=("oidc_subject",))
    assert result["oidc_subject"][0] == "proxy_blocked"
    full = {**result, "off_register_run": ("clear", []), "disabled_workflow": ("clear", [])}
    assert "oidc_subject=blind" in di.status_line(full)


def test_non_403_http_error_is_blind_not_proxy() -> None:
    result = di.assess(Fake(oidc=_http(500, github=False)), API, now=NOW, checks=("oidc_subject",))
    assert result["oidc_subject"][0] == "blind"


def test_portal_failure_raises() -> None:
    with pytest.raises(RuntimeError, match="writer unreachable"):
        _tick(Fake(oidc={"use_default": False}), portal=Portal(fail=True))


def test_diagnose_writes_nothing(capsys: pytest.CaptureFixture[str]) -> None:
    actions, portal = _tick(Fake(oidc={"use_default": False}), diagnose=True)
    assert actions == {"diagnose": "diagnose"} and portal.calls == []
    assert "diagnose" in capsys.readouterr().out


def test_rows_are_read_when_not_given(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(di, "find_recs", lambda source, status, reader=None: seen.append(source) or [])
    di.run_tick(Fake(), API, now=NOW, portal=Portal())
    assert seen == [di.SOURCE]


@pytest.mark.parametrize("cls", di.CLASSES)
def test_acceptance_passes_lint(cls: str) -> None:
    ok, why = lint_acceptance_command(di.acceptance_for(cls), require_discrimination=True)
    assert ok, why


def test_default_register_and_now(monkeypatch: pytest.MonkeyPatch) -> None:
    result = di.assess(Fake(), API, checks=("disabled_workflow",))
    assert result == {"disabled_workflow": ("clear", [])}


class TestEntryPoints:
    @pytest.fixture
    def context(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
        state: dict[str, Any] = {"fake": Fake()}
        monkeypatch.setattr(di, "_github_context", lambda: (state["fake"], API))
        return state

    def test_github_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GH_TOKEN", "t")
        caller, api_base = di._github_context()
        assert callable(caller) and api_base.startswith("https://api.github.com/repos/")

    def test_tick_reads_rows_and_files(self, context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CONVERGENCE_HEALTH_DIAGNOSE", raising=False)
        monkeypatch.setattr(di, "find_recs", lambda source, status, profile=None, reader=None: [])
        portal = Portal()
        monkeypatch.setattr(di, "run_tick", lambda caller, api, diagnose, rows: {"rows": str(rows), "d": str(diagnose)})
        assert di.tick(profile="p") == {"rows": "[]", "d": "False"}
        assert portal.calls == []

    def test_tick_diagnose_reads_nothing(self, context: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CONVERGENCE_HEALTH_DIAGNOSE", "1")
        monkeypatch.setattr(di, "find_recs", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read")))
        assert di.tick() == {"diagnose": "diagnose"}

    def test_probe_clear(self, context: dict[str, Any]) -> None:
        assert di.probe() == 0
        assert di.probe("integrity_blind") == 0

    def test_probe_finding(self, context: dict[str, Any]) -> None:
        context["fake"] = Fake(oidc={"use_default": False})
        assert di.probe("oidc_subject") == 1

    def test_probe_one_class_reads_only_its_class(self, context: dict[str, Any]) -> None:
        context["fake"] = Fake(oidc=_http(403, github=False))
        assert di.probe("off_register_run") == 0
        assert not any("oidc" in u for u in context["fake"].urls)

    def test_probe_proxy_block_exits_2(self, context: dict[str, Any]) -> None:
        context["fake"] = Fake(oidc=_http(403, github=False))
        assert di.probe("oidc_subject") == 2

    def test_probe_unknown_check(self, context: dict[str, Any]) -> None:
        assert di.probe("nope") == 1


def _paged_workflows(pages: list[list[dict[str, Any]]]) -> Callable[[str], Any]:
    base = Fake()

    def caller(url: str) -> Any:
        if "/actions/workflows?" in url:
            page = int(url.rsplit("page=", 1)[1])
            return {"workflows": pages[page - 1] if page <= len(pages) else []}
        return base(url)

    return caller


def test_disabled_workflow_on_second_page_is_found() -> None:
    first = [{"path": f".github/workflows/w{i}.yml", "state": "active"} for i in range(100)]
    second = [{"path": ".github/workflows/late.yml", "state": "disabled_manually"}]
    result = di.assess(_paged_workflows([first, second]), API, now=NOW, checks=("disabled_workflow",))
    assert result["disabled_workflow"] == ("breach", ["workflow:late.yml:disabled_manually"])


def test_two_full_workflow_pages_is_blind() -> None:
    full = [{"path": f".github/workflows/w{i}.yml", "state": "active"} for i in range(100)]
    result = di.assess(_paged_workflows([full, full]), API, now=NOW, checks=("disabled_workflow",))
    assert result["disabled_workflow"][0] == "blind"
