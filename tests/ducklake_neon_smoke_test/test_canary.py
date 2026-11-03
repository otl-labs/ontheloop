"""CONCERN: scripts/ducklake_smoke/canary.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_neon_smoke_test.py monolith: canary_rehearsal
orchestration (with the _stub_canary_rehearsal helper + the _FAKE_BRANCH_ID/_FAKE_BRANCH_HOST/
_FAKE_PROJECT_ID constants), _lambda_invoke_cli (with the _stub_invoke_response helper), and
_wait_function_active. Also carries
test_core_sigv4_invoke_interception_canary_family (moved from its plan-prose home in
test_facade.py's "five interception tests" group): it depends on _stub_canary_rehearsal, and the
no-cross-test-import guard forbids test_facade.py importing a single-concern helper from this
module -- see the manifest note in the w7_build_file2.py generation script for the full rationale.
"""

from __future__ import annotations

import types

import pytest

import scripts.ducklake_neon_smoke_test as smoke
from scripts.ducklake_smoke import canary, core
from tests.fixtures.ducklake_smoke_fakes import _Resp

_FAKE_BRANCH_ID = "br-fake-canary-abc"

_FAKE_BRANCH_HOST = "br-fake-canary-abc.us-east-2.aws.neon.tech"

_FAKE_PROJECT_ID = "proj-fake-123"


def _stub_canary_rehearsal(monkeypatch, *, clone_ok=True, ryw_ok=True):
    """Stub all subprocess/AWS/Neon API calls for canary_rehearsal unit tests."""
    _fake_arns = {
        "ducklake-deps-layer": "arn:fake:1",
        "ducklake-extensions-layer": "arn:fake:2",
        "ducklake-pgclient-layer": "arn:fake:3",
    }
    monkeypatch.setattr(canary, "_publish_candidate_layers", lambda **kw: _fake_arns)
    monkeypatch.setattr(
        canary, "_get_function_role_arn", lambda fn, **kw: "arn:aws:iam::ACCOUNT_ID_PLACEHOLDER:role/fake-role"
    )
    monkeypatch.setattr(canary, "_canary_create_function", lambda fn, **kw: None)
    monkeypatch.setattr(canary, "_canary_delete_function", lambda fn, **kw: True)

    monkeypatch.setattr(smoke.neon_api, "fetch_api_key", lambda profile=None: "fake-api-key")
    monkeypatch.delenv("NEON_PROJECT_ID", raising=False)
    monkeypatch.setattr(smoke.neon_api, "resolve_project_id", lambda api_key, name="ducklake-catalog": _FAKE_PROJECT_ID)
    monkeypatch.setattr(
        smoke.neon_api,
        "create_branch",
        lambda api_key, project_id: {"branch_id": _FAKE_BRANCH_ID, "host": _FAKE_BRANCH_HOST},
    )
    monkeypatch.setattr(smoke.neon_api, "delete_branch", lambda api_key, project_id, branch_id: None)

    def _fake_invoke(fn, payload, **kw):
        action = payload.get("action", "")
        if action == "catalog_reinit":
            return {"ok": True, "meta_schema": payload.get("meta_schema"), "reinitialized": True}
        if action == "create_tables":
            return {"ok": True}
        if action == "write":
            return {"ok": True, "ulid": "fake-ulid"}
        if action == "read_current":
            if not ryw_ok:
                return {"rows": []}
            return {"rows": [{"rec_id": payload.get("rec_id", "x")}]}
        if action == "clone_catalog":
            if not clone_ok:
                return {"ok": False, "error": "clone failed"}
            return {"ok": True, "meta_schema": "ducklake_ops", "branch_host": payload.get("branch_host"), "cloned": True}
        return {}

    monkeypatch.setattr(canary, "_lambda_invoke_cli", _fake_invoke)

    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}.lambda-url")
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, p, **kw: _Resp(200, {"ok": True}))

    monkeypatch.setattr(smoke.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""))


def test_canary_rehearsal_happy_path(monkeypatch, capsys):
    """Full happy-path: all gates pass, teardown completes, prints CANARY_REHEARSAL OK."""
    _stub_canary_rehearsal(monkeypatch)
    smoke.canary_rehearsal()
    out = capsys.readouterr().out
    assert "CANARY_REHEARSAL OK" in out
    assert "CANARY_ATTACH OK" in out
    assert "CANARY_RYW OK" in out
    assert "CANARY_CLONE_CATALOG OK" in out


def test_canary_rehearsal_json_output(monkeypatch):
    """json_output=True emits machine-readable JSON with expected keys including branch fields."""
    _stub_canary_rehearsal(monkeypatch)
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        smoke.canary_rehearsal(json_output=True)

    import json

    lines = [ln for ln in buf.getvalue().splitlines() if ln.startswith("{")]
    assert lines, "No JSON line found in output"
    data = json.loads(lines[-1])
    assert data["attach_ok"] is True
    assert data["ryw_ok"] is True
    assert data["clone_ok"] is True
    assert data["torn_down"]["canary_functions"] is True
    assert data["torn_down"]["scratch_meta"] is True
    assert data["torn_down"]["scratch_s3_prefix"] is True
    assert data["torn_down"]["branch"] is True
    assert data["scratch"]["branch_id"] == _FAKE_BRANCH_ID
    assert data["scratch"]["meta_schema"] != "ducklake_ops"
    assert "_canary_rehearsal" in data["scratch"]["data_path"]


def test_canary_rehearsal_scratch_meta_isolated(monkeypatch):
    """Scratch meta-schema must be _CANARY_SCRATCH_META, never the production ducklake_ops."""
    _stub_canary_rehearsal(monkeypatch)
    import contextlib
    import io
    import json

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        smoke.canary_rehearsal(json_output=True)

    data = json.loads([ln for ln in buf.getvalue().splitlines() if ln.startswith("{")][-1])
    assert data["scratch"]["meta_schema"] == smoke._CANARY_SCRATCH_META
    assert data["scratch"]["meta_schema"] != "ducklake_ops"


def test_canary_rehearsal_ryw_fail_raises(monkeypatch):
    """RYW failure (empty rows from reader) raises SmokeTestFailure (Decision 55)."""
    _stub_canary_rehearsal(monkeypatch, ryw_ok=False)
    with pytest.raises(smoke.SmokeTestFailure, match="CANARY_RYW FAIL"):
        smoke.canary_rehearsal()


def test_canary_rehearsal_clone_catalog_fail_raises(monkeypatch):
    """clone_catalog returning ok=False raises SmokeTestFailure."""
    _stub_canary_rehearsal(monkeypatch, clone_ok=False)
    with pytest.raises(smoke.SmokeTestFailure, match="CANARY_CLONE_CATALOG FAIL"):
        smoke.canary_rehearsal()


def test_canary_rehearsal_functions_torn_down_on_error(monkeypatch):
    """Ephemeral Lambda functions are deleted even when a gate fails (finally block)."""
    deleted = []
    _td_arns = {"ducklake-deps-layer": "arn:1", "ducklake-extensions-layer": "arn:2", "ducklake-pgclient-layer": "arn:3"}
    monkeypatch.setattr(canary, "_publish_candidate_layers", lambda **kw: _td_arns)
    monkeypatch.setattr(canary, "_get_function_role_arn", lambda fn, **kw: "arn:role")
    monkeypatch.setattr(canary, "_canary_create_function", lambda fn, **kw: None)
    monkeypatch.setattr(canary, "_canary_delete_function", lambda fn, **kw: deleted.append(fn) or True)
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(core, "_sigv4_invoke", lambda url, p, **kw: _Resp(200, {"ok": True}))
    monkeypatch.setattr(smoke.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    monkeypatch.setattr(smoke.neon_api, "fetch_api_key", lambda profile=None: "fake-api-key")
    monkeypatch.delenv("NEON_PROJECT_ID", raising=False)
    monkeypatch.setattr(smoke.neon_api, "resolve_project_id", lambda api_key, name="ducklake-catalog": _FAKE_PROJECT_ID)
    monkeypatch.setattr(
        smoke.neon_api, "create_branch", lambda api_key, project_id: {"branch_id": _FAKE_BRANCH_ID, "host": _FAKE_BRANCH_HOST}
    )
    monkeypatch.setattr(smoke.neon_api, "delete_branch", lambda api_key, project_id, branch_id: None)

    def _raise_on_attach(fn, payload, **kw):
        if payload.get("action") == "catalog_reinit":
            return {"ok": True}
        if payload.get("action") == "create_tables":
            raise smoke.SmokeTestFailure("attach failed")
        return {}

    monkeypatch.setattr(canary, "_lambda_invoke_cli", _raise_on_attach)

    with pytest.raises(smoke.SmokeTestFailure, match="attach failed"):
        smoke.canary_rehearsal()

    assert len(deleted) == len(smoke._CANARY_FUNCTION_NAMES), "All ephemeral functions must be deleted in finally"


def test_canary_rehearsal_budget_constants_unchanged():
    """CHURN constants used in _canary_churn must still come from ducklake_runtime (Decision 55)."""
    from src.common import ducklake_runtime

    assert smoke.CHURN_WRITERS == ducklake_runtime.CHURN_WRITERS
    assert smoke.COMMIT_LATENCY_BUDGET_MS == ducklake_runtime.COMMIT_LATENCY_BUDGET_MS
    assert smoke.OCC_COLLISION_RATE_BUDGET == ducklake_runtime.OCC_COLLISION_RATE_BUDGET


def test_canary_rehearsal_scratch_meta_teardown_false_on_sigv4_error(monkeypatch):
    """torn_down['scratch_meta'] is False (not raised) when _sigv4_invoke raises (H-2 failure branch)."""
    import contextlib
    import io
    import json as _json

    _stub_canary_rehearsal(monkeypatch)

    def _raise_sigv4(url, payload, **kw):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(core, "_sigv4_invoke", _raise_sigv4)

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        smoke.canary_rehearsal(json_output=True)

    data = _json.loads([ln for ln in buf.getvalue().splitlines() if ln.startswith("{")][-1])
    assert data["torn_down"]["scratch_meta"] is False
    assert data["torn_down"]["canary_functions"] is True


def test_canary_rehearsal_branch_host_passed_to_clone(monkeypatch):
    """Neon branch_host must be included in the clone_catalog event payload (Decision 100)."""
    clone_events = []

    _stub_canary_rehearsal(monkeypatch)

    def _capture_invoke(fn, payload, **kw):
        if payload.get("action") == "clone_catalog":
            clone_events.append(payload.copy())

        if payload.get("action") == "catalog_reinit":
            return {"ok": True}
        if payload.get("action") == "create_tables":
            return {"ok": True}
        if payload.get("action") == "write":
            return {"ok": True, "ulid": "fake-ulid"}
        if payload.get("action") == "read_current":
            return {"rows": [{"rec_id": payload.get("rec_id", "x")}]}
        if payload.get("action") == "clone_catalog":
            return {"ok": True, "meta_schema": "ducklake_ops", "branch_host": payload.get("branch_host"), "cloned": True}
        return {}

    monkeypatch.setattr(canary, "_lambda_invoke_cli", _capture_invoke)

    smoke.canary_rehearsal()

    assert len(clone_events) == 1, "clone_catalog must be invoked exactly once"
    assert clone_events[0].get("branch_host") == _FAKE_BRANCH_HOST, (
        "branch_host from Neon create_branch must be forwarded to clone_catalog event"
    )
    clone_data_path = clone_events[0].get("data_path", "")
    assert clone_data_path.startswith("s3://") and clone_data_path.endswith("/ducklake/"), (
        f"clone_catalog event must carry the production data_path (s3://<bucket>/ducklake/), got {clone_data_path!r}"
    )


def test_canary_rehearsal_delete_branch_called_on_clone_failure(monkeypatch):
    """delete_branch must be called in finally even when clone_catalog returns ok=False."""
    deleted = []

    _stub_canary_rehearsal(monkeypatch, clone_ok=False)
    monkeypatch.setattr(
        smoke.neon_api,
        "delete_branch",
        lambda api_key, project_id, branch_id: deleted.append(branch_id),
    )

    with pytest.raises(smoke.SmokeTestFailure, match="CANARY_CLONE_CATALOG FAIL"):
        smoke.canary_rehearsal()

    assert deleted == [_FAKE_BRANCH_ID], "delete_branch must be called in finally even when clone fails"


def _stub_invoke_response(monkeypatch, return_value):
    """Stub subprocess.run + the response-file read so _lambda_invoke_cli returns `return_value`."""
    monkeypatch.setattr(smoke.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout="", stderr=""))
    import builtins
    import json as _json

    real_open = builtins.open

    def _fake_open(path, *a, **kw):
        if isinstance(path, str) and path.endswith("response.json"):
            import io

            return io.StringIO(_json.dumps(return_value))
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", _fake_open)


def test_lambda_invoke_cli_unwraps_function_url_envelope(monkeypatch):
    """_lambda_invoke_cli unwraps the {statusCode, body} envelope, returning the parsed body dict."""
    envelope = {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": '{"ok": true, "x": 1}'}
    _stub_invoke_response(monkeypatch, envelope)
    out = smoke._lambda_invoke_cli("fn", {"action": "catalog_reinit"}, profile=None, region="eu-west-2")
    assert out == {"ok": True, "x": 1}


def test_lambda_invoke_cli_passes_through_raw_error(monkeypatch):
    """A handler runtime error (no statusCode/body) is returned as-is for the caller to inspect."""
    err = {"errorMessage": "boom", "errorType": "RuntimeError"}
    _stub_invoke_response(monkeypatch, err)
    out = smoke._lambda_invoke_cli("fn", {"action": "create_tables"}, profile=None, region="eu-west-2")
    assert out == err
    assert out.get("ok") is None


def test_lambda_invoke_cli_loud_fails_on_nonzero_rc(monkeypatch):
    """A non-zero CLI exit raises SmokeTestFailure (Decision 55)."""
    monkeypatch.setattr(
        smoke.subprocess, "run", lambda cmd, **kw: types.SimpleNamespace(returncode=254, stdout="", stderr="boom")
    )
    with pytest.raises(smoke.SmokeTestFailure, match="lambda invoke fn failed"):
        smoke._lambda_invoke_cli("fn", {"action": "x"}, profile=None, region="eu-west-2")


def test_wait_function_active_invokes_waiter(monkeypatch):
    """_wait_function_active runs `lambda wait function-active-v2` for the named function."""
    captured = {}

    def _run(cmd, **kw):
        captured["cmd"] = cmd
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(smoke.subprocess, "run", _run)
    smoke._wait_function_active("ducklake-maintenance-canary-ephemeral", profile="p", region="eu-west-2")
    cmd = captured["cmd"]
    assert "wait" in cmd
    assert "function-active-v2" in cmd
    assert "ducklake-maintenance-canary-ephemeral" in cmd


def test_wait_function_active_loud_fails_on_waiter_error(monkeypatch):
    """A non-zero waiter exit raises SmokeTestFailure (Decision 55 -- never race the first invoke)."""
    monkeypatch.setattr(
        smoke.subprocess,
        "run",
        lambda cmd, **kw: types.SimpleNamespace(returncode=255, stdout="", stderr="Waiter FunctionActiveV2 failed"),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="wait function-active-v2"):
        smoke._wait_function_active("fn", profile=None, region="eu-west-2")


def test_core_sigv4_invoke_interception_canary_family(monkeypatch):
    """Module-object interception: patching core._sigv4_invoke intercepts canary_rehearsal's
    finally-block cleanup call (the one core._sigv4_invoke site in the canary family)."""
    _stub_canary_rehearsal(monkeypatch)
    calls = []

    def _raise_sigv4(url, payload, **kw):
        calls.append(payload)
        raise RuntimeError("connection refused")

    monkeypatch.setattr(core, "_sigv4_invoke", _raise_sigv4)
    smoke.canary_rehearsal(json_output=True)
    assert calls, "core._sigv4_invoke was never called -- interception did not occur"


def test_explicit_project_id_skips_resolve_project_id(monkeypatch, capsys):
    """An explicit project id never calls the org-level project listing (a project-scoped key cannot)."""
    _stub_canary_rehearsal(monkeypatch)
    seen: list[str] = []

    def _no_listing(api_key, name="ducklake-catalog"):
        raise AssertionError("resolve_project_id must not be called when a project id is given")

    monkeypatch.setattr(smoke.neon_api, "resolve_project_id", _no_listing)
    monkeypatch.setattr(
        smoke.neon_api,
        "create_branch",
        lambda api_key, project_id: seen.append(project_id) or {"branch_id": _FAKE_BRANCH_ID, "host": _FAKE_BRANCH_HOST},
    )
    smoke.canary_rehearsal(project_id="proj-explicit-1")
    assert seen == ["proj-explicit-1"]


def test_env_project_id_skips_resolve_project_id(monkeypatch):
    _stub_canary_rehearsal(monkeypatch)
    monkeypatch.setenv("NEON_PROJECT_ID", "proj-from-env")
    seen: list[str] = []
    monkeypatch.setattr(
        smoke.neon_api,
        "resolve_project_id",
        lambda api_key, name="ducklake-catalog": (_ for _ in ()).throw(AssertionError("listing called")),
    )
    monkeypatch.setattr(
        smoke.neon_api,
        "create_branch",
        lambda api_key, project_id: seen.append(project_id) or {"branch_id": _FAKE_BRANCH_ID, "host": _FAKE_BRANCH_HOST},
    )
    smoke.canary_rehearsal()
    assert seen == ["proj-from-env"]


def test_cli_project_id_reaches_canary_rehearsal(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(smoke, "canary_rehearsal", lambda **kw: captured.update(kw))
    assert smoke.main(["--canary-rehearsal", "--project-id", "proj-cli-9"]) == 0
    assert captured["project_id"] == "proj-cli-9"


def test_missing_project_id_and_listing_failure_fails_loud_with_hint(monkeypatch):
    _stub_canary_rehearsal(monkeypatch)

    def _listing_denied(api_key, name="ducklake-catalog"):
        raise smoke.neon_api.NeonApiError("organizations listing returned 403")

    monkeypatch.setattr(smoke.neon_api, "resolve_project_id", _listing_denied)
    with pytest.raises(smoke.SmokeTestFailure, match="--project-id"):
        smoke.canary_rehearsal()
