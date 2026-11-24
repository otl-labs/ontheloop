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
    problems = c.class_problems("w::j", ".github/workflows/persona-verdict.yml", {}, job, [], None)
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
    assert c.class_problems("w", c.PROBE, {}, job, row, "api.deepseek.com:443") == []
    ordinary = v._row_host_problems("w", LIVE, yaml.safe_load((LIVE / v.LIST_FILE).read_text()), row, job["steps"])
    assert ordinary


def test_the_class_row_rule_refuses_what_the_ordinary_rule_admits() -> None:
    job = _docs()["probe"]["jobs"][JOB]
    problems = c.class_problems("w", c.PROBE, {}, job, ["sts.amazonaws.com:443"], "api.deepseek.com:443")
    assert any("row must be exactly" in p for p in problems)


def test_primary_host_is_the_primary_role_tiers_provider() -> None:
    data = yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8"))
    assert c.primary_host(LIVE, data) == ("api.deepseek.com:443", [])
    assert c.primary_host(LIVE, {"provider_hosts": {}})[0] is None


def test_container_floor_verb_is_floor_only_in_the_class() -> None:
    assert fl.is_floor_line("sudo " + fl.PROBE_PREFIX + c.CONTAINER_VERB)
    job = _docs()["probe"]["jobs"][JOB]
    assert c.is_container_job(job) and not c.is_container_job({"steps": [{"run": "sudo " + fl.PROBE_PREFIX + "egress-floor"}]})


def test_primary_host_reports_a_missing_contract_and_a_missing_primary_row(tmp_path: Path) -> None:
    host, problems = c.primary_host(tmp_path, {})
    assert host is None and "does not give the tier models" in problems[0]
    contract = tmp_path / c.INFERENCE_PROVIDER
    contract.parent.mkdir(parents=True)
    contract.write_text("tier_model: {tiers: [{role: other}]}\nmodel_id_formats: {litellm_tier_models: {}}\n")
    host, problems = c.primary_host(tmp_path, {})
    assert host is None and "no primary-role tier row" in problems[0]


def test_write_all_permissions_count_as_id_token_write() -> None:
    job = {"permissions": "write-all", "steps": []}
    assert any("id-token: write" in p for p in c.class_problems("w", c.PROBE, {}, job, [], None))
