"""scripts/ci/terraform_github_apply.sh under stubs (Decision 248, PLAN-terraform-github-gated-apply VP 9).

The real script, gate, reporter and quiet_run.sh run from a copy of scripts/ci under tmp_path, so terraform's
-chdir and plan.bin stay there. Stub aws, curl, openssl, date and terraform on PATH log each call (and their argv)
to files; the sentinel key and token must never reach stdout (but the add-mask line), stderr, an output file or any
argv log. The token must reach curl only on stdin and Terraform only as TF_VAR_github_token.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from scripts.ci import terraform_github_apply_gate as gate

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / "scripts" / "ci"
COPIED = (
    "terraform_github_apply.sh",
    "terraform_github_apply_gate.py",
    "terraform_github_plan_report.py",
    "plan_names_core.py",
    "quiet_run.sh",
)
KEY = "stub-apply-app-private-key-sentinel"  # pragma: allowlist secret
TOKEN = "ghs_stubInstallationTokenSentinel0123"  # pragma: allowlist secret
CLOCK = 1_800_000_000
PROBE_RUN = "424242"

_AWS = """#!/usr/bin/env bash
echo "aws $*" >> "$STUB_ARGV"
echo "aws" >> "$STUB_CALLS"
if [ "${AWS_STUB:-ok}" = fail ]; then echo "AccessDenied for $STUB_KEY" >&2; exit 254; fi
printf '{"app_id":"11","installation_id":"22","private_key":"%s"}\\n' "$STUB_KEY"
"""

_OPENSSL = """#!/usr/bin/env bash
echo "openssl $*" >> "$STUB_ARGV"
case "$1" in
    base64) base64 -w0 ;;
    dgst)
        key=$(cat "$4")
        [ "$key" = "$STUB_KEY" ] && echo "openssl sign key=ok" >> "$STUB_CALLS" || echo "openssl sign key=bad" >> "$STUB_CALLS"
        printf 'signature' ;;
    *) exit 99 ;;
esac
"""

_DATE = """#!/usr/bin/env bash
[ "$1" = "+%s" ] && { echo "$STUB_CLOCK"; exit 0; }
exec /usr/bin/date "$@"
"""

_CURL = """#!/usr/bin/env python3
import base64, json, os, sys
argv = sys.argv[1:]
open(os.environ["STUB_ARGV"], "a").write("curl " + " ".join(argv) + "\\n")
opts, url, i = {}, "", 0
while i < len(argv):
    a = argv[i]
    if a in ("-o", "-D", "-w", "-X", "-H", "--data"):
        opts[a] = argv[i + 1]
        i += 2
    elif a.startswith("-"):
        i += 1
    else:
        url = a
        i += 1
headers = sys.stdin.read() if opts.get("-H") == "@-" else ""
bearer = next((ln.split(" ", 2)[2] for ln in headers.splitlines() if ln.startswith("Authorization: Bearer ")), "")
auth = "token" if bearer == os.environ["STUB_TOKEN"] else "none" if not bearer else "jwt"
extra = ""
if auth == "jwt":
    seg = bearer.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4)))
    extra = f" iat={claims['iat']} exp={claims['exp']} iss={claims['iss']}"
method = opts.get("-X", "GET")
path = url.split("api.github.com", 1)[-1]
open(os.environ["STUB_CALLS"], "a").write(f"curl {method} {path} auth={auth}{extra}\\n")
env = os.environ
status, body, hdrs = 404, {"message": "Not Found"}, {"x-github-request-id": "R"}
if path.startswith("/app/installations/22/access_tokens"):
    status, body = int(env.get("STUB_MINT_STATUS", "201")), {"token": env["STUB_TOKEN"]}
elif path == "/installation/token":
    status, body = 204, {}
elif path.startswith("/users/"):
    status, body = 200, {"id": 7000 + len(path)}
elif path.endswith("/pending_deployments") and method == "GET":
    status = 200
    body = json.loads(env.get("STUB_PENDING", "[]"))
elif path.endswith("/pending_deployments") and method == "POST":
    status = int(env.get("STUB_POST_STATUS", "403"))
    body = {"message": env.get("STUB_POST_MESSAGE", "Resource not accessible by integration")}
    if env.get("STUB_POST_NO_REQUEST_ID"):
        hdrs = {}
if "-o" in opts and opts["-o"] != "/dev/null":
    open(opts["-o"], "w").write(json.dumps(body))
if "-D" in opts:
    open(opts["-D"], "w").write("HTTP/2 %d\\r\\n" % status + "".join(f"{k}: {v}\\r\\n" for k, v in hdrs.items()))
if "-w" in opts:
    sys.stdout.write(str(status))
"""

_TERRAFORM = """#!/usr/bin/env bash
echo "terraform $*" >> "$STUB_ARGV"
[ "${TF_VAR_github_token:-}" = "$STUB_TOKEN" ] && tok=ok || tok=missing
dir=${1#-chdir=}
shift
sub="$1"
case "$sub" in
    plan) case " $* " in *" -out=plan.bin "*) sub=plan-out ;; *) sub=replan ;; esac ;;
esac
echo "terraform $sub token=$tok reviewers=${TF_VAR_gated_apply_reviewer_user_ids:-}" >> "$STUB_CALLS"
[ "$sub" = "${TF_STUB_FAIL:-}" ] && { echo "terraform error"; exit 1; }
case "$sub" in
    plan-out) echo plan > "$dir/plan.bin" ;;
    show) cat "$TF_STUB_PLAN" ;;
    replan) exit "${TF_STUB_REPLAN_RC:-0}" ;;
esac
exit 0
"""


def _change() -> dict[str, Any]:
    return {
        "address": "github_repository.ontheloop",
        "mode": "managed",
        "type": "github_repository",
        "change": {"actions": ["update"], "before": {"description": "a"}, "after": {"description": "b"}},
    }


def _stub(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o755)


@pytest.fixture()
def env(tmp_path: Path) -> dict[str, str]:
    ci = tmp_path / "repo" / "scripts" / "ci"
    ci.mkdir(parents=True)
    for name in COPIED:
        shutil.copy2(CI / name, ci / name)
    (tmp_path / "repo" / "terraform" / "github").mkdir(parents=True)
    stubs = tmp_path / "bin"
    stubs.mkdir()
    for name, text in (("aws", _AWS), ("openssl", _OPENSSL), ("date", _DATE), ("curl", _CURL), ("terraform", _TERRAFORM)):
        _stub(stubs / name, text)
    runner = tmp_path / "runner"
    runner.mkdir()
    plan = {"resource_changes": [_change()]}
    (tmp_path / "plan.json").write_text(json.dumps(plan), encoding="utf-8")
    e = {k: v for k, v in os.environ.items() if not k.startswith(("TF_VAR_", "GITHUB_", "AWS_"))}
    e.update(
        PATH=f"{stubs}:{e['PATH']}",
        RUNNER_TEMP=str(runner),
        GITHUB_REPOSITORY="otl-labs/ontheloop",
        GITHUB_API_URL="https://api.github.com",
        GITHUB_STEP_SUMMARY=str(tmp_path / "summary.md"),
        GITHUB_OUTPUT=str(tmp_path / "output"),
        GITHUB_ENV=str(tmp_path / "envfile"),
        REVIEWED=json.dumps(gate.rows(plan)),
        GATE_CHANGE="",
        PROBE_RUN_ID="",
        REVIEWER_LOGINS="alice,bob",
        STUB_ARGV=str(tmp_path / "argv.log"),
        STUB_CALLS=str(tmp_path / "calls.log"),
        STUB_KEY=KEY,
        STUB_TOKEN=TOKEN,
        STUB_CLOCK=str(CLOCK),
        TF_STUB_PLAN=str(tmp_path / "plan.json"),
        STUB_PENDING=json.dumps(
            [{"environment": {"id": 9, "name": "deployment-approval-probe"}, "current_user_can_approve": False}]
        ),
    )
    e["TEST_ROOT"] = str(tmp_path)
    return e


def _run(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    script = Path(env["TEST_ROOT"]) / "repo" / "scripts" / "ci" / "terraform_github_apply.sh"
    return subprocess.run(["bash", str(script)], env=env, capture_output=True, text=True, timeout=60)


def _calls(env: dict[str, str]) -> list[str]:
    path = Path(env["STUB_CALLS"])
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def _short(env: dict[str, str]) -> list[str]:
    return [" ".join(c.split()[:3]) if c.startswith("curl") else " ".join(c.split()[:2]) for c in _calls(env)]


def _revoked(env: dict[str, str]) -> bool:
    return _calls(env)[-1] == "curl DELETE /installation/token auth=token"


def _assert_confined(env: dict[str, str], r: subprocess.CompletedProcess[str]) -> None:
    root = Path(env["TEST_ROOT"])
    assert KEY not in r.stdout and KEY not in r.stderr
    assert [line for line in r.stdout.splitlines() if TOKEN in line] in ([], [f"::add-mask::{TOKEN}"])
    assert TOKEN not in r.stderr
    for name in ("summary.md", "output", "envfile", "argv.log"):
        path = root / name
        if path.exists():
            text = path.read_text(encoding="utf-8")
            assert KEY not in text and TOKEN not in text, name
    for path in (root / "runner").rglob("*"):
        assert TOKEN not in path.read_text(encoding="utf-8", errors="replace"), path.name
    assert not (root / "repo" / "terraform" / "github" / "plan.bin").exists()


def test_bash_syntax() -> None:
    assert subprocess.run(["bash", "-n", str(CI / "terraform_github_apply.sh")], capture_output=True).returncode == 0


def test_success_runs_in_order_and_revokes(env: dict[str, str]) -> None:
    r = _run(env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert _short(env) == [
        "aws",
        "openssl sign",
        "curl POST /app/installations/22/access_tokens",
        "curl GET /users/alice",
        "curl GET /users/bob",
        "terraform init",
        "terraform plan-out",
        "terraform show",
        "terraform apply",
        "terraform replan",
        "curl DELETE /installation/token",
    ]
    assert "openssl sign key=ok" in _calls(env)
    assert all("token=ok reviewers=[7012,7010]" in c for c in _calls(env) if c.startswith("terraform"))
    assert all(c.endswith("auth=token") for c in _calls(env) if c.startswith("curl") and "access_tokens" not in c)
    lines = r.stdout.splitlines()
    assert "APPLY_APP_TOKEN_MINTED" in lines and "TF_GITHUB_APPLY_CHECKED add=0 change=1 import=0 other=0" in lines
    assert lines[-2:] == ["TF_GITHUB_APPLY_APPLIED add=0 change=1 import=0 other=0", "TF_GITHUB_APPLY_CONVERGED"]
    assert "github_repository.ontheloop" in Path(env["GITHUB_STEP_SUMMARY"]).read_text(encoding="utf-8")
    _assert_confined(env, r)


def test_the_jwt_is_short_lived_and_minted_on_stdin(env: dict[str, str]) -> None:
    _run(env)
    mint = next(c for c in _calls(env) if "access_tokens" in c)
    claims = dict(kv.split("=") for kv in mint.split()[4:])
    assert claims["iat"] == str(CLOCK - 60) and CLOCK < int(claims["exp"]) <= CLOCK + 600 and claims["iss"] == "11"
    assert "Bearer" not in Path(env["STUB_ARGV"]).read_text(encoding="utf-8")
    data = next(line for line in Path(env["STUB_ARGV"]).read_text(encoding="utf-8").splitlines() if "access_tokens" in line)
    body = json.loads(data.split("--data ", 1)[1].rsplit(" ", 1)[0])
    assert body == {
        "repositories": ["ontheloop"],
        "permissions": {"administration": "write", "metadata": "read", "actions": "read"},
    }


def test_an_empty_admin_plan_applies_nothing(env: dict[str, str]) -> None:
    Path(env["TF_STUB_PLAN"]).write_text('{"resource_changes": []}', encoding="utf-8")
    env["REVIEWED"] = "[]"
    r = _run(env)
    assert r.returncode == 0 and r.stdout.splitlines()[-1] == "TF_GITHUB_APPLY_NOTHING"
    assert "terraform apply" not in _short(env) and _revoked(env)
    _assert_confined(env, r)


def test_a_gate_refusal_never_applies_and_revokes(env: dict[str, str]) -> None:
    env["REVIEWED"] = "[]"
    r = _run(env)
    assert r.returncode == 1 and "TF_GITHUB_APPLY_UNREVIEWED github_repository.ontheloop changed=description" in r.stdout
    assert "terraform apply" not in _short(env) and _revoked(env)
    _assert_confined(env, r)


@pytest.mark.parametrize("step", ["init", "plan-out", "show", "apply"])
def test_a_terraform_failure_stops_and_revokes(env: dict[str, str], step: str) -> None:
    env["TF_STUB_FAIL"] = step
    r = _run(env)
    assert r.returncode == 1 and "TF_GITHUB_APPLY_TERRAFORM_FAILED" in r.stdout
    assert "terraform error" not in r.stdout + r.stderr
    assert _revoked(env)
    _assert_confined(env, r)


def test_a_re_plan_with_changes_fails(env: dict[str, str]) -> None:
    env["TF_STUB_REPLAN_RC"] = "2"
    r = _run(env)
    assert r.returncode == 1 and r.stdout.splitlines()[-1] == "TF_GITHUB_APPLY_NOT_CONVERGED exit=2"
    assert _revoked(env)


@pytest.mark.parametrize("bad", ["12a", "1 2", "$(id)", "-1"])
def test_a_non_numeric_probe_run_id_is_rejected_before_any_call(env: dict[str, str], bad: str) -> None:
    env["PROBE_RUN_ID"] = bad
    r = _run(env)
    assert r.returncode == 1 and "APPLY_APP_PROBE_INCONCLUSIVE approval_probe_run_id is not a run id" in r.stdout
    assert _calls(env) == []


def test_a_key_read_failure_prints_a_marker_only(env: dict[str, str]) -> None:
    env["AWS_STUB"] = "fail"
    r = _run(env)
    assert r.returncode == 1 and r.stdout.strip() == "APPLY_APP_KEY_READ_FAILED"
    assert _short(env) == ["aws"]
    _assert_confined(env, r)


def test_a_mint_failure_stops_before_terraform(env: dict[str, str]) -> None:
    env["STUB_MINT_STATUS"] = "403"
    r = _run(env)
    assert r.returncode == 1 and "APPLY_APP_TOKEN_FAILED status=403" in r.stdout
    assert not any(c.startswith("terraform") for c in _calls(env))
    _assert_confined(env, r)


def _probe_calls(env: dict[str, str]) -> list[str]:
    return [c for c in _short(env) if "pending_deployments" in c]


def test_a_refused_probe_continues_to_the_apply(env: dict[str, str]) -> None:
    env["PROBE_RUN_ID"] = PROBE_RUN
    r = _run(env)
    assert r.returncode == 0, r.stdout
    assert "APPLY_APP_APPROVAL_REFUSED post=TOKEN_LACKS_PERMISSION status=403" in r.stdout
    pending = f"/repos/otl-labs/ontheloop/actions/runs/{PROBE_RUN}/pending_deployments"
    assert _probe_calls(env) == [f"curl GET {pending}", f"curl POST {pending}", f"curl GET {pending}"]
    assert "terraform apply" in _short(env) and _revoked(env)
    _assert_confined(env, r)


def test_a_not_reviewer_refusal_reads_can_approve_false(env: dict[str, str]) -> None:
    env.update(PROBE_RUN_ID=PROBE_RUN, STUB_POST_STATUS="422", STUB_POST_MESSAGE="Unprocessable")
    r = _run(env)
    assert r.returncode == 0, r.stdout
    assert "APPLY_APP_APPROVAL_REFUSED post=NOT_REVIEWER status=422" in r.stdout
    _assert_confined(env, r)


@pytest.mark.parametrize(
    ("over", "marker"),
    [
        ({"STUB_POST_STATUS": "202"}, "APPLY_APP_APPROVAL_ACCEPTED post=APPROVED"),
        ({"STUB_POST_NO_REQUEST_ID": "1"}, "APPLY_APP_PROBE_INCONCLUSIVE post=PROXY_BLOCKED"),
        ({"STUB_POST_MESSAGE": "API rate limit exceeded"}, "APPLY_APP_PROBE_INCONCLUSIVE post=INCONCLUSIVE"),
        ({"STUB_PENDING": "[]"}, "APPLY_APP_PROBE_INCONCLUSIVE pending read status=200"),
        (
            {"STUB_PENDING": json.dumps([{"environment": {"id": 9, "name": "tf-gated-apply"}}])},
            "APPLY_APP_PROBE_INCONCLUSIVE pending read status=200",
        ),
    ],
    ids=["accepted", "proxy-blocked", "rate-limited", "nothing-pending", "other-environment"],
)
def test_a_failing_probe_stops_before_terraform_and_revokes(env: dict[str, str], over: dict[str, str], marker: str) -> None:
    env.update(over, PROBE_RUN_ID=PROBE_RUN)
    r = _run(env)
    assert r.returncode == 1 and marker in r.stdout
    assert not any(c.startswith("terraform") for c in _calls(env)) and _revoked(env)
    _assert_confined(env, r)
