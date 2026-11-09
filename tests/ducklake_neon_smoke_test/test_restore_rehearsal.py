"""restore_rehearsal orchestrator (rec-5023): sequencing, teardown, stops, redaction, dry-run, CLI. All seams mocked."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from scripts import ducklake_neon_smoke_test as smoke
from scripts.ducklake_smoke import restore_rehearsal as rr
from scripts.ducklake_smoke import restore_rehearsal_aws as aws
from scripts.ducklake_smoke import restore_rehearsal_evidence as ev

pytestmark = pytest.mark.unit

RUN = "20261109t193000_a1b2c3"
FAKE_HOST = "ep-secret.example.invalid"
FAKE_DSN = "postgresql://restore_rehearsal_x:hunter2fake@ep-secret.example.invalid/db"  # pragma: allowlist secret
FAKE_URL = "https://dr.s3.eu-west-2.amazonaws.com/catalog-dr/x.dump?X-Amz-Signature=sig"
SHA = "zipsha="


def _args(tmp_path: Path, **over: Any) -> Any:
    ns = rr.build_parser().parse_args(
        [
            "run",
            "--profile",
            "admin",
            "--dev-profile",
            "dev",
            "--project-id",
            "proj-1",
            "--run-id",
            RUN,
            "--evidence-out",
            str(tmp_path / "evidence.json"),
        ]
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def _good_bodies() -> dict[str, dict[str, Any]]:
    tbl = {"schema": "main", "table": "a", "read_ok": True, "count": 3, "checksum": "99"}
    return {
        "restore": {
            "ok": True,
            "restore_ms": 5,
            "snapshot_id": 7,
            "absolute_paths": 0,
            "foreign_owned": 0,
            "foreign_acl": 0,
            "foreign_acl_grantees": [],
        },
        "rewrite": {"ok": True, "rewritten": True, "rewrite_ms": 2},
        "verify": {"ok": True, "attach_ok": True, "missing_files": 0, "tables": [tbl]},
        "prod_checksum": {"ok": True, "tables": [{**tbl}]},
        "duckdb_copy": {
            "ok": True,
            "copy_ms": 4,
            "catalog_rows": 10,
            "file_bytes": 99,
            "tables": [{"table": "main.a", "match_restored": True, "match_median": True}],
        },
        "write_probe": {
            "ok": True,
            "write_probe_key": "rehearsal_probe/t/f.parquet",
            "write_probe_in_scratch": True,
            "catalog_format_version": "1.0",
        },
        "teardown_scratch": {"ok": True, "deleted_objects": 4},
    }


class _World:
    """Patches every aws seam; records call order; scripted step bodies."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp, self.calls, self.bodies = tmp_path, [], _good_bodies()
        self.scratch_listing = [[], [], []]  # leftover check, then teardown verification
        self.leftover_fns, self.branches, self.shas = set(), [], (SHA, SHA)
        self.step_hook = None
        self.delete_ok = True
        self.patches: list[Any] = []

    def log(self, name: str, ret: Any = None):
        def _f(*a: Any, **k: Any) -> Any:
            self.calls.append(name)
            return ret(*a, **k) if callable(ret) else ret

        return _f

    def start(self) -> "_World":
        record = {"source_git_sha": "deadbeef", "code_sha256": SHA}
        cfg = {"CodeSha256": SHA, "Role": "role-x", "MemorySize": 1536, "Layers": ["l1"]}
        creates = iter(self.shas)
        scratch_calls = {"n": 0}

        def scratch_objects(dev: Any, run_id: str | None = None) -> list[str]:
            self.calls.append("scratch_objects")
            scratch_calls["n"] += 1
            return self.scratch_listing[min(scratch_calls["n"] - 1, len(self.scratch_listing) - 1)]

        def invoke(client: Any, fn: str, payload: dict[str, Any]) -> dict[str, Any]:
            self.calls.append(f"invoke:{payload['step']}")
            if self.step_hook:
                self.step_hook(fn, payload)
            body = self.bodies[payload["step"]]
            if isinstance(body, Exception):
                raise body
            return body

        mapping = {
            "session": lambda profile, region="eu-west-2": MagicMock(name=f"session-{profile}"),
            "read_deploy_record": self.log("record", record),
            "function_config": self.log("function_config", lambda *a, **k: dict(cfg)),
            "function_exists": self.log("function_exists", lambda admin, name: name in self.leftover_fns),
            "function_url": self.log("function_url", "https://dr-url.example"),
            "leftover_branches": self.log("leftover_branches", lambda *a: list(self.branches)),
            "scratch_objects": scratch_objects,
            "prefix_size": self.log("prefix_size", {"objects": 1, "bytes": 2}),
            "copy_prefix": self.log("copy_prefix", {"objects": 3, "bytes": 30, "ms": 11}),
            "head_exists": self.log("head_exists", False),
            "delete_scratch": self.log("delete_scratch", 2),
            "dump_catalog": self.log("dump", {"ok": True, "s3_key": "k.dump", "bucket": "dr-b", "dump_bytes": 123}),
            "presign_dump": self.log("presign", FAKE_URL),
            "create_target": self.log(
                "create_target",
                lambda api_key, project, run_id, on_branch=None: (
                    on_branch and on_branch("br-1"),
                    {"branch_id": "br-1", "dsn_uri": FAKE_DSN},
                )[1],
            ),
            "create_function": self.log("create_function", lambda *a, **k: next(creates)),
            "invoker": self.log("invoker", MagicMock()),
            "invoke_step": invoke,
            "delete_function_confirmed": self.log("delete_function", lambda *a, **k: self.delete_ok),
        }
        for name, fn in mapping.items():
            p = patch.object(rr.aws, name, fn)
            p.start()
            self.patches.append(p)
        for target, ret in (
            ("fetch_api_key", "api-key-fake"),
            ("wait_operations", None),
            ("delete_branch", None),
        ):
            p = patch.object(rr.neon_api, target, self.log(target, ret))
            p.start()
            self.patches.append(p)
        return self

    def stop(self) -> None:
        for p in self.patches:
            p.stop()
        ev._secrets.clear()


@pytest.fixture
def world(tmp_path: Path):
    w = _World(tmp_path).start()
    yield w
    w.stop()


def _run(world: _World, **over: Any) -> int:
    return rr.run(_args(world.tmp, **over))


def _evidence(world: _World) -> dict[str, Any]:
    return json.loads((world.tmp / "evidence.json").read_text())


# ---------------------------------------------------------------- happy path and ordering


def test_full_run_writes_counts_only_evidence_and_tears_down_everything(world, capsys):
    assert _run(world) == 0
    out = capsys.readouterr().out
    assert "RESTORE_REHEARSAL_OK" in out
    e = _evidence(world)
    assert e["torn_down"] == {"scratch": True, "restore_function": True, "checksum_function": True, "branch": True}
    assert e["dump_bytes"] == 123 and e["copy_objects"] == 3 and e["restore_ms"] == 5 and e["rewritten"] is True
    assert e["ops_tables"] == [
        {
            "table": "main.a",
            "read_ok": True,
            "restored_count": 3,
            "restored_checksum": "99",
            "prod_count": 3,
            "prod_checksum": "99",
        }
    ]
    assert e["write_probe_in_production"] is False and e["maintenance_zip_sha256"] == SHA
    assert set(e) == set(ev.EVIDENCE_KEYS)
    assert ev.forbidden_hits(json.dumps(e)) == []


def test_order_teardown_handles_then_dump_then_target_functions_copy_steps(world):
    _run(world)
    c = world.calls
    first_create = c.index("create_target")
    assert c.index("function_config") < c.index("dump") < first_create < c.index("create_function") < c.index("copy_prefix")
    steps = [x for x in c if x.startswith("invoke:")]
    assert steps == [
        "invoke:restore",
        "invoke:rewrite",
        "invoke:verify",
        "invoke:prod_checksum",
        "invoke:duckdb_copy",
        "invoke:write_probe",
        "invoke:teardown_scratch",
    ]
    assert c.index("copy_prefix") < c.index("invoke:restore")
    restore = c.index("invoke:restore")
    assert c.index("wait_operations") < c.index("presign") < restore
    assert c.index("invoke:teardown_scratch") < c.index("delete_function") < c.index("delete_branch")


def test_each_step_uses_its_function_and_the_event_carries_the_token_and_scratch_paths(world):
    seen: list[tuple[str, dict[str, Any]]] = []
    world.step_hook = lambda fn, payload: seen.append((fn, payload))
    _run(world)
    by_step = {p["step"]: (fn, p) for fn, p in seen}
    assert by_step["prod_checksum"][0] == aws.CHECKSUM_FN
    assert all(by_step[s][0] == aws.RESTORE_FN for s in by_step if s != "prod_checksum")
    token = by_step["restore"][1]["confirm"]
    assert token and all(p["confirm"] == token and p["run_id"] == RUN for _, p in seen)
    assert by_step["rewrite"][1]["data_path"].startswith(f"s3://{aws.BUCKET}/{aws.scratch_prefix(RUN)}")
    assert by_step["restore"][1]["dump_url"] == FAKE_URL


def test_the_dsn_reaches_only_create_function_environment(world):
    captured: list[dict[str, Any]] = []
    orig = rr.aws.create_function

    def _create(*a: Any, **k: Any) -> str:
        captured.append(k)
        return orig(*a, **k)

    with patch.object(rr.aws, "create_function", side_effect=_create):
        _run(world)
    envs = {c["env"].get("DUCKLAKE_RESTORE_TARGET_DSN") for c in captured}
    assert envs == {FAKE_DSN, None}
    assert [c["role"] for c in captured] == ["role-x", "role-x"]
    assert not any(FAKE_DSN in json.dumps(c.get("env", {})) for c in captured if "DUCKLAKE_RESTORE_TARGET_DSN" not in c["env"])


def test_no_file_is_written_during_a_run_except_the_evidence_and_no_secret_in_it(world, tmp_path, capsys):
    before = set(Path("/tmp").glob("*"))
    _run(world)
    text = (world.tmp / "evidence.json").read_text() + capsys.readouterr().out
    for needle in (FAKE_DSN, FAKE_URL, "hunter2fake", FAKE_HOST):
        assert needle not in text
    assert {p.name for p in world.tmp.iterdir()} == {"evidence.json"}
    assert set(Path("/tmp").glob("restore-rehearsal*")) <= before


# ---------------------------------------------------------------- stops that are results


def _fail(world: _World, step: str, **over: Any) -> None:
    world.bodies[step] = {**world.bodies[step], **over}


def test_rewritten_false_stops_writes_evidence_and_tears_down(world, capsys):
    _fail(world, "rewrite", rewritten=False)
    assert _run(world) == 1
    assert "FAILED rewritten" in capsys.readouterr().out
    assert "invoke:verify" not in world.calls and "invoke:teardown_scratch" in world.calls
    e = _evidence(world)
    assert e["rewritten"] is False and all(e["torn_down"].values())


@pytest.mark.parametrize(
    "step,over",
    [
        ("verify", {"missing_files": 2}),
        ("verify", {"attach_ok": False, "missing_files": None, "tables": []}),
        ("verify", {"tables": [{"schema": "main", "table": "a", "read_ok": False, "count": None, "checksum": None}]}),
    ],
)
def test_verify_failures_stop_the_run_with_evidence(world, step, over):
    _fail(world, step, **over)
    assert _run(world) == 1
    assert "invoke:prod_checksum" not in world.calls and (world.tmp / "evidence.json").exists()


def test_a_prod_mismatch_is_a_result_naming_faithful(world, capsys):
    world.bodies["prod_checksum"]["tables"][0]["checksum"] = "other"
    assert _run(world) == 1
    assert "FAILED faithful" in capsys.readouterr().out


def test_restore_non_zero_without_final_is_a_step_error_with_no_evidence(world, capsys):
    world.bodies["restore"] = {"ok": False, "step": "restore", "error_class": "CatalogDrError", "code": 1}
    assert _run(world) == 2
    out = capsys.readouterr().out
    assert "STEP_ERROR step=restore error_class=CatalogDrError code=1" in out
    assert not (world.tmp / "evidence.json").exists() and "delete_branch" in world.calls


def test_restore_final_makes_a_restore_failure_a_result(world, capsys):
    world.bodies["restore"] = {"ok": False, "step": "restore", "error_class": "CatalogDrError", "code": 1}
    assert _run(world, restore_final=True) == 1
    e = _evidence(world)
    assert e["restore_ok"] is False and e["restore_exit_status"] == 1 and "FAILED restored" in capsys.readouterr().out
    assert "invoke:rewrite" not in world.calls


def test_restore_final_does_not_excuse_other_errors(world):
    world.bodies["restore"] = {"ok": False, "step": "restore", "error_class": "OSError", "code": 5}
    assert _run(world, restore_final=True) == 2


def test_a_step_error_prints_only_step_class_and_code(world, capsys):
    world.bodies["verify"] = {
        "ok": False,
        "step": "verify",
        "error_class": "OperationalError",
        "code": "08006",
        "message": FAKE_HOST,
    }
    assert _run(world) == 2
    out = capsys.readouterr().out
    assert FAKE_HOST not in out and "step=verify error_class=OperationalError code=08006" in out
    assert not (world.tmp / "evidence.json").exists()


@pytest.mark.parametrize(
    "bodies_value,limit",
    [
        (aws.LimitHit("restore", "lambda_timeout_900s", 901), "lambda_timeout_900s"),
        ({"ok": False, "step": "restore", "error_class": "OSError", "code": 28}, "enospc_tmp"),
    ],
)
def test_a_lambda_limit_writes_host_limit_evidence_naming_step_and_limit(world, bodies_value, limit, capsys):
    world.bodies["restore"] = bodies_value
    assert _run(world) == 1
    e = _evidence(world)
    assert e["host_limit"]["step"] == "restore" and e["host_limit"]["limit"] == limit
    assert all(e["torn_down"].values())


# ---------------------------------------------------------------- teardown


def test_teardown_deletes_only_what_this_run_created(world):
    world.bodies["restore"] = {"ok": False, "step": "restore", "error_class": "OSError", "code": 5}
    with patch.object(rr.aws, "create_function", side_effect=RuntimeError("boom")):
        _run(world)
    assert "delete_function" in world.calls  # the restore function flag was set before the failing create
    assert world.calls.count("delete_function") == 1  # the checksum function was never started
    assert "delete_branch" in world.calls


def test_a_failure_before_any_creation_creates_and_deletes_nothing(world, capsys):
    world.leftover_fns = {aws.RESTORE_FN}
    assert _run(world) == 2
    assert "dump" not in world.calls and "create_target" not in world.calls
    assert "delete_function" not in world.calls and "delete_branch" not in world.calls
    assert "left over" in capsys.readouterr().out


@pytest.mark.parametrize("what", ["branch", "function", "scratch"])
def test_leftover_handles_stop_before_creating_anything(world, what, capsys):
    if what == "branch":
        world.branches = ["restore_rehearsal_old"]
    elif what == "function":
        world.leftover_fns = {aws.CHECKSUM_FN}
    else:
        world.scratch_listing = [["ducklake-neon-smoke/restore-rehearsal/old/x"]]
    assert _run(world) == 2
    assert "dump" not in world.calls and "create_target" not in world.calls
    assert "left over" in capsys.readouterr().out


def test_missing_project_id_stops_before_anything(world, capsys):
    assert _run(world, project_id=None) == 1
    assert "NEON_PROJECT_ID" in capsys.readouterr().out and world.calls == []


def test_resolve_stops_when_project_id_absent_directly(world):
    sitting = rr._Sitting(_args(world.tmp, project_id=None), RUN)
    with pytest.raises(aws.RehearsalError, match="NEON_PROJECT_ID"):
        sitting.resolve_handles()


def test_a_code_sha_mismatch_stops_before_any_step_and_still_tears_down(world, capsys):
    world.stop()
    world.shas = (SHA, "different=")
    world.start()
    assert _run(world) == 2
    assert "invoke:restore" not in world.calls and "delete_branch" in world.calls and "delete_function" in world.calls
    assert "CodeSha256" in capsys.readouterr().out


def test_scratch_falls_back_to_the_dev_profile_when_the_step_cannot_run(world):
    world.bodies["teardown_scratch"] = RuntimeError("down")
    _run(world)
    assert "delete_scratch" in world.calls


def test_scratch_not_empty_after_teardown_is_torn_down_false_and_the_run_still_reports(world, capsys):
    world.scratch_listing = [[], ["left"], ["left"]]
    assert _run(world) == 1
    assert _evidence(world)["torn_down"]["scratch"] is False
    assert "FAILED torn_down" in capsys.readouterr().out


def test_a_failing_delete_is_recorded_false_without_masking_the_result(world, capsys):
    world.delete_ok = False
    assert _run(world) == 1
    assert _evidence(world)["torn_down"]["restore_function"] is False


def test_a_branch_delete_error_is_recorded_false(world, capsys):
    with patch.object(rr.neon_api, "delete_branch", side_effect=RuntimeError(FAKE_HOST)):
        assert _run(world) == 1
    out = capsys.readouterr().out
    assert _evidence(world)["torn_down"]["branch"] is False and FAKE_HOST not in out


def test_scratch_teardown_with_nothing_started_only_verifies_the_listing(world):
    sitting = rr._Sitting(_args(world.tmp), RUN)
    sitting.handles = {"api_key": "k"}
    assert sitting._teardown_scratch() is True
    sitting.state.scratch_started = True
    assert sitting._teardown_scratch() is True  # no restore function: fallback path
    assert "delete_scratch" in world.calls


# ---------------------------------------------------------------- run-id, project id, dry-run, redaction


def test_new_run_id_is_a_plain_postgres_identifier_fragment():
    import re

    assert re.fullmatch(r"\d{8}t\d{6}_[0-9a-f]{6}", rr.new_run_id())


def test_dry_run_calls_nothing_and_lists_the_mutations_with_run_id_and_project(world, capsys):
    assert _run(world, dry_run=True, run_id=None) == 0
    out = capsys.readouterr().out
    assert world.calls == []
    assert "RUN_ID " in out and "NEON_PROJECT_ID proj-1" in out
    for needle in (
        aws.RESTORE_FN,
        aws.CHECKSUM_FN,
        aws.SMOKE_MAINTENANCE_FN,
        aws.READER_FN,
        "restore_rehearsal_",
        "reserved concurrency 1",
    ):
        assert needle in out
    assert "dev" in out and "admin" in out


def test_dry_run_uses_the_supplied_run_id_and_names(world, capsys):
    _run(world, dry_run=True)
    out = capsys.readouterr().out
    assert f"RUN_ID {RUN}" in out and f"restore_rehearsal_{RUN}" in out
    assert rr.dry_run_lines(_args(world.tmp, project_id=None), RUN)[1] == "NEON_PROJECT_ID ABSENT"


def test_a_run_given_a_run_id_uses_its_names_exactly(world):
    seen = {}
    world.step_hook = lambda fn, payload: seen.setdefault("run_id", payload["run_id"])
    _run(world)
    assert seen["run_id"] == RUN


def test_unexpected_exception_text_is_redacted(world, capsys):
    with patch.object(rr.aws, "copy_prefix", side_effect=RuntimeError(f"connect to {FAKE_DSN} failed")):
        assert _run(world) == 2
    out = capsys.readouterr().out
    assert "hunter2fake" not in out and "postgresql://" not in out and "RUN_ERROR RuntimeError" in out


# ---------------------------------------------------------------- version recovery and verify-evidence


def _vr_s3(*, noncurrent: bool = True, restored: bytes | None = None, head_missing: bool = True) -> MagicMock:
    s3 = MagicMock()
    store: dict[str, bytes] = {}
    s3.put_object.side_effect = lambda **k: store.update(data=k["Body"])
    s3.list_object_versions.return_value = {
        "Versions": [{"IsLatest": False, "VersionId": "v1"}] if noncurrent else [{"IsLatest": True, "VersionId": "v0"}]
    }
    s3.get_object.side_effect = lambda **k: {"Body": io.BytesIO(restored if restored is not None else store["data"])}
    return s3


def _vr_args(tmp_path: Path, **over: Any) -> Any:
    ns = rr.build_parser().parse_args(
        ["version-recovery", "--dev-profile", "dev", "--evidence-out", str(tmp_path / "vr.json")]
    )
    for k, v in over.items():
        setattr(ns, k, v)
    return ns


def test_version_recovery_recovers_a_deleted_scratch_object_with_matching_sha(tmp_path, capsys):
    s3 = _vr_s3()
    dev = MagicMock()
    dev.client.return_value = s3
    with patch.object(rr.aws, "session", return_value=dev), patch.object(rr.aws, "head_exists", return_value=False):
        assert rr.version_recovery(_vr_args(tmp_path)) == 0
    assert "VERSION_RECOVERY_OK" in capsys.readouterr().out
    e = json.loads((tmp_path / "vr.json").read_text())
    assert e["sha256_match"] is True and e["noncurrent_versions"] == 1 and e["deleted_after"] is True
    key = s3.put_object.call_args.kwargs["Key"]
    assert "/versioning/" in key and key.startswith("ducklake-neon-smoke/restore-rehearsal/")


def test_version_recovery_names_failures(tmp_path, capsys):
    s3 = _vr_s3(restored=b"tampered")
    dev = MagicMock()
    dev.client.return_value = s3
    with patch.object(rr.aws, "session", return_value=dev), patch.object(rr.aws, "head_exists", return_value=True):
        assert rr.version_recovery(_vr_args(tmp_path, run_id=RUN)) == 1
    out = capsys.readouterr().out
    assert "FAILED sha256_match" in out and "FAILED deleted_after" in out


def test_version_recovery_without_a_noncurrent_version_fails_cleanly(tmp_path, capsys):
    s3 = _vr_s3(noncurrent=False)
    dev = MagicMock()
    dev.client.return_value = s3
    with patch.object(rr.aws, "session", return_value=dev), patch.object(rr.aws, "head_exists", return_value=False):
        assert rr.version_recovery(_vr_args(tmp_path)) == 1
    assert "FAILED noncurrent_versions" in capsys.readouterr().out
    s3.copy_object.assert_not_called()


def test_version_recovery_refuses_profile(tmp_path, capsys):
    with patch.object(rr.aws, "session", side_effect=AssertionError("session opened")):
        assert rr.version_recovery(_vr_args(tmp_path, profile="agent_platform_admin")) == 1
    assert "--profile is refused" in capsys.readouterr().out


def test_verify_evidence_subcommand(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(
        json.dumps(
            {
                "run_id": "x",
                "measured_at": "t",
                "object_under_versioning_prefix": True,
                "noncurrent_versions": 1,
                "sha256_match": True,
                "deleted_after": True,
            }
        )
    )
    assert rr.main(["verify-evidence", str(good)]) == 0
    assert "VERSION_RECOVERY_OK" in capsys.readouterr().out
    bad = tmp_path / "bad.json"
    bad.write_text("postgresql://u:p@h/d")
    assert rr.main(["verify-evidence", str(bad)]) == 1


def test_write_and_verify_without_a_path_still_verifies(capsys):
    assert rr._write_and_verify({"run_id": "x"}, None) == 1
    assert "FAILED" in capsys.readouterr().out


# ---------------------------------------------------------------- CLI flags


@pytest.mark.parametrize(
    "flags,subcommand",
    [
        (["--restore-rehearsal"], "run"),
        (["--restore-rehearsal-version-recovery"], "version-recovery"),
    ],
)
def test_the_two_cli_flags_dispatch_and_pass_values_through(flags, subcommand):
    argv = [
        *flags,
        "--profile",
        "admin",
        "--dev-profile",
        "dev",
        "--run-id",
        RUN,
        "--project-id",
        "proj-1",
        "--evidence-out",
        "/tmp/e.json",
        "--dry-run",
        "--restore-final",
    ]
    with patch.object(rr, "main", return_value=0) as main:
        assert smoke.main(argv) == 0
    passed = main.call_args.args[0]
    assert passed[0] == subcommand
    for pair in (
        ("--profile", "admin"),
        ("--dev-profile", "dev"),
        ("--run-id", RUN),
        ("--project-id", "proj-1"),
        ("--evidence-out", "/tmp/e.json"),
    ):
        assert passed[passed.index(pair[0]) + 1] == pair[1]
    assert "--dry-run" in passed and "--restore-final" in passed


def test_cli_without_optional_values_passes_none_of_them():
    with patch.object(rr, "main", return_value=0) as main:
        smoke.main(["--restore-rehearsal-version-recovery"])
    passed = main.call_args.args[0]
    assert "--profile" not in passed and "--run-id" not in passed and "--dry-run" not in passed
    assert passed[:3] == ["version-recovery", "--dev-profile", "agent_platform"]


def test_a_probe_without_a_key_leaves_production_unknown_and_fails_one_way_writes(world, capsys):
    world.bodies["write_probe"] = {**world.bodies["write_probe"], "write_probe_key": None}
    assert _run(world) == 1
    assert _evidence(world)["write_probe_in_production"] is None
    assert "FAILED one_way_writes" in capsys.readouterr().out
