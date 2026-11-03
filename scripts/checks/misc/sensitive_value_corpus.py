"""Runtime-built corpus for the sensitive-value detector ratchet (rec-4481 plan 1, Decision 138).

No credential, account or unique-id shape is ever a literal here: every value is assembled at call time, so the
never-commit hook and detect-secrets never see one. scripts/ cannot import tests/, so the secret-shaped builders are
copies of tests/fixtures/turn_capture_corpus.build_secret's concatenation shapes. Duplicate or malformed case ids raise
at call time, never at import.
"""

from __future__ import annotations

import re
from typing import NamedTuple

CASE_ID_RE = re.compile(r"^[a-z0-9_]+$")

_FILLER = "abcdefghijklmnopqrstuvwxyz0123456789"


class Case(NamedTuple):
    case_id: str
    class_id: str | None
    text: str
    planted_value: str | None


def _secret(kind: str) -> str:
    parts = {
        "aws_access": ("AK" + "IA", "ABCDEFGHIJKLMNOP"),
        "aws_access_asia": ("AS" + "IA", "QRSTUVWXYZ234567"),
        "aws_secret": ("wJalrXUtnFEMI/K7MDENG", "/bPxRfiCYEXAMPLEKEY"),
        "aws_session": ("FwoGZXIvYXdzE" + "JDH", "//////////wEaDExampleSessionTokenValue+/=="),
        "anthropic": ("sk-" + "ant-", "api03-" + _FILLER + "_-" + _FILLER[:8]),
        "github": ("gh" + "p_", _FILLER + _FILLER[:8]),
        "github_pat": ("github" + "_pat_", (_FILLER + "_") * 3),
        "bearer_long": ("abcdef0123456789", "abcdef0123456789xyz"),
        "bearer_digit": ("abcdefghij", "klmnopqrst1"),
        "unique_role": ("AR" + "OA", "ABCDEFGHIJKLMNOPQ"),
        "unique_user": ("AI" + "DA", "RSTUVWXYZ23456789"),
        "unique_user2": ("AN" + "PA", "ZYXWVUTSRQPONMLKJ"),
    }
    head, tail = parts[kind]
    return head + tail


def _acct(variant: int) -> str:
    runs = (("2468", "1357", "0246"), ("9753", "1086", "4219"), ("1122", "3344", "5566"))
    return "".join(runs[variant])


def _case(case_id: str, class_id: str | None, text: str, planted: str | None) -> Case:
    return Case(case_id, class_id, text, planted)


def _check(cases: tuple[Case, ...]) -> tuple[Case, ...]:
    seen: set[str] = set()
    for case in cases:
        if not CASE_ID_RE.match(case.case_id):
            raise ValueError(f"case_id {case.case_id!r} must match {CASE_ID_RE.pattern}")
        if case.case_id in seen:
            raise ValueError(f"duplicate case_id {case.case_id!r}")
        seen.add(case.case_id)
    return cases


def positives() -> tuple[Case, ...]:
    """One Case per (class, shape); planted_value is the secret part that must not survive masking."""
    akid, asia = _secret("aws_access"), _secret("aws_access_asia")
    sec, tok = _secret("aws_secret"), _secret("aws_session")
    ant, ghp, pat = _secret("anthropic"), _secret("github"), _secret("github_pat")
    bear_long, bear_digit = _secret("bearer_long"), _secret("bearer_digit")
    role, user, user2 = _secret("unique_role"), _secret("unique_user"), _secret("unique_user2")
    a0, a1, a2 = _acct(0), _acct(1), _acct(2)
    pw = "hunter2" + "xyz"
    dsn_pw = "s3cr3t" + "pass"
    hub = "acme" + "_information_hub"
    hub2 = "corp" + "_information_hub"
    scheme_pg = "post" + "gres://"
    scheme_https = "https" + "://"
    sig = "0123456789abcdef" * 4
    return _check(
        (
            _case("aws_access_key_id_bare", "AWS_ACCESS_KEY_ID", f"key id {akid} was rotated", akid),
            _case("aws_access_key_id_json", "AWS_ACCESS_KEY_ID", f'{{"AccessKeyId": "{asia}"}}', asia),
            _case("aws_secret_access_key_export", "AWS_SECRET_ACCESS_KEY", f"export AWS_SECRET_ACCESS_KEY={sec}", sec),
            _case("aws_secret_access_key_json", "AWS_SECRET_ACCESS_KEY", f'{{\\"SecretAccessKey\\":\\"{sec}\\"}}', sec),
            _case("aws_session_token_export", "AWS_SESSION_TOKEN", f"export AWS_SESSION_TOKEN={tok}", tok),
            _case("aws_session_token_json", "AWS_SESSION_TOKEN", f'"SessionToken": "{tok}"', tok),
            _case("anthropic_api_key_bare", "ANTHROPIC_API_KEY", f"using {ant} for the call", ant),
            _case("anthropic_api_key_env", "ANTHROPIC_API_KEY", f"ANTHROPIC_API_KEY={ant}\n", ant),
            _case("github_token_classic", "GITHUB_TOKEN", f"remote uses {ghp} here", ghp),
            _case("github_token_fine_grained", "GITHUB_TOKEN", f"token: {pat}", pat),
            _case("bearer_token_long", "BEARER_TOKEN", f"Authorization: Bearer {bear_long}", bear_long),
            _case("bearer_token_digit", "BEARER_TOKEN", f"curl -H 'Authorization: Bearer {bear_digit}'", bear_digit),
            _case("url_credential_dsn", "URL_CREDENTIAL", f"{scheme_pg}app:{dsn_pw}@db.internal:5432/orders", dsn_pw),
            _case(
                "url_credential_token_user", "URL_CREDENTIAL", f'git clone "{scheme_https}{dsn_pw}@host.example/r.git"', dsn_pw
            ),
            _case("password_assignment_bare", "PASSWORD_ASSIGNMENT", f"password={pw}&user=app", pw),
            _case("password_assignment_quoted", "PASSWORD_ASSIGNMENT", f'PASSWORD = "{pw}" dbname=x', pw),
            _case("x_amz_param_signature", "X_AMZ_PARAM", f"?X-Amz-Signature={sig}&X-Amz-Expires=60", sig),
            _case("x_amz_param_credential", "X_AMZ_PARAM", f"x-amz-security-token = '{dsn_pw}'", dsn_pw),
            _case("unique_id_role", "UNIQUE_ID", f'{{"RoleId": "{role}"}}', role),
            _case("unique_id_user", "UNIQUE_ID", f"principal {user} and {user2}", user),
            _case("account_id_arn_quoted", "ACCOUNT_ID", f'"arn:aws:iam::{a0}:role/svc-role"', a0),
            _case("account_id_arn_comma", "ACCOUNT_ID", f"arn:aws:sns:eu-west-2:{a1}:topic-name, next", a1),
            _case(
                "account_id_access_denied",
                "ACCOUNT_ID",
                f"User: arn:aws:iam::{a2}:user/ci is not authorized to perform: s3:Get",
                a2,
            ),
            _case("account_id_bare", "ACCOUNT_ID", f"deployed to account {a1} today", a1),
            _case(
                "assumed_role_session_sdk",
                "ASSUMED_ROLE_SESSION",
                "assumed-role/PlatformDev/botocore-session-1793280352",  # pragma: allowlist secret
                "botocore-session-1793280352",
            ),
            _case(
                "assumed_role_session_sso",
                "ASSUMED_ROLE_SESSION",
                "arn:aws:sts::acct:assumed-role/AWSReservedSSO_Admin_0123abcd/first.last@example.com failed",
                "first.last@example.com",
            ),
            _case("sso_hub_profile_bare", "SSO_HUB_PROFILE", f"profile {hub} expired", hub),
            _case("sso_hub_profile_flag", "SSO_HUB_PROFILE", f"aws sso login --profile {hub2}", hub2),
        )
    )


def negatives() -> tuple[Case, ...]:
    """Texts no read_masked class may touch."""
    hexa = "a1b2c3d4e5"  # pragma: allowlist secret
    return _check(
        (
            _case("neg_sha1", None, "commit " + hexa * 4 + " merged", None),
            _case("neg_sha256", None, "digest " + "0123456789abcdef" * 4, None),
            _case("neg_md5", None, "md5 " + "fedcba9876543210" * 2, None),  # pragma: allowlist secret
            _case("neg_hex_with_digit_run", None, "digest deadbeef" + "1234" + "5678" + "9012" + "cafe end", None),
            _case("neg_epoch_millis", None, "ts 1793280352123 done", None),
            _case("neg_uuid_letter_last", None, "id 12345678-1234-4234-8234-12345678901a", None),
            _case("neg_iso_timestamp", None, "at 2026-10-29T13:23:41Z", None),
            _case("neg_token_call", None, "token = get_token()", None),
            _case("neg_public_s3_path", None, "s3://bucket-name/prefix/key.parquet", None),
            _case("neg_accountless_arn", None, "arn:aws:s3:::example-bucket/key", None),
            _case("neg_short_bearer", None, "Bearer " + "abcdefghijklmnopqrstuvwxyz", None),
            _case("neg_bearer_word", None, "Bearer authentication scheme", None),
            _case("neg_url_no_credentials", None, "see https" + "://example.com/path?q=1", None),
            _case("neg_password_prose", None, "password reset required", None),
            _case("neg_plain_email", None, "contact user@example.com", None),
        )
    )


def known_false_positives() -> tuple[tuple[Case, str], ...]:
    """Texts a read_masked class hits although they hold nothing sensitive: (case, class that hits it)."""
    uuid = "12345678-1234-4234-8234-" + _acct(0)
    cases = _check((_case("kfp_uuid_digit_last_group", None, f"id {uuid}", None),))
    return tuple((case, "ACCOUNT_ID") for case in cases)


def all_cases() -> tuple[Case, ...]:
    return _check(positives() + negatives() + tuple(c for c, _ in known_false_positives()))
