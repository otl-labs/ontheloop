#!/usr/bin/env python3
"""POST-APPLY live-simulate runner for docs/contracts/iam-simulate-fixture.yaml (design (d)).

Runs `iam:SimulatePrincipalPolicy` for every (verb, target ARN, expected decision,
required_context_keys) triple in the fixture, diffs the live decision against the expected one,
prints a per-triple table, and exits non-zero on any mismatch.

WHY IT LIVES IN scripts/ci/ AND NOT scripts/verifiers/ (B2):
  - validate_verifier_hermeticity AST-scans scripts/verifiers/*.py and its `classes and all(...)`
    exemption is unreachable for a class-less module, so ANY `import boto3` there (module-level or
    nested) reds the PR.
  - A Verifier class would enrol this in run_all_verifiers(), executing a live-AWS call from CI --
    the exact opposite of the intent.
  - scripts/ci/ is the established home for admin/CI operational runners (reconcile_target.py,
    ducklake_artifacts.py, plan_digest.py, review_verdict.py).

WHY IT IS NOT A validate.py CHECK: it asserts LIVE state. Between merge and the human-gated
bootstrap apply the code legitimately declares verbs the live policy lacks, so wiring this into
--pre would red the very PR that fixes the problem. It runs AFTER the admin apply -- see
docs/contracts/deploy-paths.yaml#admin_out_of_band.procedure -- and again on any hashicorp/aws
provider bump.

Every external dependency (the STS identity lookup and the IAM client) is injected through a
factory parameter, so the whole module is importable and unit-testable with no credentials and no
network: boto3 is imported lazily inside the two default factories only.

Usage:
    bin/venv-python -m scripts.ci.iam_simulate --profile agent_platform_admin \\
        --fixture docs/contracts/iam-simulate-fixture.yaml
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional, TextIO

DEFAULT_FIXTURE = "docs/contracts/iam-simulate-fixture.yaml"
DEFAULT_REGION = "eu-west-2"
VALID_DECISIONS = ("allowed", "implicitDeny", "explicitDeny")

_UNRESOLVED_PLACEHOLDER_RE = re.compile(r"\$\{[^}]*\}")


@dataclass(frozen=True)
class Triple:
    """One fully-resolved fixture row: what to simulate and what the answer must be."""

    id: str
    verb: str
    target_arn: str
    expected_decision: str
    context: dict[str, Any]
    why: str
    by_design: Optional[str] = None


Simulator = Callable[[Triple], str]


# ---------------------------------------------------------------------------
# Fixture loading + placeholder resolution (pure -- no AWS)
# ---------------------------------------------------------------------------


def load_fixture(path: Path) -> dict[str, Any]:
    """Parse the fixture YAML. A malformed or non-mapping fixture is a loud error, never an empty run."""
    import yaml  # noqa: PLC0415 -- deferred so an import of this module stays dependency-light

    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: fixture must be a YAML mapping, got {type(data).__name__}")
    if not data.get("triples"):
        raise ValueError(f"{path}: fixture declares no triples -- refusing to report a vacuous zero-mismatch pass")
    return data


def resolve_placeholders(value: str, account_id: str, region: str) -> str:
    """Substitute ${account_id} / ${region}; a surviving ${...} is a typo, not a literal ARN."""
    resolved = value.replace("${account_id}", account_id).replace("${region}", region)
    leftover = _UNRESOLVED_PLACEHOLDER_RE.search(resolved)
    if leftover:
        raise ValueError(f"unresolved placeholder {leftover.group(0)!r} in {value!r}")
    return resolved


def build_triples(data: dict[str, Any], account_id: str, region: str) -> list[Triple]:
    """Resolve every fixture row into a Triple, failing loud on a malformed or wildcarded row."""
    triples: list[Triple] = []
    seen: set[str] = set()
    for index, row in enumerate(data["triples"]):
        if not isinstance(row, dict):
            raise ValueError(f"triple #{index}: expected a mapping, got {type(row).__name__}")
        missing = [k for k in ("id", "verb", "target_arn_template", "expected_decision", "why") if k not in row]
        if missing:
            raise ValueError(f"triple #{index} ({row.get('id', '<no id>')}): missing required field(s) {missing}")
        triple_id = str(row["id"])
        if triple_id in seen:
            raise ValueError(f"duplicate triple id {triple_id!r}")
        seen.add(triple_id)
        expected = str(row["expected_decision"])
        if expected not in VALID_DECISIONS:
            raise ValueError(f"{triple_id}: expected_decision {expected!r} is not one of {VALID_DECISIONS}")
        target = resolve_placeholders(str(row["target_arn_template"]), account_id, region)
        if "*" in target and target != "*":
            raise ValueError(
                f"{triple_id}: target ARN {target!r} contains a wildcard -- the simulator matches the POLICY's "
                "resource pattern against this literal string, so a wildcard here proves nothing "
                "(only the bare '*' is accepted, for actions IAM scopes to no resource type)"
            )
        raw_context = row.get("required_context_keys") or {}
        if not isinstance(raw_context, dict):
            raise ValueError(f"{triple_id}: required_context_keys must be a mapping, got {type(raw_context).__name__}")
        context = {
            str(name): (
                resolve_placeholders(str(value), account_id, region)
                if isinstance(value, str)
                else value
                if isinstance(value, bool)
                else [resolve_placeholders(str(v), account_id, region) for v in value]
            )
            for name, value in raw_context.items()
        }
        triples.append(
            Triple(
                id=triple_id,
                verb=str(row["verb"]),
                target_arn=target,
                expected_decision=expected,
                context=context,
                why=str(row["why"]),
                by_design=str(row["by_design"]) if row.get("by_design") else None,
            )
        )
    return triples


def principal_arn(data: dict[str, Any], account_id: str, region: str) -> str:
    """The role ARN the simulate runs against (identity policies + attached managed + boundary)."""
    principal = data.get("principal") or {}
    template = principal.get("arn_template")
    if not template:
        raise ValueError("fixture has no principal.arn_template -- cannot resolve the simulated identity")
    return resolve_placeholders(str(template), account_id, region)


def context_entries(triple: Triple) -> list[dict[str, Any]]:
    """Fixture context mapping -> the ContextEntries shape SimulatePrincipalPolicy expects.

    A YAML bool is sent as ContextKeyType "boolean" (a Bool condition key such as
    lambda:InvokedViaFunctionUrl is not satisfied by a string-typed entry); strings and lists keep
    "string" and "stringList".
    """
    entries: list[dict[str, Any]] = []
    for name, value in sorted(triple.context.items()):
        if isinstance(value, bool):
            entries.append({"ContextKeyName": name, "ContextKeyValues": [str(value).lower()], "ContextKeyType": "boolean"})
            continue
        values = [value] if isinstance(value, str) else [str(v) for v in value]
        entries.append(
            {
                "ContextKeyName": name,
                "ContextKeyValues": values,
                "ContextKeyType": "string" if len(values) == 1 else "stringList",
            }
        )
    return entries


# ---------------------------------------------------------------------------
# Verdict reporting (pure -- the AWS call is injected)
# ---------------------------------------------------------------------------


def run_fixture(triples: list[Triple], simulate: Simulator, stream: Optional[TextIO] = None) -> int:
    """Simulate every triple, print the per-triple table, and return the MISMATCH COUNT.

    A raised exception from `simulate` is recorded as a mismatch for that triple rather than
    aborting the run: one unsimulatable verb must not hide the verdicts of the other sixty.
    """
    from scripts.ci.redaction_check import mask_account_ids  # noqa: PLC0415 -- stdlib-only shared mask

    out = stream if stream is not None else sys.stdout
    id_width = max((len(t.id) for t in triples), default=0)
    verb_width = max((len(t.verb) for t in triples), default=0)

    mismatches: list[tuple[Triple, str]] = []
    for triple in triples:
        try:
            actual = simulate(triple)
        except Exception as exc:  # noqa: BLE001 -- any client/permission error is a per-triple result
            actual = f"ERROR({type(exc).__name__}: {exc})"
        matched = actual == triple.expected_decision
        if not matched:
            mismatches.append((triple, actual))
        print(
            mask_account_ids(
                f"  {'OK      ' if matched else 'MISMATCH'}  {triple.id:<{id_width}}  {triple.verb:<{verb_width}}  "
                f"expected={triple.expected_decision:<12} actual={actual}"
            ),
            file=out,
        )

    print(f"\n  {len(triples)} triples simulated, {len(mismatches)} mismatch(es).", file=out)
    for triple, actual in mismatches:
        print(mask_account_ids(f"\n  MISMATCH {triple.id}: {triple.verb} on {triple.target_arn}"), file=out)
        print(mask_account_ids(f"    expected={triple.expected_decision} actual={actual}"), file=out)
        print(mask_account_ids(f"    why: {triple.why}"), file=out)
        if triple.by_design:
            print(
                mask_account_ids(
                    f"    by_design: {triple.by_design}\n"
                    "    A by_design entry that flipped to allowed is a GRANT LEAK -- treat it as a security "
                    "regression, not a fixture error."
                ),
                file=out,
            )
    return len(mismatches)


# ---------------------------------------------------------------------------
# Live AWS seams (the only place boto3 is touched)
# ---------------------------------------------------------------------------


def resolve_identity(profile: Optional[str], region: Optional[str]) -> tuple[str, str]:
    """Return (account_id, region) from `sts get-caller-identity` + the resolved session region."""
    import boto3  # noqa: PLC0415

    session = boto3.Session(profile_name=profile or None, region_name=region or None)
    account_id = session.client("sts").get_caller_identity()["Account"]
    return account_id, (region or session.region_name or DEFAULT_REGION)


def make_simulator(profile: Optional[str], region: str, source_arn: str) -> Simulator:
    """Build the live SimulatePrincipalPolicy callable for `source_arn`."""
    import boto3  # noqa: PLC0415

    client = boto3.Session(profile_name=profile or None, region_name=region).client("iam")

    def _call(triple: Triple) -> str:
        response = client.simulate_principal_policy(
            PolicySourceArn=source_arn,
            ActionNames=[triple.verb],
            ResourceArns=[triple.target_arn],
            ContextEntries=context_entries(triple),
        )
        results = response.get("EvaluationResults") or []
        if not results:
            raise RuntimeError(f"SimulatePrincipalPolicy returned no EvaluationResults for {triple.verb}")
        return str(results[0].get("EvalDecision", ""))

    return _call


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(
    argv: Optional[list[str]] = None,
    identity_fn: Callable[[Optional[str], Optional[str]], tuple[str, str]] = resolve_identity,
    simulator_factory: Callable[[Optional[str], str, str], Simulator] = make_simulator,
) -> int:
    """CLI entrypoint. Exit 0 iff every triple's live decision matches its expected_decision.

    identity_fn / simulator_factory are the injected AWS seams (defaults are the live boto3 ones),
    so the whole flow is exercisable in unit tests without credentials.
    """
    parser = argparse.ArgumentParser(prog="iam_simulate.py", description="Post-apply IAM simulate gate")
    parser.add_argument("--fixture", default=DEFAULT_FIXTURE, help=f"fixture path (default: {DEFAULT_FIXTURE})")
    parser.add_argument("--profile", default=None, help="AWS profile (post-apply this is agent_platform_admin)")
    parser.add_argument("--region", default=None, help=f"AWS region override (default: session region or {DEFAULT_REGION})")
    args = parser.parse_args(argv)

    from scripts.ci.redaction_check import mask_account_ids  # noqa: PLC0415 -- stdlib-only shared mask

    try:
        data = load_fixture(Path(args.fixture))
    except Exception as exc:  # noqa: BLE001
        print(mask_account_ids(f"iam_simulate: cannot load fixture {args.fixture}: {exc}"), file=sys.stderr)
        return 1

    try:
        account_id, region = identity_fn(args.profile, args.region)
    except Exception as exc:  # noqa: BLE001
        print(mask_account_ids(f"iam_simulate: cannot resolve the caller identity: {exc}"), file=sys.stderr)
        return 1

    try:
        source_arn = principal_arn(data, account_id, region)
        triples = build_triples(data, account_id, region)
    except ValueError as exc:
        print(mask_account_ids(f"iam_simulate: malformed fixture: {exc}"), file=sys.stderr)
        return 1

    try:
        simulate = simulator_factory(args.profile, region, source_arn)
    except Exception as exc:  # noqa: BLE001
        print(mask_account_ids(f"iam_simulate: cannot build the IAM simulate client: {exc}"), file=sys.stderr)
        return 1

    print(mask_account_ids(f"=== IAM simulate gate: {len(triples)} triples against the live policy (region {region}) ==="))
    mismatches = run_fixture(triples, simulate)
    if mismatches:
        print(
            "\n  FAIL: the live identity/boundary intersection does not match the recorded contract. "
            "Fix in HCL and re-apply through the same human gate -- never by editing the expectation.",
        )
        return 1
    print("\n  PASS: every triple's live decision matches its expected_decision.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
