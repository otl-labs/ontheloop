"""validate_persona_verdict_egress: the live tree is green, one red fixture per refused shape, registered in both tiers."""

from __future__ import annotations

import copy
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.misc import validate_persona_verdict_egress as v

LIVE = Path(_common.ROOT)
KEYED = ".github/workflows/persona-verdict-keyed.yml"
SHA = "1d88384fd173af6ecb012dfd0264a306b711241a"  # pragma: allowlist secret
CHECKOUT = "actions/checkout@" + "a" * 40
SELF_CHECK = v.RUN_PREFIX + "egress-self-check"
Docs = dict[str, Any]


def _filter_step() -> dict[str, Any]:
    return {
        "uses": f"step-security/harden-runner@{SHA}",
        "with": {
            "egress-policy": "block",
            "allowed-endpoints": "sts.amazonaws.com:443",
            "disable-telemetry": True,
            "disable-sudo-and-containers": True,
        },
    }


def _keyed_job(**extra: Any) -> dict[str, Any]:
    job: dict[str, Any] = {
        "runs-on": "ubuntu-latest",
        "permissions": {"id-token": "write", "contents": "read"},
        "steps": [
            _filter_step(),
            {"uses": CHECKOUT, "with": {"path": "main", "persist-credentials": False}},
            {"run": v.MKDIR},
            {"run": SELF_CHECK},
            {"run": "echo later"},
        ],
    }
    job.update(extra)
    return job


def _good() -> Docs:
    cfg = yaml.safe_load((LIVE / v.LIST_FILE).read_text(encoding="utf-8"))
    cfg["jobs"][KEYED] = {"smoke": ["sts.amazonaws.com:443"]}
    probe = yaml.safe_load((LIVE / v.PROBE).read_text(encoding="utf-8"))
    keyed = {
        "name": "persona-verdict-keyed",
        "on": {"workflow_dispatch": {}},
        "permissions": {},
        "jobs": {"smoke": _keyed_job()},
    }
    return {"cfg": cfg, "probe": probe, "keyed": keyed, "version": cfg["filter"]["version"]}


def _tree(tmp_path: Path, edit: Callable[[Docs], None] | None = None) -> Path:
    docs = copy.deepcopy(_good())
    if edit:
        edit(docs)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / v.LIST_FILE).write_text(yaml.safe_dump(docs["cfg"]), encoding="utf-8")
    wf = tmp_path / ".github/workflows"
    wf.mkdir(parents=True, exist_ok=True)
    (wf / "persona-verdict-egress-probe.yml").write_text(_render(docs["probe"], docs["version"]), encoding="utf-8")
    if docs["keyed"] is not None:
        (wf / "persona-verdict-keyed.yml").write_text(_render(docs["keyed"], docs["version"]), encoding="utf-8")
    return tmp_path


def _render(doc: dict[str, Any], version: str) -> str:
    lines = []
    for line in yaml.safe_dump(doc).splitlines():
        lines.append(f"{line}  # {version}" if "uses: step-security/harden-runner@" in line and version else line)
    return "\n".join(lines) + "\n"


def _problems(tmp_path: Path, edit: Callable[[Docs], None] | None) -> list[str]:
    return v.check_tree(_tree(tmp_path, edit))[0]


def _job(d: Docs) -> dict[str, Any]:
    return d["keyed"]["jobs"]["smoke"]


def _step0(d: Docs) -> dict[str, Any]:
    return _job(d)["steps"][0]


def _red(name: str, message: str, edit: Callable[[Docs], None]) -> Any:
    return pytest.param(edit, message, id=name)


def _set_with(key: str, value: Any) -> Callable[[Docs], None]:
    return lambda d: _step0(d)["with"].update({key: value})


def _set_endpoints(hosts: str, row: list[str] | None = None) -> Callable[[Docs], None]:
    def edit(d: Docs) -> None:
        _step0(d)["with"]["allowed-endpoints"] = hosts
        if row is not None:
            d["cfg"]["jobs"][KEYED]["smoke"] = row

    return edit


def _insert(index: int, step: dict[str, Any]) -> Callable[[Docs], None]:
    return lambda d: _job(d)["steps"].insert(index, step)


def _checkout_with(**extra: Any) -> Callable[[Docs], None]:
    return lambda d: _job(d)["steps"][1]["with"].update(extra)


def _unrowed(job: dict[str, Any], workflow: dict[str, Any] | None = None) -> Callable[[Docs], None]:
    def edit(d: Docs) -> None:
        d["keyed"]["jobs"]["other"] = job
        if workflow:
            d["keyed"].update(workflow)

    return edit


def _plain_job(**extra: Any) -> dict[str, Any]:
    job: dict[str, Any] = {"runs-on": "ubuntu-latest", "steps": [{"run": "echo hi"}]}
    job.update(extra)
    return job


def _drop_self_check(d: Docs) -> None:
    del _job(d)["steps"][3]


def _prefixless(d: Docs) -> None:
    _job(d)["steps"][3] = {"run": v.PROBE_PREFIX + "egress-self-check"}


def _stale_row(d: Docs) -> None:
    d["cfg"]["jobs"][KEYED]["gone"] = ["sts.amazonaws.com:443"]


def _no_row(d: Docs) -> None:
    del d["cfg"]["jobs"][KEYED]


def _wildcard(d: Docs) -> None:
    d["cfg"]["wildcard_residuals"] = ["*.example.com:443"]
    _set_endpoints("*.example.com:443", ["*.example.com:443"])(d)


def _probe_job(d: Docs, name: str = "egress-probe") -> dict[str, Any]:
    return d["probe"]["jobs"][name]


RED = [
    _red("step0-missing", "step 0 must use exactly", lambda d: _job(d)["steps"].pop(0)),
    _red("no-steps", "no steps", lambda d: _job(d).update({"steps": []})),
    _red("filter-second", "step 0 must use exactly", _insert(0, {"uses": CHECKOUT})),
    _red(
        "wrong-sha", "step 0 must use exactly", lambda d: _step0(d).update({"uses": "step-security/harden-runner@" + "b" * 40})
    ),
    _red("wrong-version-comment", "version comment", lambda d: d.update({"version": "v1.0.0"})),
    _red("no-version-comment", "version comment", lambda d: d.update({"version": ""})),
    _red("audit-mode", "egress-policy must be block", _set_with("egress-policy", "audit")),
    _red("telemetry-on", "disable-telemetry must be true", _set_with("disable-telemetry", False)),
    _red(
        "sudo-and-containers-on", "disable-sudo-and-containers must be true", _set_with("disable-sudo-and-containers", False)
    ),
    _red("extra-host", "differ from the job's row", _set_endpoints("sts.amazonaws.com:443 example.com:443")),
    _red("missing-host", "differ from the job's row", _set_endpoints("", ["sts.amazonaws.com:443"])),
    _red("expression-in-with", "holds an expression", _set_with("allowed-endpoints", "${{ vars.X }}")),
    _red("policy-store-input", "with: keys must be exactly", _set_with("policy", "x")),
    _red("if-on-step0", "if: or continue-on-error", lambda d: _step0(d).update({"if": "always()"})),
    _red("continue-on-error-on-step0", "if: or continue-on-error", lambda d: _step0(d).update({"continue-on-error": True})),
    _red("if-on-self-check", "must be the self-check", lambda d: _job(d)["steps"][3].update({"if": "always()"})),
    _red(
        "continue-on-error-on-self-check",
        "must be the self-check",
        lambda d: _job(d)["steps"][3].update({"continue-on-error": True}),
    ),
    _red("self-check-absent", "no self-check step", lambda d: _job(d).update({"steps": _job(d)["steps"][:3]})),
    _red("self-check-after-another-step", "must be the self-check", _insert(3, {"run": "echo early"})),
    _red("self-check-missing-between", "must be the self-check", _drop_self_check),
    _red("container", "job-level uses:, container: or services:", lambda d: _job(d).update({"container": "x"})),
    _red("services", "job-level uses:, container: or services:", lambda d: _job(d).update({"services": {"a": {}}})),
    _red("job-level-uses", "job-level uses:, container: or services:", lambda d: _job(d).update({"uses": "./x.yml"})),
    _red("docker-command", "docker command", _insert(4, {"run": "docker run x"})),
    _red("runner-not-latest", "runs-on must be exactly", lambda d: _job(d).update({"runs-on": "ubuntu-22.04"})),
    _red("wildcard", "wildcard endpoint", _wildcard),
    _red("wildcard-residual-without-artifact", "moves no artifact", _wildcard),
    _red("ip-literal", "IP literal", _set_endpoints("1.2.3.4:443", ["1.2.3.4:443"])),
    _red("execute-api", "execute-api", _set_endpoints("a.execute-api.x.com:443", ["a.execute-api.x.com:443"])),
    _red("lambda-url", "lambda-url", _set_endpoints("a.lambda-url.x.com:443", ["a.lambda-url.x.com:443"])),
    _red("endpoint-without-port", "host:port", _set_endpoints("example.com", ["example.com"])),
    _red("stale-row", "names no existing covered job", _stale_row),
    _red("covered-job-no-row", "has no row", _no_row),
    _red("id-token-job-uncovered", "has no row", _unrowed(_plain_job(permissions={"id-token": "write"}))),
    _red("secret-job-uncovered", "has no row", _unrowed(_plain_job(env={"X": "${{ secrets.FOO }}"}))),
    _red("workflow-id-token", "has no row", _unrowed(_plain_job(), {"permissions": {"id-token": "write"}})),
    _red("job-write-all", "has no row", _unrowed(_plain_job(permissions="write-all"))),
    _red("workflow-write-all", "has no row", _unrowed(_plain_job(), {"permissions": "write-all"})),
    _red("workflow-env-secret", "has no row", _unrowed(_plain_job(), {"env": {"X": "${{ secrets.FOO }}"}})),
    _red(
        "workflow-defaults-secret", "has no row", _unrowed(_plain_job(), {"defaults": {"run": {"shell": "${{ secrets.S }}"}}})
    ),
    _red("to-json-secrets", "has no row", _unrowed(_plain_job(env={"X": "${{ toJSON(secrets) }}"}))),
    _red("bracket-secret", "has no row", _unrowed(_plain_job(env={"X": "${{ secrets['FOO'] }}"}))),
    _red("inherit", "has no row", _unrowed(_plain_job(secrets="inherit"))),  # pragma: allowlist secret
    _red("agent-sha-malformed", "agent_sha256", lambda d: d["cfg"]["filter"].update({"agent_sha256": "abc"})),
    _red("agent-sha-missing", "filter needs", lambda d: d["cfg"]["filter"].pop("agent_sha256")),
    _red("filter-sha-malformed", "filter.sha", lambda d: d["cfg"]["filter"].update({"sha": "abc"})),
    _red("filter-supplied-empty", "filter_supplied is empty", lambda d: d["cfg"].update({"filter_supplied": []})),
    _red("residuals-not-a-list", "wildcard_residuals", lambda d: d["cfg"].update({"wildcard_residuals": None})),
    _red("jobs-not-a-mapping", "jobs must be a mapping", lambda d: d["cfg"].update({"jobs": []})),
    _red(
        "secret-in-job-env", "env: holds a secrets expression", lambda d: _job(d).update({"env": {"X": "${{ secrets.FOO }}"}})
    ),
    _red(
        "secret-in-workflow-env",
        "env: holds a secrets expression",
        lambda d: d["keyed"].update({"env": {"X": "${{ secrets.FOO }}"}}),
    ),
    _red(
        "secret-in-pre-step-env",
        "references a secret",
        lambda d: _job(d)["steps"][2].update({"env": {"X": "${{ secrets.FOO }}"}}),
    ),
    _red("secret-in-pre-step-with", "references a secret", _checkout_with(**{"fetch-depth": "${{ secrets.FOO }}"})),
    _red("checkout-token", "with: inputs", _checkout_with(token="x")),
    _red("checkout-ssh-key", "with: inputs", _checkout_with(**{"ssh-key": "x"})),
    _red("checkout-repository", "with: inputs", _checkout_with(repository="o/r")),
    _red("checkout-other-ref", "with: inputs", _checkout_with(ref="main")),
    _red("checkout-persists-credentials", "persist-credentials false", _checkout_with(**{"persist-credentials": True})),
    _red("checkout-unpinned", "not SHA-pinned", lambda d: _job(d)["steps"][1].update({"uses": "actions/checkout@v4"})),
    _red("checkout-without-path-main", "path main", lambda d: _job(d)["steps"][1]["with"].pop("path")),
    _red("unprefixed-self-check", "must be the self-check", _prefixless),
    _red("prefixed-self-check-without-main-checkout", "must be the self-check", lambda d: _job(d)["steps"].pop(1)),
    _red("step-not-a-mapping", "not a mapping", _insert(2, "echo")),
    _red("probe-name", "name must be", lambda d: d["probe"].update({"name": "x"})),
    _red("probe-trigger", "trigger or its paths", lambda d: d["probe"][True]["pull_request"].update({"types": ["opened"]})),
    _red("probe-permissions", "top-level permissions", lambda d: d["probe"].update({"permissions": {"contents": "read"}})),
    _red("probe-job-set", "jobs must be exactly", lambda d: d["probe"]["jobs"].update({"extra": {}})),
    _red("probe-job-key", "keys beyond", lambda d: _probe_job(d).update({"environment": "e"})),
    _red("probe-timeout", "timeout-minutes 5", lambda d: _probe_job(d).update({"timeout-minutes": 10})),
    _red("probe-run-line", "run lines drift", lambda d: _probe_job(d)["steps"][-1].update({"run": "echo"})),
    _red(
        "control-has-filter",
        "without the filter",
        lambda d: _probe_job(d, "egress-control")["steps"].insert(0, _filter_step()),
    ),
]


def test_the_live_tree_is_green() -> None:
    problems, examined = v.check_tree(LIVE)
    assert problems == [] and examined >= 4


def test_the_good_tree_passes(tmp_path: Path) -> None:
    assert _problems(tmp_path, None) == []


@pytest.mark.parametrize(("edit", "message"), RED)
def test_each_red_fixture_fails_with_its_message(tmp_path: Path, edit: Callable[[Docs], None], message: str) -> None:
    problems = _problems(tmp_path, edit)
    assert any(message in p for p in problems), problems


def test_a_job_overriding_a_workflow_level_id_token_stays_uncovered(tmp_path: Path) -> None:
    edit = _unrowed(_plain_job(permissions={"contents": "read"}), {"permissions": {"id-token": "write"}})
    assert _problems(tmp_path, edit) == []


def test_a_job_whose_only_secret_is_the_github_token_stays_uncovered(tmp_path: Path) -> None:
    assert _problems(tmp_path, _unrowed(_plain_job(env={"GH_TOKEN": "${{ secrets.GITHUB_TOKEN }}"}))) == []


def test_a_checkout_ref_of_github_sha_is_allowed(tmp_path: Path) -> None:
    assert _problems(tmp_path, _checkout_with(ref="${{ github.sha }}")) == []


def test_a_wildcard_residual_is_allowed_in_a_job_that_moves_an_artifact(tmp_path: Path) -> None:
    def edit(d: Docs) -> None:
        _wildcard(d)
        _job(d)["steps"].append({"uses": "actions/upload-artifact@" + "c" * 40})

    assert _problems(tmp_path, edit) == []


def test_a_missing_probe_workflow_is_reported(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / v.PROBE).unlink()
    assert any("is missing" in p for p in v.check_tree(root)[0])


def test_a_missing_or_unparseable_list_file_is_reported(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    (root / v.LIST_FILE).write_text("a: [", encoding="utf-8")
    assert any("does not parse" in p for p in v.check_tree(root)[0])
    (root / v.LIST_FILE).unlink()
    assert any("does not parse" in p for p in v.check_tree(root)[0])


def test_a_row_for_a_missing_workflow_is_reported(tmp_path: Path) -> None:
    def edit(d: Docs) -> None:
        d["keyed"] = None
        d["cfg"]["jobs"].pop(KEYED)
        d["cfg"]["jobs"][".github/workflows/persona-verdict-none.yml"] = {"j": ["a.com:443"]}

    assert any("names no existing covered job" in p for p in _problems(tmp_path, edit))


def test_the_registered_check_reports_problems_and_declares_what_it_examined(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[int, str]] = []
    monkeypatch.setattr(registry, "examined", lambda n, unit="": seen.append((n, unit)))
    monkeypatch.setattr(_common, "ROOT", _tree(tmp_path, _no_row))
    failed: list[str] = []
    v.validate_persona_verdict_egress(failed)
    assert failed and seen and seen[0][1] == "workflows"
    monkeypatch.setattr(_common, "ROOT", LIVE)
    failed.clear()
    v.validate_persona_verdict_egress(failed)
    assert failed == []


def test_the_check_is_registered() -> None:
    assert registry.resolve("validate_persona_verdict_egress") is v.validate_persona_verdict_egress
