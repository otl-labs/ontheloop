"""The write-time scrubber is byte-identical to its pre-library self (rec-4481 plan 1)."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

from scripts.checks.misc import sensitive_value_corpus as corpus
from src.sensitive_values import mask
from src.turn_capture.scrub import scrub_text

ROOT = Path(__file__).resolve().parents[2]

# Frozen on main before scrub.py became an adapter: sha256 over scrub_text output and counts for these cases.
IDENTITY_CASE_IDS = (
    "aws_access_key_id_bare",
    "aws_access_key_id_json",
    "aws_secret_access_key_export",
    "aws_secret_access_key_json",
    "aws_session_token_export",
    "aws_session_token_json",
    "anthropic_api_key_bare",
    "anthropic_api_key_env",
    "github_token_classic",
    "github_token_fine_grained",
    "bearer_token_long",
    "bearer_token_digit",
    "url_credential_dsn",
    "url_credential_token_user",
    "password_assignment_bare",
    "password_assignment_quoted",
    "x_amz_param_signature",
    "x_amz_param_credential",
    "unique_id_role",
    "unique_id_user",
    "account_id_arn_quoted",
    "account_id_arn_comma",
    "account_id_access_denied",
    "account_id_bare",
    "assumed_role_session_sdk",
    "assumed_role_session_sso",
    "sso_hub_profile_bare",
    "sso_hub_profile_flag",
    "neg_sha1",
    "neg_sha256",
    "neg_md5",
    "neg_hex_with_digit_run",
    "neg_epoch_millis",
    "neg_uuid_letter_last",
    "neg_iso_timestamp",
    "neg_token_call",
    "neg_public_s3_path",
    "neg_accountless_arn",
    "neg_short_bearer",
    "neg_bearer_word",
    "neg_url_no_credentials",
    "neg_password_prose",
    "neg_plain_email",
)
PRE_LIBRARY_DIGEST = "8c1202059f1da73e0ae6bf9df45c90b2b2f4150cd8db920d66d4c01bea139154"  # pragma: allowlist secret


def _cases() -> dict[str, corpus.Case]:
    return {case.case_id: case for case in corpus.positives() + corpus.negatives()}


def test_write_time_matches_pre_library_digest() -> None:
    cases = _cases()
    digest = hashlib.sha256()
    for case_id in IDENTITY_CASE_IDS:
        result = scrub_text(cases[case_id].text)
        digest.update(json.dumps([case_id, result.text, sorted(result.counts.items())]).encode("utf-8"))
        digest.update(b"\n")
    assert digest.hexdigest() == PRE_LIBRARY_DIGEST


def test_write_time_matches_mask_on_whole_corpus() -> None:
    for case in corpus.all_cases():
        scrubbed = scrub_text(case.text)
        masked = mask(case.text, "write_time")
        assert (scrubbed.text, scrubbed.counts) == (masked.text, masked.counts), case.case_id


def test_scrub_holds_no_patterns() -> None:
    tree = ast.parse((ROOT / "src" / "turn_capture" / "scrub.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert "re" not in [alias.name for alias in node.names]
        if isinstance(node, ast.ImportFrom):
            assert node.module != "re"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "compile"
