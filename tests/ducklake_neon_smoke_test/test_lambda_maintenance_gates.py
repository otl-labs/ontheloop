"""CONCERN: scripts/ducklake_smoke/lambda_maintenance_gates.py (rec-2709 Wave 7).

Split out of the former tests/test_ducklake_neon_smoke_test.py monolith: the four
lambda_maintenance_merge gate tests (ok, empty-smoke-catalog-ok, files-grew-fails, not-ok-fails).
"""

from __future__ import annotations

import pytest

import scripts.ducklake_neon_smoke_test as smoke
from scripts.ducklake_smoke import core
from tests.fixtures.ducklake_smoke_fakes import _Resp


def test_lambda_maintenance_merge_ok(monkeypatch, capsys):
    """VP9: maintenance merge with force_recreate_tables=True; asserts files_after_merge <= files_before."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    invoked = {}

    def fake_invoke(url, payload, **kw):
        invoked["payload"] = payload
        return _Resp(200, {"ok": True, "files_before": 3, "files_after_merge": 2, "elapsed_ms": 45.0})

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    smoke.lambda_maintenance_merge()
    out = capsys.readouterr().out
    assert "MAINTENANCE_MERGE OK files_before=3 files_after_merge=2" in out
    assert invoked["payload"] == {"action": "merge", "force_recreate_tables": True}


def test_lambda_maintenance_merge_empty_smoke_catalog_ok(monkeypatch, capsys):
    """VP9: works on a fresh environment where smoke tables were just created (files_before=0)."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(200, {"ok": True, "files_before": 0, "files_after_merge": 0, "elapsed_ms": 12.0}),
    )
    smoke.lambda_maintenance_merge()
    assert "MAINTENANCE_MERGE OK files_before=0 files_after_merge=0" in capsys.readouterr().out


def test_lambda_maintenance_merge_files_grew_fails(monkeypatch):
    """VP9: loud-fail when files_after_merge > files_before (merge expanded the catalog)."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(200, {"ok": True, "files_before": 2, "files_after_merge": 5}),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="files grew after merge"):
        smoke.lambda_maintenance_merge()


def test_lambda_maintenance_merge_not_ok_fails(monkeypatch):
    """VP9: loud-fail when maintenance returns ok=False."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(200, {"ok": False, "error": "catalog error"}),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="MAINTENANCE_MERGE FAIL"):
        smoke.lambda_maintenance_merge()


_GUARD_STATS_OK = {
    "pre_expiry_would_delete_candidates": 2,
    "post_expiry_would_delete_candidates": 2,
    "g2_snapshots_remaining": 3,
    "g4_would_delete_files": 2,
    "g4_would_delete_bytes": 200,
    "g4_deferred_files": 0,
    "g4_deferred_bytes": 0,
    "g4_bounded": False,
    "g4_drain_cutoff_days": 7,
}


def test_lambda_maintenance_gc_ok(monkeypatch, capsys):
    """T2.18 c9: gc with force_recreate_tables=True (rec-2115 gap-1); asserts the guard-stats shape
    and files_after <= files_before."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    invoked = {}

    def fake_invoke(url, payload, **kw):
        invoked["payload"] = payload
        return _Resp(
            200,
            {
                "ok": True,
                "guard_stats": _GUARD_STATS_OK,
                "files_before": 5,
                "files_after": 3,
                "snapshots_expired": 1,
                "files_cleaned": 2,
                "orphans_deleted": 0,
            },
        )

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    smoke.lambda_maintenance_gc()
    out = capsys.readouterr().out
    assert "MAINTENANCE_GC OK files_before=5 files_after=3" in out
    assert invoked["payload"] == {"action": "gc", "force_recreate_tables": True}


def test_lambda_maintenance_gc_fresh_smoke_catalog_ok(monkeypatch, capsys):
    """rec-2115 gap-1: force_recreate_tables=True means gc no longer 502s on a fresh smoke catalog
    (files_before=0 when the smoke tables were just created) -- this is the fresh-catalog case the
    deployed gate hits on EVERY push, so a files_cleaned=0 body must keep passing."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(
            200,
            {
                "ok": True,
                "guard_stats": {
                    **_GUARD_STATS_OK,
                    "pre_expiry_would_delete_candidates": 0,
                    "post_expiry_would_delete_candidates": 0,
                    "g4_would_delete_files": 0,
                },
                "files_before": 0,
                "files_after": 0,
                "snapshots_expired": 0,
                "files_cleaned": 0,
                "orphans_deleted": 0,
            },
        ),
    )
    smoke.lambda_maintenance_gc()
    assert "MAINTENANCE_GC OK files_before=0 files_after=0" in capsys.readouterr().out


def test_gc_gate_fails_on_pre_fix_breaker_stats_shape(monkeypatch):
    """T2.18 c9: the gate must discriminate a pre-fix build on RESPONSE SHAPE. A body carrying the
    retired breaker_stats key (with no guard_stats at all) is exactly what the shipped
    (pre-fix) Lambda would have returned, and must fail this gate -- five consecutive green CD
    runs passed this shape unconditionally while the underlying breaker was fail-open (rec-3772)."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(
            200,
            {
                "ok": True,
                "breaker_stats": {"breaker_tripped": False},
                "files_before": 5,
                "files_after": 3,
                "snapshots_expired": 1,
                "files_cleaned": 2,
                "orphans_deleted": 0,
            },
        ),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="retired breaker key"):
        smoke.lambda_maintenance_gc()


def test_gc_gate_fails_on_missing_guard_stats(monkeypatch):
    """A post-fix-shaped body missing guard_stats entirely (e.g. a typo'd key) must also fail."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(
            200,
            {
                "ok": True,
                "files_before": 5,
                "files_after": 3,
                "snapshots_expired": 1,
                "files_cleaned": 2,
                "orphans_deleted": 0,
            },
        ),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="missing the guard_stats shape"):
        smoke.lambda_maintenance_gc()


def test_lambda_maintenance_gc_fails_without_post_expiry_guard_stats(monkeypatch):
    """Red-before case: a pre-fix payload -- built by OMITTING the new keys, never by naming the
    retired pre-expiry-count key (this file is not on VP7's four-surface allowlist for it) --
    must fail this gate."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    pre_fix_guard_stats = {
        "g2_snapshots_remaining": 3,
        "g4_would_delete_files": 2,
        "g4_would_delete_bytes": 200,
        "g4_deferred_files": 0,
        "g4_deferred_bytes": 0,
        "g4_bounded": False,
    }
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(
            200,
            {
                "ok": True,
                "guard_stats": pre_fix_guard_stats,
                "files_before": 5,
                "files_after": 3,
                "snapshots_expired": 1,
                "files_cleaned": 2,
                "orphans_deleted": 0,
            },
        ),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="missing keys"):
        smoke.lambda_maintenance_gc()


def test_gc_gate_fails_on_drain_cutoff_below_grace_floor(monkeypatch):
    """A non-null g4_drain_cutoff_days below FILE_CLEANUP_GRACE_DAYS (7) is never legal -- a real
    cutoff is always >= the floor. Null (the wholesale-defer / steady-state value) is accepted."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(
            200,
            {
                "ok": True,
                "guard_stats": {**_GUARD_STATS_OK, "g4_drain_cutoff_days": 3},
                "files_before": 5,
                "files_after": 3,
                "snapshots_expired": 1,
                "files_cleaned": 2,
                "orphans_deleted": 0,
            },
        ),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="grace floor"):
        smoke.lambda_maintenance_gc()


def test_gc_gate_fails_when_storage_grew(monkeypatch):
    """rec-3802: seeding arms the files_before>0 branch, previously dead against an always-empty
    smoke catalog. A grown-storage body (files_after > files_before) must fail this gate --
    merge_adjacent_files can only reduce or hold the live count, so growth is a real defect
    (Decision 181/59: never relax this comparison to pass)."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(
            200,
            {
                "ok": True,
                "guard_stats": _GUARD_STATS_OK,
                "files_before": 1,
                "files_after": 2,
                "snapshots_expired": 0,
                "files_cleaned": 0,
                "orphans_deleted": 0,
            },
        ),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="storage grew"):
        smoke.lambda_maintenance_gc()


def test_lambda_maintenance_gc_not_ok_fails(monkeypatch):
    """VP10: loud-fail when maintenance returns ok=False."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(200, {"ok": False, "error": "catalog error"}),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="MAINTENANCE_GC FAIL"):
        smoke.lambda_maintenance_gc()


# ---------------------------------------------------------------------------
# lambda_maintenance_breaker (T2.18 c9 forced G1 trip gate)
# ---------------------------------------------------------------------------


def test_lambda_maintenance_breaker_ok(monkeypatch, capsys):
    """The forced probe must return 500 + breaker_tripped=True -- the gate's happy path."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(500, {"ok": False, "breaker_tripped": True, "error_type": "breaker"}),
    )
    smoke.lambda_maintenance_breaker()
    assert "MAINTENANCE_BREAKER OK status=500 breaker_tripped=true" in capsys.readouterr().out


def test_breaker_gate_fails_when_probe_does_not_trip(monkeypatch):
    """T2.18 c9: no early-return-OK branch. A 200 + breaker_tripped=False response -- exactly what
    the retired file_fraction=0.0 probe returned on an empty smoke catalog -- must now FAIL the
    gate outright (the vacuity this plan closes: this gate previously passed unconditionally on a
    fresh/empty smoke catalog)."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(200, {"ok": True, "breaker_tripped": False}),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="expected 500"):
        smoke.lambda_maintenance_breaker()


def test_lambda_maintenance_breaker_missing_breaker_tripped_fails(monkeypatch):
    """A 500 response that lacks breaker_tripped=True is not proof of the forced trip."""
    monkeypatch.setattr(core, "_function_url", lambda role: f"https://{role}")
    monkeypatch.setattr(
        core,
        "_sigv4_invoke",
        lambda url, payload, **kw: _Resp(500, {"ok": False, "error_type": "runtime"}),
    )
    with pytest.raises(smoke.SmokeTestFailure, match="lacks breaker_tripped=True"):
        smoke.lambda_maintenance_breaker()


_CLEAN_OWNERSHIP = {
    "foreign_owned": 0,
    "schema_owner_is_current_user": True,
    "schema_owner_held": True,
    "schema_owner": "ducklake_smoke_owner",
    "scoped_create_on_schema": False,
    "scoped_create_on_public": False,
}
_CREATE_OK = {
    "ok": True,
    "tables": [f"t{i}" for i in range(9)],
    "created": ["t1", "t2"],
    "added": [],
    "ownership": _CLEAN_OWNERSHIP,
}


def _create_gate(monkeypatch, resp):
    seen = {}
    monkeypatch.setattr(core, "_function_url", lambda role: seen.setdefault("role", role) and "https://fake-url.invalid")

    def fake_invoke(url, payload, **kw):
        seen["payload"] = payload
        return resp

    monkeypatch.setattr(core, "_sigv4_invoke", fake_invoke)
    return seen


def test_create_telemetry_gate_passes_on_a_clean_report(monkeypatch, capsys):
    seen = _create_gate(monkeypatch, _Resp(200, _CREATE_OK))
    smoke.lambda_maintenance_create_telemetry()
    out = capsys.readouterr().out
    assert out.strip() == "MAINTENANCE_CREATE_TELEMETRY OK created=2 added=0 foreign_owned=0 scoped_create=false"
    assert seen == {"role": "maintenance_smoke", "payload": {"action": "create_telemetry_tables"}}
    assert "fake-url" not in out


@pytest.mark.parametrize(
    ("override", "needle"),
    [
        ({"foreign_owned": 3}, "foreign owner"),
        ({"foreign_owned": None}, "foreign owner"),
        ({"schema_owner_held": False}, "owner role is not held"),
        ({"schema_owner_held": None}, "owner role is not held"),
        ({"scoped_create_on_schema": True}, "scoped login holds CREATE"),
        ({"scoped_create_on_public": True}, "scoped login holds CREATE"),
        ({"scoped_create_on_schema": None}, "scoped login holds CREATE"),
    ],
)
def test_create_telemetry_ownership_failures(monkeypatch, override, needle):
    _create_gate(monkeypatch, _Resp(200, {**_CREATE_OK, "ownership": {**_CLEAN_OWNERSHIP, **override}}))
    with pytest.raises(core.SmokeTestFailure, match=needle):
        smoke.lambda_maintenance_create_telemetry()


def test_create_telemetry_foreign_owned_fails(monkeypatch):
    _create_gate(monkeypatch, _Resp(200, {**_CREATE_OK, "ownership": {**_CLEAN_OWNERSHIP, "foreign_owned": 1}}))
    with pytest.raises(core.SmokeTestFailure, match="MAINTENANCE_CREATE_TELEMETRY FAIL: a relation"):
        smoke.lambda_maintenance_create_telemetry()


def test_create_telemetry_schema_owner_mismatch_fails(monkeypatch):
    _create_gate(monkeypatch, _Resp(200, {**_CREATE_OK, "ownership": {**_CLEAN_OWNERSHIP, "schema_owner_held": False}}))
    with pytest.raises(core.SmokeTestFailure, match="owner role is not held by the session"):
        smoke.lambda_maintenance_create_telemetry()


def test_create_telemetry_owner_held_through_membership_passes(monkeypatch, capsys):
    member = {**_CLEAN_OWNERSHIP, "schema_owner_is_current_user": False, "schema_owner_held": True}
    _create_gate(monkeypatch, _Resp(200, {**_CREATE_OK, "ownership": member}))
    smoke.lambda_maintenance_create_telemetry()
    assert capsys.readouterr().out.startswith("MAINTENANCE_CREATE_TELEMETRY OK")


def test_create_telemetry_scoped_create_fails(monkeypatch):
    _create_gate(monkeypatch, _Resp(200, {**_CREATE_OK, "ownership": {**_CLEAN_OWNERSHIP, "scoped_create_on_schema": True}}))
    with pytest.raises(core.SmokeTestFailure, match="Decision 216 cl.1"):
        smoke.lambda_maintenance_create_telemetry()


@pytest.mark.parametrize(
    "resp",
    [
        _Resp(500, {"ok": False, "error_type": "runtime", "error": "sentinel-host.example"}, text="sentinel-host.example"),
        _Resp(200, {**_CREATE_OK, "ok": False}),
        _Resp(502, None, text="sentinel-host.example"),
    ],
)
def test_create_telemetry_not_ok_fails(monkeypatch, resp):
    _create_gate(monkeypatch, resp)
    with pytest.raises(core.SmokeTestFailure, match=r"MAINTENANCE_CREATE_TELEMETRY FAIL: status=\d+ error_type=") as info:
        smoke.lambda_maintenance_create_telemetry()
    assert "sentinel-host" not in str(info.value)


def test_create_telemetry_wrong_table_count_fails(monkeypatch):
    _create_gate(monkeypatch, _Resp(200, {**_CREATE_OK, "tables": ["t"]}))
    with pytest.raises(core.SmokeTestFailure, match="nine telemetry tables"):
        smoke.lambda_maintenance_create_telemetry()


def test_create_telemetry_gate_prints_added_count(monkeypatch, capsys):
    added = ["telemetry_sessions.checkout_sha", "telemetry_observations.usage_final", "telemetry_observations.requested_model"]
    _create_gate(monkeypatch, _Resp(200, {**_CREATE_OK, "added": added}))
    smoke.lambda_maintenance_create_telemetry()
    out = capsys.readouterr().out
    assert out.strip() == "MAINTENANCE_CREATE_TELEMETRY OK created=2 added=3 foreign_owned=0 scoped_create=false"
    assert not any(part in out for name in added for part in name.split("."))


@pytest.mark.parametrize("added", ["absent", None, "telemetry_sessions.checkout_sha", [1]])
def test_create_telemetry_gate_fails_without_an_added_list(monkeypatch, added):
    body = {k: v for k, v in _CREATE_OK.items() if k != "added"}
    if added != "absent":
        body["added"] = added
    _create_gate(monkeypatch, _Resp(200, body))
    with pytest.raises(core.SmokeTestFailure, match="did not list added columns"):
        smoke.lambda_maintenance_create_telemetry()


def test_create_telemetry_missing_created_prints_zero(monkeypatch, capsys):
    _create_gate(monkeypatch, _Resp(200, {k: v for k, v in _CREATE_OK.items() if k != "created"}))
    smoke.lambda_maintenance_create_telemetry()
    assert "created=0" in capsys.readouterr().out
