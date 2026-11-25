"""_persona_verdict_container: the live probe workflow passes and each red fixture of the container-job class is refused."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common
from scripts.checks.misc import _persona_verdict_container as c
from scripts.checks.misc import _persona_verdict_floor as fl
from scripts.checks.misc import validate_persona_verdict_egress as v

LIVE = Path(_common.ROOT)
JOB = "egress-container"
Docs = dict[str, Any]


def _docs() -> Docs:
    return {
        "cfg": yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8")),
        "probe": yaml.safe_load((LIVE / v.PROBE).read_text(encoding="utf-8")),
        "version": yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8"))["filter"]["version"],
    }


def _problems(tmp_path: Path, edit: Callable[[Docs], None] | None = None) -> list[str]:
    docs = _docs()
    if edit:
        edit(docs)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    for rel in (v.INFERENCE_PROVIDER, ".github/workflows/persona-verdict.yml"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text((LIVE / rel).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / v.LIST_FILE).write_text(yaml.safe_dump(docs["cfg"]), encoding="utf-8")
    wf = tmp_path / ".github/workflows"
    wf.mkdir(parents=True, exist_ok=True)
    text = "\n".join(
        f"{ln}  # {docs['version']}" if "uses: step-security/harden-runner@" in ln else ln
        for ln in yaml.safe_dump(docs["probe"]).splitlines()
    )
    (wf / "persona-verdict-egress-probe.yml").write_text(text + "\n", encoding="utf-8")
    return v.check_tree(tmp_path)[0]


def _steps(d: Docs) -> list[dict[str, Any]]:
    return d["probe"]["jobs"][JOB]["steps"]


def _launcher(d: Docs) -> dict[str, Any]:
    return next(s for s in _steps(d) if c.is_launcher_line(str(s.get("run", ""))))


def _job(d: Docs) -> dict[str, Any]:
    return d["probe"]["jobs"][JOB]


RED: list[tuple[str, str, Callable[[Docs], None]]] = [
    ("id-token", "id-token: write", lambda d: _job(d).update({"permissions": {"contents": "read", "id-token": "write"}})),
    ("secret", "secret", lambda d: _steps(d)[1].update({"env": {"K": "${{ secrets.K }}"}})),
    ("environment", "environment", lambda d: _job(d).update({"environment": "x"})),
    ("other-sudo", "only sudo lines", lambda d: _steps(d).insert(3, {"name": "s", "id": "s", "run": "sudo id"})),
    ("launcher-text", "exactly one launcher", lambda d: _launcher(d).update({"run": _launcher(d)["run"] + " extra"})),
    ("launcher-if", "keys must be exactly", lambda d: _launcher(d).update({"if": "always()"})),
    ("launcher-continue", "keys must be exactly", lambda d: _launcher(d).update({"continue-on-error": True})),
    ("launcher-shell", "keys must be exactly", lambda d: _launcher(d).update({"shell": "bash"})),
    ("launcher-env", "keys must be exactly", lambda d: _launcher(d).update({"env": {"A": "b"}})),
    ("launcher-wd", "keys must be exactly", lambda d: _launcher(d).update({"working-directory": "x"})),
    ("docker", "docker command", lambda d: _steps(d).insert(3, {"run": "docker ps"})),
    ("second-host", "row must be exactly", lambda d: d["cfg"]["jobs"][v.PROBE][JOB].append("api.anthropic.com:443")),
    ("no-blob", "row must be exactly", lambda d: d["cfg"]["jobs"][v.PROBE][JOB].remove(c.BLOB_HOST)),
    ("no-final", "last step", lambda d: _steps(d).pop()),
    (
        "control-with-filter",
        "without the filter",
        lambda d: d["probe"]["jobs"]["egress-container-control"]["steps"].insert(0, {"uses": "step-security/harden-runner@x"}),
    ),
    (
        "trigger-without-image",
        "trigger",
        lambda d: d["probe"][True]["pull_request"]["paths"].remove("config/persona_image.yaml"),
    ),
]


def test_the_live_probe_workflow_passes(tmp_path: Path) -> None:
    assert _problems(tmp_path) == []


@pytest.mark.parametrize(("name", "message", "edit"), RED, ids=[r[0] for r in RED])
def test_each_red_fixture_is_refused(tmp_path: Path, name: str, message: str, edit: Callable[[Docs], None]) -> None:
    problems = _problems(tmp_path, edit)
    assert any(message in p for p in problems), problems


def test_launcher_before_the_first_self_check_is_refused(tmp_path: Path) -> None:
    def edit(d: Docs) -> None:
        steps = _steps(d)
        steps.insert(2, steps.pop(next(i for i, s in enumerate(steps) if c.is_launcher_line(str(s.get("run", ""))))))

    assert any("before the first self-check" in p for p in _problems(tmp_path, edit))


def test_a_container_job_outside_the_probe_workflow_is_refused() -> None:
    job = copy.deepcopy(_docs()["probe"]["jobs"][JOB])
    problems = c.class_problems("w::j", ".github/workflows/persona-verdict.yml", {}, job, [], {})
    assert any("must be in" in p for p in problems)


def test_the_blob_host_in_a_job_outside_the_class_is_refused(tmp_path: Path) -> None:
    def edit(d: Docs) -> None:
        d["cfg"]["jobs"][v.PROBE]["egress-probe"].append(c.BLOB_HOST)
        d["probe"]["jobs"]["egress-probe"]["steps"][0]["with"]["allowed-endpoints"] += " " + c.BLOB_HOST

    assert any("outside sts and ssm" in p for p in _problems(tmp_path, edit))


def test_a_lone_provider_host_in_a_job_outside_the_class_is_refused(tmp_path: Path) -> None:
    def edit(d: Docs) -> None:
        d["cfg"]["jobs"][v.PROBE]["egress-probe"].append("api.deepseek.com:443")
        d["probe"]["jobs"]["egress-probe"]["steps"][0]["with"]["allowed-endpoints"] += " api.deepseek.com:443"

    assert any("outside sts and ssm" in p for p in _problems(tmp_path, edit))


def test_the_class_row_rule_admits_what_the_ordinary_rule_refuses() -> None:
    job = _docs()["probe"]["jobs"][JOB]
    row = ["api.deepseek.com:443", c.BLOB_HOST]
    assert c.class_problems("w", c.PROBE, {}, job, row, {"primary": "api.deepseek.com:443"}) == []
    ordinary = v._row_host_problems("w", LIVE, yaml.safe_load((LIVE / v.LIST_FILE).read_text()), row, job["steps"])
    assert ordinary


def test_the_class_row_rule_refuses_what_the_ordinary_rule_admits() -> None:
    job = _docs()["probe"]["jobs"][JOB]
    problems = c.class_problems("w", c.PROBE, {}, job, ["sts.amazonaws.com:443"], {"primary": "api.deepseek.com:443"})
    assert any("row must be exactly" in p for p in problems)


def test_role_hosts_maps_each_committed_role_to_its_provider() -> None:
    data = yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8"))
    assert c.role_hosts(LIVE, data) == (
        {"primary": "api.deepseek.com:443", "warm_fetched_escape_hatch": "api.anthropic.com:443"},
        [],
    )
    assert c.role_hosts(LIVE, {"provider_hosts": {}})[1]
    shared = {"provider_hosts": {"deepseek/": "h:443", "anthropic/": "h:443"}}
    assert any("share a provider host" in p for p in c.role_hosts(LIVE, shared)[1])


def test_container_floor_verb_is_floor_only_in_the_class() -> None:
    assert fl.is_floor_line("sudo " + fl.PROBE_PREFIX + c.CONTAINER_VERB)
    job = _docs()["probe"]["jobs"][JOB]
    assert c.is_container_job(job) and not c.is_container_job({"steps": [{"run": "sudo " + fl.PROBE_PREFIX + "egress-floor"}]})


def test_role_hosts_reports_a_missing_contract_and_a_missing_primary_row(tmp_path: Path) -> None:
    hosts, problems = c.role_hosts(tmp_path, {})
    assert hosts == {} and "does not give the tier models" in problems[0]
    contract = tmp_path / c.INFERENCE_PROVIDER
    contract.parent.mkdir(parents=True)
    contract.write_text("tier_model: {tiers: [{role: other}]}\nmodel_id_formats: {litellm_tier_models: {}}\n")
    hosts, problems = c.role_hosts(tmp_path, {})
    assert hosts == {} and "no primary-role tier row" in problems[0]


def test_write_all_permissions_count_as_id_token_write() -> None:
    job = {"permissions": "write-all", "steps": []}
    assert any("id-token: write" in p for p in c.class_problems("w", c.PROBE, {}, job, [], {}))


def _run_job() -> tuple[Docs, Docs, list[Any], dict[str, str]]:
    wf = yaml.safe_load((LIVE / c.VERDICT).read_text(encoding="utf-8"))
    cfg = yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8"))
    row = cfg["jobs"][c.VERDICT]["run"]
    return wf, copy.deepcopy(wf["jobs"]["run"]), row, c.role_hosts(LIVE, cfg)[0]


def _run_problems(edit: Callable[[Docs, list[Any]], None] | None = None, primary: bool = True) -> list[str]:
    wf, job, row, host = _run_job()
    if edit:
        edit(job, row)
    return c.run_job_problems("w::run", wf, job, row, host if primary else {})


def _named(job: Docs, name: str) -> dict[str, Any]:
    return next(s for s in job["steps"] if s.get("name") == name)


RUN_RED: list[tuple[str, str, Callable[[Docs, list[Any]], None]]] = [
    ("permissions", "permissions must be exactly", lambda j, r: j.update({"permissions": {"contents": "read"}})),
    ("environment", "persona-verdict Environment", lambda j, r: j.pop("environment")),
    ("inherit", "inherits secrets", lambda j, r: j.update({"secrets": "inherit"})),
    ("third-sudo", "only sudo lines", lambda j, r: j["steps"].insert(3, {"id": "x", "run": "sudo id"})),
    ("one-probe", "needs the floor step", lambda j, r: j["steps"].remove(_named(j, "Probe from the container again"))),
    (
        "probe-keys",
        "probe step's keys",
        lambda j, r: _named(j, "Probe from the container again").update({"continue-on-error": True}),
    ),
    ("persona-env", "persona step's keys", lambda j, r: _named(j, "Run the fixture persona")["env"].update({"X": "y"})),
    (
        "secret-elsewhere",
        "outside the persona step",
        lambda j, r: _named(j, "Harden the runner").update({"env": {"A": "${{ secrets.X }}"}}),
    ),
    (
        "wrong-secret",
        "exactly secrets.AWS_ACCOUNT_ID",
        lambda j, r: _named(j, "Run the fixture persona")["env"].update({"AWS_ACCOUNT_ID": "${{ secrets.OTHER }}"}),
    ),
    ("last-step", "final self-check", lambda j, r: j["steps"].pop()),
    ("row", "run job's row must be exactly", lambda j, r: r.append("evil.example:443")),
]


def test_the_live_run_job_passes_the_class_rules() -> None:
    assert _run_problems() == []
    assert _run_problems(primary=False) == []


@pytest.mark.parametrize(("name", "message", "edit"), RUN_RED, ids=[r[0] for r in RUN_RED])
def test_each_run_job_red_fixture_is_refused(name: str, message: str, edit: Callable[[Docs, list[Any]], None]) -> None:
    assert any(message in p for p in _run_problems(edit)), (name, _run_problems(edit))


def test_probe_ordering_around_the_persona_step_is_enforced() -> None:
    def early(j: Docs, r: list[Any]) -> None:
        steps = j["steps"]
        first = next(i for i, s in enumerate(steps) if s.get("name") == "Probe from the container")
        steps.insert(0, steps.pop(first))

    def late(j: Docs, r: list[Any]) -> None:
        steps = j["steps"]
        i = next(i for i, s in enumerate(steps) if s.get("name") == "Probe from the container again")
        steps.insert(len(steps) - 3, steps.pop(i))

    assert any("must follow the first self-check" in p for p in _run_problems(early))
    assert any("second probe must be" in p for p in _run_problems(late))


def _fallback_problems(row_edit: Callable[[list[Any]], list[Any]] | None = None, job_name: str = "run-fallback") -> list[str]:
    wf = yaml.safe_load((LIVE / c.VERDICT).read_text(encoding="utf-8"))
    cfg = yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8"))
    hosts = c.role_hosts(LIVE, cfg)[0]
    row = list(cfg["jobs"][c.VERDICT][job_name])
    if row_edit:
        row = row_edit(row)
    return c.run_job_problems(f"{c.VERDICT}::{job_name}", wf, copy.deepcopy(wf["jobs"][job_name]), row, hosts)


def test_class_row_holds_its_roles_one_provider_host() -> None:
    assert _fallback_problems() == [] and _fallback_problems(job_name="run") == []
    swap = lambda row: ["api.deepseek.com:443" if h == "api.anthropic.com:443" else h for h in row]  # noqa: E731
    assert any("row must be exactly" in p for p in _fallback_problems(swap))
    run_with_fallback = lambda row: ["api.anthropic.com:443" if h == "api.deepseek.com:443" else h for h in row]  # noqa: E731
    assert any("row must be exactly" in p for p in _fallback_problems(run_with_fallback, "run"))
    both = lambda row: [*row, "api.deepseek.com:443"]  # noqa: E731
    assert any("row must be exactly" in p for p in _fallback_problems(both))
    assert any("row must be exactly" in p for p in _fallback_problems(lambda row: [*row, "api.anthropic.com:443"], "run"))


def test_the_persona_role_literal_must_match_the_job() -> None:
    wf = yaml.safe_load((LIVE / c.VERDICT).read_text(encoding="utf-8"))
    cfg = yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8"))
    hosts = c.role_hosts(LIVE, cfg)[0]
    job = copy.deepcopy(wf["jobs"]["run-fallback"])
    _named(job, "Run the fixture persona")["env"]["PERSONA_ROLE"] = "primary"
    problems = c.run_job_problems(f"{c.VERDICT}::run-fallback", wf, job, cfg["jobs"][c.VERDICT]["run-fallback"], hosts)
    assert any("PERSONA_ROLE must be exactly" in p for p in problems)
