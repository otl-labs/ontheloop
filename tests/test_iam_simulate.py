"""Credential-free tests for scripts/ci/iam_simulate.py (design (d) post-apply runner).

Covers fixture parsing, placeholder resolution, required_context_keys assembly, the
expected-vs-actual verdict logic, and the non-zero exit on mismatch -- with the AWS call boundary
stubbed throughout (the runner's two boto3 seams are injected parameters, so no test ever needs
credentials or a network). The real docs/contracts/iam-simulate-fixture.yaml is parsed here too, so
a typo in a target ARN or an unknown expected_decision fails at PR time rather than at 2am under
the admin apply.

Placeholder account id is a non-numeric sentinel on purpose: this repository is PUBLIC and a
12-digit literal is blocked by the pre-commit never-commit hook (Decision 101).
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from scripts.ci.iam_simulate import (
    DEFAULT_FIXTURE,
    Triple,
    build_triples,
    context_entries,
    load_fixture,
    main,
    principal_arn,
    resolve_placeholders,
    run_fixture,
)
from scripts.ci.redaction_check import ACCOUNT_ID_SHAPE

_ACCOUNT = "account-id-placeholder"
_REGION = "eu-west-2"
_REPO_ROOT = Path(__file__).resolve().parent.parent

_MINIMAL_FIXTURE = """
schema_version: 1
principal:
  arn_template: "arn:aws:iam::${account_id}:role/agent-platform-github-ci-apply"
triples:
  - id: allowed-verb
    verb: "iam:ListInstanceProfilesForRole"
    target_arn_template: "arn:aws:iam::${account_id}:role/agent-platform-simulate-probe"
    expected_decision: allowed
    required_context_keys: {}
    why: "rec-2882 companion verb."
  - id: denied-verb
    verb: "s3:DeleteBucket"
    target_arn_template: "arn:aws:s3:::agent-platform-data-lake"
    expected_decision: implicitDeny
    by_design: "The data lake holds tfstate and the convergence record."
    required_context_keys: {}
    why: "Bucket destruction stays admin-tier."
  - id: bounded-verb
    verb: "iam:CreateRole"
    target_arn_template: "arn:aws:iam::${account_id}:role/agent-platform-simulate-probe"
    expected_decision: allowed
    required_context_keys:
      "iam:PermissionsBoundary": "arn:aws:iam::${account_id}:policy/agent-platform-github-ci-apply-boundary"
    why: "In-budget create."
"""


def _write(tmp_path: Path, body: str, name: str = "fixture.yaml") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def _triple(**overrides) -> Triple:
    base = {
        "id": "t",
        "verb": "iam:DeleteRole",
        "target_arn": "arn:aws:iam::acct:role/agent-platform-simulate-probe",
        "expected_decision": "allowed",
        "context": {},
        "why": "because",
    }
    base.update(overrides)
    return Triple(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Fixture parsing + placeholder resolution
# ---------------------------------------------------------------------------


def test_load_fixture_parses_triples(tmp_path: Path) -> None:
    data = load_fixture(_write(tmp_path, _MINIMAL_FIXTURE))
    assert [row["id"] for row in data["triples"]] == ["allowed-verb", "denied-verb", "bounded-verb"]


def test_load_fixture_rejects_non_mapping(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must be a YAML mapping"):
        load_fixture(_write(tmp_path, "- just\n- a\n- list\n"))


def test_load_fixture_rejects_empty_triples(tmp_path: Path) -> None:
    """A fixture with no triples must fail loud, never report a vacuous zero-mismatch pass."""
    with pytest.raises(ValueError, match="no triples"):
        load_fixture(_write(tmp_path, "schema_version: 1\ntriples: []\n"))


def test_resolve_placeholders_substitutes_both() -> None:
    resolved = resolve_placeholders("arn:aws:sns:${region}:${account_id}:agent-platform-alerts", _ACCOUNT, _REGION)
    assert resolved == f"arn:aws:sns:{_REGION}:{_ACCOUNT}:agent-platform-alerts"


def test_resolve_placeholders_rejects_unknown_placeholder() -> None:
    with pytest.raises(ValueError, match="unresolved placeholder"):
        resolve_placeholders("arn:aws:iam::${acct_id}:role/x", _ACCOUNT, _REGION)


def test_build_triples_resolves_context_and_by_design(tmp_path: Path) -> None:
    triples = build_triples(load_fixture(_write(tmp_path, _MINIMAL_FIXTURE)), _ACCOUNT, _REGION)
    by_id = {t.id: t for t in triples}
    assert by_id["allowed-verb"].target_arn == f"arn:aws:iam::{_ACCOUNT}:role/agent-platform-simulate-probe"
    assert by_id["denied-verb"].by_design is not None
    assert by_id["allowed-verb"].by_design is None
    assert by_id["bounded-verb"].context == {
        "iam:PermissionsBoundary": f"arn:aws:iam::{_ACCOUNT}:policy/agent-platform-github-ci-apply-boundary"
    }


def test_build_triples_rejects_missing_field(tmp_path: Path) -> None:
    body = 'triples:\n  - id: x\n    verb: "iam:GetRole"\n    expected_decision: allowed\n    why: "w"\n'
    with pytest.raises(ValueError, match="missing required field"):
        build_triples(load_fixture(_write(tmp_path, body)), _ACCOUNT, _REGION)


def test_build_triples_rejects_unknown_decision(tmp_path: Path) -> None:
    body = (
        'triples:\n  - id: x\n    verb: "iam:GetRole"\n'
        '    target_arn_template: "arn:aws:iam::${account_id}:role/x"\n'
        '    expected_decision: probably\n    why: "w"\n'
    )
    with pytest.raises(ValueError, match="is not one of"):
        build_triples(load_fixture(_write(tmp_path, body)), _ACCOUNT, _REGION)


def test_build_triples_rejects_wildcard_target(tmp_path: Path) -> None:
    """A wildcard target proves nothing: the simulator matches the POLICY pattern against this string."""
    body = (
        'triples:\n  - id: x\n    verb: "iam:DeleteRole"\n'
        '    target_arn_template: "arn:aws:iam::${account_id}:role/agent-platform-*"\n'
        '    expected_decision: allowed\n    why: "w"\n'
    )
    with pytest.raises(ValueError, match="contains a wildcard"):
        build_triples(load_fixture(_write(tmp_path, body)), _ACCOUNT, _REGION)


def test_build_triples_rejects_a_wildcard_inside_an_arn_even_when_it_ends_in_star(tmp_path: Path) -> None:
    body = (
        'triples:\n  - id: x\n    verb: "lambda:CreateEventSourceMapping"\n'
        '    target_arn_template: "arn:aws:lambda:${region}:${account_id}:*"\n'
        '    expected_decision: explicitDeny\n    why: "w"\n'
    )
    with pytest.raises(ValueError, match="only the bare"):
        build_triples(load_fixture(_write(tmp_path, body)), _ACCOUNT, _REGION)


def test_build_triples_accepts_the_bare_star_target_for_resourceless_actions(tmp_path: Path) -> None:
    """Decision 213: CreateEventSourceMapping has no resource type, so the policy's Resource "*" is what is matched."""
    body = (
        'triples:\n  - id: x\n    verb: "lambda:CreateEventSourceMapping"\n    target_arn_template: "*"\n'
        '    expected_decision: explicitDeny\n    why: "w"\n'
    )
    (triple,) = build_triples(load_fixture(_write(tmp_path, body)), _ACCOUNT, _REGION)
    assert triple.target_arn == "*"


def test_build_triples_rejects_duplicate_ids(tmp_path: Path) -> None:
    row = (
        '  - id: dup\n    verb: "iam:GetRole"\n    target_arn_template: "arn:aws:iam::${account_id}:role/x"\n'
        '    expected_decision: allowed\n    why: "w"\n'
    )
    with pytest.raises(ValueError, match="duplicate triple id"):
        build_triples(load_fixture(_write(tmp_path, "triples:\n" + row + row)), _ACCOUNT, _REGION)


def test_principal_arn_resolves_and_fails_loud_when_absent(tmp_path: Path) -> None:
    data = load_fixture(_write(tmp_path, _MINIMAL_FIXTURE))
    assert principal_arn(data, _ACCOUNT, _REGION).endswith(":role/agent-platform-github-ci-apply")
    with pytest.raises(ValueError, match="no principal.arn_template"):
        principal_arn({"triples": [1]}, _ACCOUNT, _REGION)


# ---------------------------------------------------------------------------
# required_context_keys assembly
# ---------------------------------------------------------------------------


def test_context_entries_single_value_is_a_string_key() -> None:
    entries = context_entries(_triple(context={"iam:PassedToService": "lambda.amazonaws.com"}))
    assert entries == [
        {
            "ContextKeyName": "iam:PassedToService",
            "ContextKeyValues": ["lambda.amazonaws.com"],
            "ContextKeyType": "string",
        }
    ]


def test_context_entries_multi_value_is_a_string_list_key() -> None:
    entries = context_entries(_triple(context={"aws:TagKeys": ["Project", "Owner"]}))
    assert entries[0]["ContextKeyType"] == "stringList"
    assert entries[0]["ContextKeyValues"] == ["Project", "Owner"]


def test_context_entries_empty_context_sends_nothing() -> None:
    assert context_entries(_triple(context={})) == []


@pytest.mark.parametrize("flag,rendered", [(True, "true"), (False, "false")])
def test_context_entries_bool_value_is_a_boolean_key(flag: bool, rendered: str) -> None:
    entries = context_entries(_triple(context={"lambda:InvokedViaFunctionUrl": flag}))
    assert entries == [
        {"ContextKeyName": "lambda:InvokedViaFunctionUrl", "ContextKeyValues": [rendered], "ContextKeyType": "boolean"}
    ]


def test_build_triples_keeps_a_bool_context_untouched_through_placeholder_resolution(tmp_path: Path) -> None:
    body = _MINIMAL_FIXTURE + (
        "  - id: bool-ctx\n"
        "    verb: lambda:InvokeFunction\n"
        '    target_arn_template: "arn:aws:lambda:${region}:${account_id}:function:agent-platform-simulate-probe"\n'
        "    expected_decision: explicitDeny\n"
        "    required_context_keys:\n"
        '      "lambda:InvokedViaFunctionUrl": false\n'
        '      "lambda:FunctionArn": "arn:aws:lambda:${region}:${account_id}:function:agent-platform-simulate-probe"\n'
        "    why: w\n"
    )
    triple = next(t for t in build_triples(load_fixture(_write(tmp_path, body)), _ACCOUNT, _REGION) if t.id == "bool-ctx")
    assert triple.context["lambda:InvokedViaFunctionUrl"] is False
    assert _ACCOUNT in triple.context["lambda:FunctionArn"]
    types = {e["ContextKeyName"]: e["ContextKeyType"] for e in context_entries(triple)}
    assert types == {"lambda:FunctionArn": "string", "lambda:InvokedViaFunctionUrl": "boolean"}


# ---------------------------------------------------------------------------
# Verdict logic
# ---------------------------------------------------------------------------


def test_run_fixture_all_match_reports_zero_mismatches() -> None:
    triples = [_triple(id="a", expected_decision="allowed"), _triple(id="b", expected_decision="explicitDeny")]
    stream = io.StringIO()
    assert run_fixture(triples, lambda t: t.expected_decision, stream) == 0
    assert "0 mismatch(es)" in stream.getvalue()
    assert "MISMATCH" not in stream.getvalue()


def test_run_fixture_counts_and_explains_a_mismatch() -> None:
    triples = [
        _triple(id="ok", expected_decision="allowed"),
        _triple(id="leaked", expected_decision="implicitDeny", by_design="denied by design", why="grant leak"),
    ]

    def _simulate(triple: Triple) -> str:
        return "allowed"

    stream = io.StringIO()
    assert run_fixture(triples, _simulate, stream) == 1
    output = stream.getvalue()
    assert "MISMATCH" in output
    assert "leaked" in output
    assert "by_design" in output
    assert "security regression" in output


def test_run_fixture_treats_a_raising_simulator_as_a_mismatch() -> None:
    """One unsimulatable verb must not abort the run or masquerade as a pass."""

    def _boom(triple: Triple) -> str:
        raise RuntimeError("AccessDenied on simulate")

    stream = io.StringIO()
    assert run_fixture([_triple(id="x")], _boom, stream) == 1
    assert "ERROR(RuntimeError: AccessDenied on simulate)" in stream.getvalue()


def test_printed_output_masks_account_ids() -> None:
    account = str(10**11 + 7)
    target = f"arn:aws:iam::{account}:role/probe"
    triples = [_triple(id="m", target_arn=target, expected_decision="implicitDeny"), _triple(id="e", target_arn=target)]

    def _simulate(triple: Triple) -> str:
        if triple.id == "e":
            raise RuntimeError(f"denied: arn:aws:sts::{account}:assumed-role/R/s")
        return "allowed"

    stream = io.StringIO()
    assert run_fixture(triples, _simulate, stream) == 2
    output = stream.getvalue()
    assert "[ACCOUNT_ID]" in output
    assert ":role/probe" in output
    assert not ACCOUNT_ID_SHAPE.search(output)


# ---------------------------------------------------------------------------
# CLI wiring (AWS seams injected)
# ---------------------------------------------------------------------------


def _identity(profile, region):  # noqa: ANN001, ANN202
    return _ACCOUNT, region or _REGION


def test_main_exits_zero_when_every_triple_matches(tmp_path: Path) -> None:
    fixture = _write(tmp_path, _MINIMAL_FIXTURE)
    rc = main(
        ["--fixture", str(fixture), "--profile", "agent_platform_admin"],
        identity_fn=_identity,
        simulator_factory=lambda profile, region, arn: lambda t: t.expected_decision,
    )
    assert rc == 0


def test_main_exits_non_zero_on_any_mismatch(tmp_path: Path) -> None:
    fixture = _write(tmp_path, _MINIMAL_FIXTURE)
    rc = main(
        ["--fixture", str(fixture)],
        identity_fn=_identity,
        simulator_factory=lambda profile, region, arn: lambda t: "allowed",
    )
    assert rc == 1


def test_main_passes_the_resolved_principal_arn_to_the_factory(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def _factory(profile, region, arn):  # noqa: ANN001, ANN202
        seen["arn"] = arn
        seen["region"] = region
        return lambda t: t.expected_decision

    main(["--fixture", str(_write(tmp_path, _MINIMAL_FIXTURE))], identity_fn=_identity, simulator_factory=_factory)
    assert seen["arn"] == f"arn:aws:iam::{_ACCOUNT}:role/agent-platform-github-ci-apply"
    assert seen["region"] == _REGION


def test_main_fails_closed_on_missing_fixture(tmp_path: Path) -> None:
    rc = main(
        ["--fixture", str(tmp_path / "nope.yaml")],
        identity_fn=_identity,
        simulator_factory=lambda profile, region, arn: lambda t: "allowed",
    )
    assert rc == 1


def test_main_fails_closed_when_the_identity_cannot_be_resolved(tmp_path: Path) -> None:
    def _no_creds(profile, region):  # noqa: ANN001, ANN202
        raise RuntimeError("Unable to locate credentials")

    rc = main(
        ["--fixture", str(_write(tmp_path, _MINIMAL_FIXTURE))],
        identity_fn=_no_creds,
        simulator_factory=lambda profile, region, arn: lambda t: "allowed",
    )
    assert rc == 1


def test_main_fails_closed_on_a_malformed_fixture(tmp_path: Path) -> None:
    body = 'triples:\n  - id: x\n    verb: "iam:GetRole"\n    expected_decision: allowed\n    why: "w"\n'
    rc = main(
        ["--fixture", str(_write(tmp_path, body))],
        identity_fn=_identity,
        simulator_factory=lambda profile, region, arn: lambda t: "allowed",
    )
    assert rc == 1


def test_main_masks_account_ids_in_error_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    account = str(10**11 + 7)

    def _leaky_identity(profile, region):  # noqa: ANN001, ANN202
        raise RuntimeError(f"denied for arn:aws:sts::{account}:assumed-role/R/s")

    fixture = _write(tmp_path, _MINIMAL_FIXTURE)
    rc = main(["--fixture", str(fixture)], identity_fn=_leaky_identity, simulator_factory=lambda p, r, a: lambda t: "allowed")
    assert rc == 1
    err = capsys.readouterr().err
    assert "[ACCOUNT_ID]" in err
    assert not ACCOUNT_ID_SHAPE.search(err)

    wildcard = (
        'principal:\n  arn_template: "arn:aws:iam::${account_id}:role/r"\n'
        'triples:\n  - id: w\n    verb: "iam:GetRole"\n'
        '    target_arn_template: "arn:aws:iam::${account_id}:role/*"\n'
        '    expected_decision: allowed\n    why: "w"\n'
    )
    rc = main(
        ["--fixture", str(_write(tmp_path, wildcard, "w.yaml"))],
        identity_fn=lambda profile, region: (account, region or _REGION),
        simulator_factory=lambda p, r, a: lambda t: "allowed",
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "[ACCOUNT_ID]" in err
    assert not ACCOUNT_ID_SHAPE.search(err)


# ---------------------------------------------------------------------------
# The real fixture
# ---------------------------------------------------------------------------


def test_real_fixture_records_both_directions() -> None:
    """The contract is only differential if it anchors allows AND denials."""
    decisions = {t.expected_decision for t in build_triples(load_fixture(_REPO_ROOT / DEFAULT_FIXTURE), _ACCOUNT, _REGION)}
    assert {"allowed", "implicitDeny", "explicitDeny"} <= decisions


def test_real_fixture_by_design_entries_carry_a_justification() -> None:
    triples = build_triples(load_fixture(_REPO_ROOT / DEFAULT_FIXTURE), _ACCOUNT, _REGION)
    marked = [t for t in triples if t.by_design is not None]
    assert marked, "the by-design denial record must not be empty"
    for triple in marked:
        assert triple.by_design and triple.by_design.strip(), triple.id


def test_real_fixture_carries_the_s5_reads_policy_immutability_triple() -> None:
    """S5: the relocated read grants must sit behind an EXPLICIT Deny, not merely an absent Allow."""
    triples = build_triples(load_fixture(_REPO_ROOT / DEFAULT_FIXTURE), _ACCOUNT, _REGION)
    s5 = [
        t
        for t in triples
        if t.verb == "iam:CreatePolicyVersion" and t.target_arn.endswith("policy/agent-platform-github-ci-apply-reads")
    ]
    assert len(s5) == 1, "exactly one iam:CreatePolicyVersion-on-the-reads-policy triple is expected"
    assert s5[0].expected_decision == "explicitDeny"


def test_real_fixture_carries_the_update_secret_negative() -> None:
    """S2: UpdateSecret is enumerated, so a non-enumerated agent-platform-* secret must be denied."""
    triples = build_triples(load_fixture(_REPO_ROOT / DEFAULT_FIXTURE), _ACCOUNT, _REGION)
    negatives = [t for t in triples if t.verb == "secretsmanager:UpdateSecret" and t.expected_decision != "allowed"]
    assert negatives, "the UpdateSecret prefix-leak negative must be present"
    for triple in negatives:
        assert "agent-platform-" in triple.target_arn


def test_real_fixture_context_entries_are_well_formed() -> None:
    """Every required_context_keys mapping renders to a valid ContextEntries payload."""
    triples = build_triples(load_fixture(_REPO_ROOT / DEFAULT_FIXTURE), _ACCOUNT, _REGION)
    with_context = [t for t in triples if t.context]
    assert with_context, "the boundary/passrole condition triples must supply context entries"
    for triple in with_context:
        for entry in context_entries(triple):
            assert entry["ContextKeyName"]
            assert entry["ContextKeyValues"]
            assert entry["ContextKeyType"] in ("string", "stringList", "boolean")


_URL_ONLY_ROWS = {
    "url-only-direct-invoke-denied": ("lambda:InvokeFunction", "explicitDeny"),
    "url-only-direct-invoke-absent-key-denied": ("lambda:InvokeFunction", "explicitDeny"),
    "url-only-add-permission-denied": ("lambda:AddPermission", "explicitDeny"),
    "url-only-put-resource-policy-denied": ("lambda:PutResourcePolicy", "explicitDeny"),
    "url-only-add-permission-scoped": ("lambda:AddPermission", "allowed"),
    "url-only-event-source-mapping-denied": ("lambda:CreateEventSourceMapping", "explicitDeny"),
}


def test_real_fixture_carries_the_url_only_rows() -> None:
    """Decision 213: the boundary Denies are recorded with their live expectations (post-apply simulate gate)."""
    by_id = {t.id: t for t in build_triples(load_fixture(_REPO_ROOT / DEFAULT_FIXTURE), _ACCOUNT, _REGION)}
    for row_id, (verb, decision) in _URL_ONLY_ROWS.items():
        assert row_id in by_id, f"missing fixture row {row_id}"
        assert (by_id[row_id].verb, by_id[row_id].expected_decision) == (verb, decision)
    assert by_id["url-only-direct-invoke-denied"].context["lambda:InvokedViaFunctionUrl"] is False
    assert by_id["url-only-direct-invoke-absent-key-denied"].context == {}
    assert by_id["url-only-add-permission-scoped"].target_arn.endswith("function:agent-platform-simulate-probe")
    assert by_id["url-only-event-source-mapping-denied"].target_arn == "*"
    assert (
        by_id["url-only-event-source-mapping-denied"]
        .context["lambda:FunctionArn"]
        .endswith("function:agent-platform-ducklake-writer")
    )
