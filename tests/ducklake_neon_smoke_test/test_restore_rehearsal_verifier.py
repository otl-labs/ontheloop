"""verify-evidence criteria, forbidden patterns and redaction (rec-5023)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from scripts.ducklake_smoke import restore_rehearsal_evidence as ev

pytestmark = pytest.mark.unit

ACCOUNT = "1234" * 3
FAKE_PW = "pw-fake-9x"  # pragma: allowlist secret -- fake fixture value


def _good() -> dict[str, Any]:
    e: dict[str, Any] = dict.fromkeys(ev.EVIDENCE_KEYS)
    e.update(
        run_id="r1",
        measured_at="2026-11-09T20:00:00Z",
        dump_bytes=10,
        dump_ms=20,
        copy_objects=3,
        copy_bytes=4,
        copy_ms=5,
        restore_ms=6,
        rewrite_ms=7,
        restore_ok=True,
        rewritten=True,
        attach_ok=True,
        absolute_paths=0,
        foreign_owned=0,
        foreign_acl=0,
        foreign_acl_grantees=[],
        missing_files=0,
        write_probe_in_scratch=True,
        write_probe_in_production=False,
        catalog_format_version="1.0",
        prefix_sizes={"ducklake/": {"objects": 1, "bytes": 2}},
        ops_tables=[
            {
                "table": "main.a",
                "read_ok": True,
                "restored_count": 3,
                "restored_checksum": "42633225326946801578",
                "prod_count": 3,
                "prod_checksum": "42633225326946801578",
            }
        ],
        duckdb_copy_tables=[{"table": "main.a", "match_restored": True, "match_median": True}],
        torn_down={"scratch": True, "branch": True},
    )
    return e


def _verify(e: dict[str, Any]) -> tuple[int, list[str]]:
    return ev.verify_text(json.dumps(e))


def test_complete_evidence_is_accepted_and_a_20_digit_checksum_is_not_an_account_id():
    code, lines = _verify(_good())
    assert code == 0 and lines[0] == "RESTORE_REHEARSAL_OK" and "DUCKDB_COPY_OK" in lines


@pytest.mark.parametrize(
    "mutate,criterion",
    [
        (lambda e: e.update(restore_ok=False), "restored"),
        (lambda e: e.update(foreign_owned=1), "restored"),
        (lambda e: e.update(foreign_acl=2), "restored"),
        (lambda e: e.update(rewritten=False), "rewritten"),
        (lambda e: e.update(absolute_paths=1), "rewritten"),
        (lambda e: e.update(attach_ok=False), "rewritten"),
        (lambda e: e.update(missing_files=2), "complete"),
        (lambda e: e["ops_tables"][0].update(read_ok=False), "complete"),
        (lambda e: e["ops_tables"][0].update(prod_checksum="x"), "faithful"),
        (lambda e: e["ops_tables"][0].update(restored_count=None, prod_count=None), "faithful"),
        (lambda e: e.update(write_probe_in_production=True), "one_way_writes"),
        (lambda e: e.update(write_probe_in_scratch=False), "one_way_writes"),
        (lambda e: e.update(dump_ms=None), "measured"),
        (lambda e: e.update(prefix_sizes=None), "measured"),
        (lambda e: e.update(catalog_format_version=None), "format"),
        (lambda e: e["torn_down"].update(branch=False), "torn_down"),
        (lambda e: e.update(torn_down=None), "torn_down"),
    ],
)
def test_each_failed_criterion_is_named(mutate, criterion):
    e = _good()
    mutate(e)
    code, lines = _verify(e)
    assert code == 1 and f"FAILED {criterion}" in lines


def test_stopped_run_names_every_unreached_criterion():
    e = dict.fromkeys(ev.EVIDENCE_KEYS)
    e.update(rewritten=False, restore_ok=True, foreign_owned=0, foreign_acl=0, torn_down={"branch": True})
    code, lines = _verify(e)
    assert code == 1 and {"FAILED rewritten", "FAILED complete", "FAILED faithful"} <= set(lines)


def test_missing_key_is_named():
    e = _good()
    del e["host_limit"]
    code, lines = _verify(e)
    assert code == 1 and "FAILED missing:host_limit" in lines


def test_duckdb_copy_mismatch_is_named_without_changing_the_exit_code():
    e = _good()
    e["duckdb_copy_tables"][0]["match_median"] = False
    code, lines = _verify(e)
    assert code == 0 and "DUCKDB_COPY_MISMATCH main.a" in lines
    e["duckdb_copy_tables"] = []
    code, lines = _verify(e)
    assert code == 0 and "DUCKDB_COPY_MISMATCH no duckdb_copy_tables rows" in lines


@pytest.mark.parametrize(
    "leak",
    [
        "postgresql://u:p@host/db",
        "ep-cool-123.eu-west-2.aws.neon.tech",
        "abcd1234.lambda-url.eu-west-2.on.aws",
        "https://bkt.s3.eu-west-2.amazonaws.com/k?X-Amz-Signature=abc",
        "X-Amz-Security-Token",
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "arn:aws:iam::" + ACCOUNT + ":role/x",
        ACCOUNT,
        "s3://some-bucket/key",
        "agent-platform-data-lake",
    ],
)
def test_forbidden_patterns_are_refused(leak):
    e = _good()
    e["run_id"] = f"x {leak} y"
    code, lines = _verify(e)
    assert code == 1 and lines and lines[0].startswith("FORBIDDEN_PATTERN")


def test_registered_secret_and_bucket_are_refused_and_redacted():
    ev.register_secret(FAKE_PW)
    ev.register_bucket("my-dr-bucket-name")
    ev.register_bucket("my-dr-bucket-name")
    ev.register_secret(None)
    try:
        for leak in (FAKE_PW, "my-dr-bucket-name"):
            code, lines = ev.verify_text(json.dumps({"run_id": leak}))
            assert code == 1 and lines
            assert leak not in ev.redact(f"a {leak} b")
        assert ev.BUCKET_LITERALS.count("my-dr-bucket-name") == 1
    finally:
        ev._secrets.discard(FAKE_PW)
        ev.BUCKET_LITERALS.remove("my-dr-bucket-name")


def test_redact_strips_every_forbidden_pattern_and_nested_values():
    text = f"postgresql://u:p@h/d https://x.s3.amazonaws.com/k?X-Amz-Signature=z {ACCOUNT} arn:aws:s3:::b s3://b/k"
    out = ev.redact(text)
    assert ev.forbidden_hits(out) == []
    nested = ev.redact_value({"a": ["postgresql://u:p@h/d", 5], "b": {"c": ACCOUNT}, "d": None})
    assert nested == {"a": ["[redacted]", 5], "b": {"c": "[redacted]"}, "d": None}


def test_not_json_and_non_object_are_refused():
    assert ev.verify_text("not json") == (1, ["NOT_JSON"])
    assert ev.verify_text("[1]") == (1, ["NOT_JSON"])


def _vr(**over):
    e = {
        "run_id": "r1",
        "measured_at": "t",
        "object_under_versioning_prefix": True,
        "noncurrent_versions": 1,
        "sha256_match": True,
        "deleted_after": True,
    }
    e.update(over)
    return e


def test_version_recovery_ok_only_when_the_criterion_holds():
    assert ev.verify_text(json.dumps(_vr())) == (0, ["VERSION_RECOVERY_OK"])
    for over, name in (
        ({"noncurrent_versions": 0}, "noncurrent_versions"),
        ({"sha256_match": False}, "sha256_match"),
        ({"deleted_after": False}, "deleted_after"),
        ({"object_under_versioning_prefix": False}, "object_under_versioning_prefix"),
    ):
        code, lines = ev.verify_text(json.dumps(_vr(**over)))
        assert code == 1 and f"FAILED {name}" in lines
    partial = _vr()
    del partial["run_id"]
    code, lines = ev.verify_text(json.dumps(partial))
    assert code == 1 and "FAILED missing:run_id" in lines
