"""Detective controls for the scheduled workflow dispatcher App (Decision 267 clause 5, rec-4233).

Each convergence-health tick reads (1) the repository's OIDC subject template, (2) the runs created in the last three
hours, with and without the bot as actor, and (3) every workflow's state, and files one deduped rec per class through
scripts.rec_episode.run_episode (source workflow_dispatch_integrity). The three breach classes are High and never
close automatically (run_episode is called for them only while a finding exists); integrity_blind is Medium and closes
on the first tick whose reads all succeed. A portal write failure propagates (Decision 84 I-4).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from scripts.rec_episode import find_recs, run_episode
from src.data.handlers.workflow_dispatcher_handler import load_register

SOURCE = "workflow_dispatch_integrity"
MODULE_FILE = "scripts/convergence_health/dispatch_integrity.py"
BREACH_CLASSES = ("oidc_subject", "off_register_run", "disabled_workflow")
BLIND = "integrity_blind"
CLASSES = (*BREACH_CLASSES, BLIND)
WINDOW = timedelta(hours=3)
PAGE_SIZE = 100
PAGES_ACTOR = 5
PAGES_ALL = 10
PAGES_WORKFLOWS = 2
_TITLES = {
    "oidc_subject": "Dispatcher integrity breach: the repository OIDC subject template is not GitHub's default",
    "off_register_run": "Dispatcher integrity breach: the dispatcher bot started or re-ran a run outside its register",
    "disabled_workflow": "Dispatcher integrity breach: a workflow is disabled (check with the operator first)",
    BLIND: "Dispatcher integrity reads are blind: a read failed or a run listing was truncated",
}


class ProxyBlocked(RuntimeError):
    pass


class _Blind(RuntimeError):
    pass


def acceptance_for(check: str) -> str:
    return f"bin/venv-python -m scripts.convergence_health --dispatch-integrity-probe {check}"


def _is_proxy_block(exc: BaseException) -> bool:
    if not isinstance(exc, urllib.error.HTTPError) or exc.code != 403:
        return False
    headers: Any = exc.headers or {}
    return not any(k.lower() == "x-github-request-id" for k in headers.keys())


def _read(caller: Callable[[str], Any], url: str) -> Any:
    try:
        data = caller(url)
    except Exception as exc:  # noqa: BLE001
        if _is_proxy_block(exc):
            raise ProxyBlocked(url.split("?")[0]) from None
        raise _Blind(f"{type(exc).__name__} on {url.split('?')[0]}") from None
    if data is None:
        raise _Blind(f"no payload from {url.split('?')[0]}")
    return data


def _listing(caller: Callable[[str], Any], url: str, pages: int, key: str = "workflow_runs") -> list[dict[str, Any]]:
    runs: list[dict[str, Any]] = []
    for page in range(1, pages + 1):
        batch = _read(caller, f"{url}&per_page={PAGE_SIZE}&page={page}").get(key) or []
        runs.extend(batch)
        if len(batch) < PAGE_SIZE:
            return runs
    raise _Blind(f"listing truncated at {pages} full pages")


def _login(run: dict[str, Any], key: str) -> Optional[str]:
    return (run.get(key) or {}).get("login")


def off_register_findings(runs: list[dict[str, Any]], register: dict[str, Any]) -> list[str]:
    bot = register["app"]["bot_login"]
    allowed = {row["workflow"] for row in register["schedules"].values()}
    findings = set()
    for run in runs:
        workflow = str(run.get("path") or "").rsplit("/", 1)[-1]
        actor, trigger = _login(run, "actor"), _login(run, "triggering_actor")
        by_bot = bot in (actor, trigger)
        on_register = workflow in allowed
        if run.get("event") == "workflow_dispatch" and by_bot and not on_register:
            findings.add(f"run:{run.get('id')}:{workflow}")
        elif on_register and actor == bot and run.get("head_branch") != "main":
            findings.add(f"run:{run.get('id')}:{workflow}")
        elif int(run.get("run_attempt") or 1) > 1 and trigger == bot and not on_register:
            findings.add(f"run:{run.get('id')}:{workflow}")
    return sorted(findings)


def _check(cls: str, caller: Callable[[str], Any], api_base: str, now: datetime, register: dict[str, Any]) -> list[str]:
    if cls == "oidc_subject":
        data = _read(caller, f"{api_base}/actions/oidc/customization/sub")
        return [] if data.get("use_default") is True else ["oidc_subject:custom"]
    if cls == "off_register_run":
        since = (now - WINDOW).strftime("%Y-%m-%dT%H:%M:%SZ")
        bot = urllib.parse.quote(register["app"]["bot_login"], safe="")
        runs_a = _listing(caller, f"{api_base}/actions/runs?actor={bot}&created=>={since}", PAGES_ACTOR)
        runs_b = _listing(caller, f"{api_base}/actions/runs?created=>={since}", PAGES_ALL)
        unique = {run.get("id"): run for run in runs_a + runs_b}
        return off_register_findings(list(unique.values()), register)
    workflows = _listing(caller, f"{api_base}/actions/workflows?", PAGES_WORKFLOWS, key="workflows")
    return sorted(
        f"workflow:{str(wf.get('path') or '').rsplit('/', 1)[-1]}:{wf.get('state')}"
        for wf in workflows
        if str(wf.get("state") or "").startswith("disabled_")
    )


def assess(
    caller: Callable[[str], Any],
    api_base: str,
    now: Optional[datetime] = None,
    register: Optional[dict[str, Any]] = None,
    checks: tuple[str, ...] = BREACH_CLASSES,
) -> dict[str, tuple[str, list[str]]]:
    """{class: (verdict, findings)}; verdict is clear, breach, blind or proxy_blocked. Only `checks` are read."""
    now = now or datetime.now(timezone.utc)
    register = register or load_register()
    result: dict[str, tuple[str, list[str]]] = {}
    for cls in checks:
        try:
            findings = _check(cls, caller, api_base, now, register)
        except ProxyBlocked as exc:
            result[cls] = ("proxy_blocked", [str(exc)])
            continue
        except _Blind as exc:
            result[cls] = ("blind", [str(exc)])
            continue
        result[cls] = ("breach" if findings else "clear", findings)
    return result


def status_line(result: dict[str, tuple[str, list[str]]]) -> str:
    parts = " ".join(f"{cls}={'blind' if result[cls][0] == 'proxy_blocked' else result[cls][0]}" for cls in BREACH_CLASSES)
    return f"[convergence_health] dispatch_integrity: {parts}"


_FINDINGS_RE = re.compile(r"findings=(\S*)")


def _class_matches(cls: str) -> Callable[[dict[str, Any]], bool]:
    token = re.compile(r"(?:^|\s)class=" + re.escape(cls) + r"(?:\s|$)")
    return lambda rec: token.search(rec.get("context") or "") is not None


def _fields(cls: str, context: str) -> dict[str, Any]:
    return {
        "title": _TITLES[cls],
        "file": MODULE_FILE,
        "status": "open",
        "source": SOURCE,
        "priority": "Medium" if cls == BLIND else "High",
        "effort": "S",
        "risk": "low" if cls == BLIND else "high",
        "automatable": False,
        "verification_tier": "V2",
        "context": context,
        "acceptance": acceptance_for(cls),
    }


def _episode(
    cls: str,
    over: bool,
    findings: list[str],
    portal: Optional[Callable[[str, dict[str, Any]], Any]],
    rows: list[dict[str, Any]],
) -> str:
    context = f"class={cls} findings={','.join(findings)}"

    def build_update(existing: dict[str, Any]) -> Optional[dict[str, Any]]:
        match = _FINDINGS_RE.search(existing.get("context") or "")
        return None if match and match.group(1) == ",".join(findings) else {"context": context}

    def build_close(existing: dict[str, Any]) -> dict[str, Any]:
        return {"status": "closed", "resolution": f"Cleared: every {cls} read succeeded on a later tick."}

    result = run_episode(
        source=SOURCE,
        over_threshold=over,
        build_fields=lambda: _fields(cls, context),
        build_update=build_update,
        build_close=build_close,
        sub_key=_class_matches(cls),
        sub_key_fields=("context",),
        portal_caller=portal,
        rows=rows,
    )
    return str(result["action"])


def run_tick(
    caller: Callable[[str], Any],
    api_base: str,
    now: Optional[datetime] = None,
    register: Optional[dict[str, Any]] = None,
    diagnose: bool = False,
    portal: Optional[Callable[[str, dict[str, Any]], Any]] = None,
    rows: Optional[list[dict[str, Any]]] = None,
    reader: Any = None,
) -> dict[str, str]:
    """One convergence-health tick: read, print the status line, file or close episodes. Returns {class: action}."""
    result = assess(caller, api_base, now=now, register=register)
    print(status_line(result))
    blind = [f"{cls}:{verdict}" for cls, (verdict, _) in result.items() if verdict in ("blind", "proxy_blocked")]
    breaches = {cls: findings for cls, (verdict, findings) in result.items() if verdict == "breach"}
    if diagnose:
        print(f"[convergence_health] dispatch_integrity diagnose: breaches={breaches} blind={blind}")
        return {"diagnose": "diagnose"}
    if rows is None:
        rows = find_recs(SOURCE, status="open", reader=reader)
    actions = {cls: _episode(cls, True, findings, portal, rows) for cls, findings in breaches.items()}
    actions[BLIND] = _episode(BLIND, bool(blind), sorted(blind), portal, rows)
    return actions


def _github_context() -> tuple[Callable[[str], Any], str]:
    import os  # noqa: PLC0415

    from scripts import repo_identity  # noqa: PLC0415
    from scripts.convergence_health.approvals import _make_github_caller  # noqa: PLC0415

    token = os.environ.get("GH_TOKEN", "") or os.environ.get("GITHUB_TOKEN", "")
    return _make_github_caller(token), f"https://api.github.com/repos/{repo_identity.full_name()}"


def tick(profile: Optional[str] = None) -> dict[str, str]:
    import os  # noqa: PLC0415

    caller, api_base = _github_context()
    diagnose = bool(os.environ.get("CONVERGENCE_HEALTH_DIAGNOSE"))
    rows = None if diagnose else find_recs(SOURCE, status="open", profile=profile)
    return run_tick(caller, api_base, diagnose=diagnose, rows=rows)


def probe(check: Optional[str] = None) -> int:
    """0 iff the class (or every breach class) is clear now, 1 on a finding or a blind read, 2 on an egress-proxy 403."""
    if check is not None and check not in CLASSES:
        print(f"[convergence_health] dispatch_integrity_probe: unknown check {check!r}")
        return 1
    checks = BREACH_CLASSES if check in (None, BLIND) else (check,)
    caller, api_base = _github_context()
    result = assess(caller, api_base, checks=checks)
    for cls, (verdict, findings) in result.items():
        print(f"[convergence_health] dispatch_integrity_probe {cls}={verdict} {findings}")
    verdicts = {verdict for verdict, _ in result.values()}
    if "proxy_blocked" in verdicts:
        return 2
    return 0 if verdicts == {"clear"} else 1
