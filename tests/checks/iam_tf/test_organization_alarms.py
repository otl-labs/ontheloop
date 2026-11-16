"""rec-5026: the management account's trail and five-alarm detector are declared as specified.

Pins terraform/organization/trail.tf (Decision 202 as read per account): a multi-region, global-events,
log-file-validated trail homed in var.aws_region, five metric filters whose literal patterns carry the required
clauses within CloudWatch Logs' limits, each alarm targeting the one topic, and the sanitised event fixture that
step 5's live logs test-metric-filter run reads. Every predicate returns a problem list, so each red case drives
the real predicate.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

import pytest

from tests.checks.iam_tf._platform_security_hcl import _attr, _local_map, _resource_body, _resources

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TRAIL = _REPO_ROOT / "terraform" / "organization" / "trail.tf"
_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "organization_security_filter_events.json"
_P = "agent-platform-org-security-"
ROLE, SIGNIN, POLICY, ROOT, TAMPER = (
    _P + n for n in ("admin-role-assumed", "signin-anomaly", "policy-change", "root-activity", "detector-tamper")
)
_POLICY_EVENTS = (
    "PutRolePolicy PutUserPolicy AttachRolePolicy AttachUserPolicy CreateAccessKey CreateRole UpdateAssumeRolePolicy "
    "AttachPolicy DetachPolicy UpdatePolicy DisablePolicyType"
).split()


def _patterns(text: str) -> dict[str, str]:
    return _local_map(text, "org_security_filter_patterns")


def trail_problems(text: str) -> list[str]:
    problems = []
    trail = _resource_body(text, "aws_cloudtrail", "org_security") or ""
    for attr in ("is_multi_region_trail", "include_global_service_events", "enable_log_file_validation"):
        if _attr(trail, attr) != "true":
            problems.append(f"the trail does not set {attr} = true")
    if _attr(trail, "name") != f'"{_P}trail"':
        problems.append("the trail name is outside the family")
    if "prevent_destroy = true" not in trail:
        problems.append("the trail lacks prevent_destroy")
    bucket = _resource_body(text, "aws_s3_bucket", "org_security_trail") or ""
    if _attr(bucket, "bucket") != f'"{_P}trail-${{local.org_security_account_id}}-${{var.aws_region}}"':
        problems.append("the trail bucket is not named from data sources and the pinned region")
    if re.search(r"\b[a-z]{2}-[a-z]+-\d\b", text):
        problems.append("a region literal in trail.tf")
    for rtype in (
        "aws_s3_bucket_versioning",
        "aws_s3_bucket_public_access_block",
        "aws_s3_bucket_server_side_encryption_configuration",
        "aws_s3_bucket_ownership_controls",
        "aws_s3_bucket_lifecycle_configuration",
        "aws_s3_bucket_policy",
    ):
        if (rtype, "org_security_trail") not in {(t, n) for _, t, n in _resources(text)}:
            problems.append(f"the trail bucket lacks {rtype}")
    return problems


def pattern_problems(text: str) -> list[str]:
    pats = _patterns(text)
    problems = []
    if set(pats) != {ROLE, SIGNIN, POLICY, ROOT, TAMPER}:
        return [f"the five filters are {sorted(pats)}"]
    for name, pat in pats.items():
        if "${" in pat:
            problems.append(f"{name}: interpolation in a pattern")
        if len(pat) > 1024:
            problems.append(f"{name}: pattern over 1,024 characters")
    policy = pats[POLICY]
    if sorted(re.findall(r'eventName = "(\w+)"', policy)) != sorted(_POLICY_EVENTS):
        problems.append("the policy-change filter does not name exactly the eleven events")
    if any(clause in policy for clause in ("userIdentity", "errorCode", "requestParameters", "readOnly")):
        problems.append("the policy-change filter carries an actor, error, parameter or read clause")
    role = pats[ROLE]
    for needle in (
        "sts.amazonaws.com",
        "AssumeRole",
        "SwitchRole",
        "*:role/OrganizationAdmin",
        "additionalEventData.SwitchTo",
    ):
        if needle not in role:
            problems.append(f"the role-assumed filter lacks {needle}")
    if "errorCode" in role:
        problems.append("the role-assumed filter has an errorCode clause (a denied attempt must alarm)")
    signin = pats[SIGNIN]
    for needle in (
        "ConsoleLogin",
        "Failure",
        "MFAUsed",
        "org-admin-operator",
        "readOnly IS FALSE",
        "signin.amazonaws.com",
        "AssumeRole",
    ):
        if needle not in signin:
            problems.append(f"the sign-in filter lacks {needle}")
    root = pats[ROOT]
    if "Root" not in root or "AwsServiceEvent" not in root:
        problems.append("the root filter lacks Root or the AWS service event exclusion")
    tamper = pats[TAMPER]
    for needle in (
        "cloudtrail.amazonaws.com",
        "agent-platform-org-security-trail-*",
        "CreateLogStream",
        "requestParameters.topicArn",
        "requestParameters.subscriptionArn",
        "PutMetricAlarm",
        "DeleteAlarms",
        "DisableAlarmActions",
    ):
        if needle not in tamper:
            problems.append(f"the tamper filter lacks {needle}")
    return problems


def alarm_problems(text: str) -> list[str]:
    problems = []
    alarms = {
        _attr(_resource_body(text, t, n) or "", "alarm_name"): n
        for _, t, n in _resources(text)
        if t == "aws_cloudwatch_metric_alarm"
    }
    if set(alarms) != {f'"{n}"' for n in _patterns(text)}:
        problems.append("the alarms do not match the five filters one to one")
    for name in alarms.values():
        body = _resource_body(text, "aws_cloudwatch_metric_alarm", name) or ""
        if _attr(body, "alarm_actions") != "[aws_sns_topic.org_security_alerts.arn]":
            problems.append(f"{name}: does not target the topic")
    if "email" not in (_resource_body(text, "aws_sns_topic_subscription", "org_security_alerts_email") or ""):
        problems.append("the topic has no e-mail subscription")
    return problems


def fixture_problems(patterns: dict[str, str] | None = None, raw: str | None = None) -> list[str]:
    raw = _FIXTURE.read_text(encoding="utf-8") if raw is None else raw
    events = json.loads(raw)["events"]
    problems = []
    if re.search(r"(?<!\d)\d{12}(?!\d)", raw):
        problems.append("the fixture carries a 12-digit id")
    for e in events:
        if not (isinstance(e.get("name"), str) and isinstance(e.get("event"), dict) and isinstance(e.get("must_match"), list)):
            problems.append(f"an event lacks name, event or must_match: {e.get('name')}")
            continue
        if (
            e["event"].get("errorCode")
            and e["event"].get("requestParameters", None) is not None
            and "iam." in e["event"]["eventSource"]
        ):
            problems.append(f"{e['name']}: a denied IAM event with requestParameters")
    for name in (ROLE, SIGNIN, POLICY, ROOT, TAMPER):
        if not any(name in e.get("must_match", []) for e in events):
            problems.append(f"no match event for {name}")
        if not any(name not in e.get("must_match", []) for e in events):
            problems.append(f"no near-miss event for {name}")
    if not any(e["event"].get("errorCode") and POLICY in e.get("must_match", []) for e in events):
        problems.append("no denied event in the policy-change must_match")
    if not any(e["event"].get("eventName") == "Unsubscribe" and TAMPER in e.get("must_match", []) for e in events):
        problems.append("no sns Unsubscribe event in the tamper must_match")
    return problems


def test_trail_is_declared_as_specified() -> None:
    assert trail_problems(_TRAIL.read_text(encoding="utf-8")) == []


def test_patterns_name_their_event_sets() -> None:
    assert pattern_problems(_TRAIL.read_text(encoding="utf-8")) == []


def test_alarms_target_the_topic() -> None:
    assert alarm_problems(_TRAIL.read_text(encoding="utf-8")) == []


def test_fixture_covers_every_filter() -> None:
    assert fixture_problems() == []


def _sub(old: str, new: str) -> Callable[[str], str]:
    return lambda t: t.replace(old, new, 1)


_REDS: dict[str, tuple[Callable[[str], list[str]], Callable[[str], str]]] = {
    "trail not multi-region": (
        trail_problems,
        _sub("is_multi_region_trail         = true", "is_multi_region_trail         = false"),
    ),
    "global events off": (
        trail_problems,
        _sub("include_global_service_events = true", "include_global_service_events = false"),
    ),
    "validation off": (trail_problems, _sub("enable_log_file_validation    = true", "enable_log_file_validation    = false")),
    "region literal": (trail_problems, lambda t: t + '\nlocals {\n  r = "us-east-1"\n}\n'),
    "pattern interpolation": (
        pattern_problems,
        _sub('requestParameters.roleArn = \\"*:role', 'requestParameters.roleArn = \\"${var.x}:role'),
    ),
    "policy event dropped": (pattern_problems, _sub(' || ($.eventName = \\"DisablePolicyType\\")', "")),
    "policy actor clause": (
        pattern_problems,
        _sub(
            '{ (($.eventSource = \\"iam.amazonaws.com\\") && (',
            '{ ($.userIdentity.type = \\"IAMUser\\") && (($.eventSource = \\"iam.amazonaws.com\\") && (',
        ),
    ),
    "role errorCode clause": (
        pattern_problems,
        _sub('($.eventName = \\"SwitchRole\\")', '($.eventName = \\"SwitchRole\\") && ($.errorCode NOT EXISTS)'),
    ),
    "tamper sns subscription clause dropped": (
        pattern_problems,
        _sub(' || ($.requestParameters.subscriptionArn = \\"*:agent-platform-org-security-alerts:*\\")', ""),
    ),
    "pattern over the limit": (
        pattern_problems,
        _sub('{ ($.userIdentity.type = \\"Root\\")', "{ " + "(1 = 1) && " * 100 + '($.userIdentity.type = \\"Root\\")'),
    ),
    "alarm without the topic": (
        alarm_problems,
        _sub("alarm_actions       = [aws_sns_topic.org_security_alerts.arn]", "alarm_actions       = []"),
    ),
}


@pytest.mark.parametrize("label", sorted(_REDS))
def test_red_case(label: str) -> None:
    predicate, mutate = _REDS[label]
    base = _TRAIL.read_text(encoding="utf-8")
    mutated = mutate(base)
    assert mutated != base, f"red case {label!r} did not mutate anything"
    assert predicate(mutated) != [], f"red case {label!r} was not detected"


def _fixture_red(mutate: Callable[[dict], None]) -> list[str]:
    doc = json.loads(_FIXTURE.read_text(encoding="utf-8"))
    mutate(doc)
    return fixture_problems(raw=json.dumps(doc))


def test_fixture_red_cases() -> None:
    assert _fixture_red(lambda d: d["events"][0]["event"].update(account="1" * 12)) != []
    assert _fixture_red(lambda d: d["events"][0].pop("must_match")) != []
    assert _fixture_red(lambda d: [e["must_match"].clear() for e in d["events"]]) != []
    assert (
        _fixture_red(
            lambda d: d["events"].__setitem__(
                slice(None), [e for e in d["events"] if e["event"].get("eventName") != "Unsubscribe"]
            )
        )
        != []
    )
    denied = next(e for e in json.loads(_FIXTURE.read_text())["events"] if e["name"] == "iam-PutRolePolicy-denied")
    assert denied["event"]["requestParameters"] is None
