"""Filter-pattern, fixture and triage predicates for the platform-security-* detector (Decision 202).

Split from tests/checks/iam_tf/test_platform_role_iam_change_alarms.py to keep it under the 500-SLOC
limit (Decision 128). Each predicate returns a problem list, so the test's red cases drive the real
predicate. Denied IAM calls carry requestParameters null in CloudTrail, so the role-change filter
also matches the role named in errorMessage, and the fixture's denied events must carry that shape.
"""

from __future__ import annotations

import re

import yaml

from tests.checks.iam_tf._platform_security_hcl import _local_string

ALARMS = {
    "platform-security-platform-role-iam-change",
    "platform-security-denied-iam-write",
    "platform-security-detector-tamper",
    "platform-security-alerts-topic-change",
    "platform-security-iam-event-heartbeat",
    "platform-security-agent-session-iam-change",
}
AGENT_SESSION = "platform-security-agent-session-iam-change"
HEARTBEAT = "platform-security-iam-event-heartbeat"
_REGEX_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789:_#=@/;,-^$?[]{}|\\*+.")


def _role_regex_problems(pattern: str) -> list[str]:
    m = re.search(r"\$\.requestParameters\.roleName\s*=\s*%([^%]*)%", pattern)
    if m is None:
        return ["role-change filter lacks a regex roleName clause"]
    span = m.group(1)
    alts = span.split("|")
    if len(alts) != 2 or not all(re.fullmatch(r"\^(?:\[[A-Za-z]{2}\])+\$", a) for a in alts):
        return ["roleName regex is not two anchored character-class alternatives"]
    rx = re.compile(span)
    hits = ["PlatformDev", "platformdev", "PLATFORMDEV", "PlatformAdmin", "platformadmin", "pLaTfOrMaDmIn"]
    misses = ["agent-platform-github-ci-branch", "PlatformDevX", "xPlatformAdmin", "PlatformAdm"]
    return [f"roleName regex misses {n}" for n in hits if not rx.fullmatch(n)] + [
        f"roleName regex matches {n}" for n in misses if rx.fullmatch(n)
    ]


def _denied_message(resource: str) -> str:
    return (
        f"User: x is not authorized to perform: iam:TagRole on resource: {resource} because no identity-based policy allows it"
    )


def _denied_role_regex_problems(pattern: str) -> list[str]:
    """A denied call carries requestParameters null, so filter 1 must find the role in errorMessage."""
    m = re.search(r"\$\.errorMessage\s*=\s*%([^%]*)%", pattern)
    if m is None:
        return ["role-change filter lacks a regex errorMessage clause (denied calls carry requestParameters null)"]
    span = m.group(1)
    alts = span.split("|")
    if len(alts) != 2 or not all(re.fullmatch(r"role\.(?:\[[A-Za-z]{2}\])+\\s", a) for a in alts):
        return ["errorMessage regex is not two role.<character classes>\\s alternatives"]
    rx = re.compile(span)
    hits = ["role platformadmin", "role PlatformDev", "arn:aws:iam::ACCOUNT:role/PlatformAdmin", "role pLaTfOrMdEv"]
    misses = ["role platformadminx", "role agent-platform-github-ci-branch", "role xPlatformDev", "role PlatformAdm"]
    return [f"errorMessage regex misses {r}" for r in hits if not rx.search(_denied_message(r))] + [
        f"errorMessage regex matches {r}" for r in misses if rx.search(_denied_message(r))
    ]


_USER_CLASSES = r"(?:[A-Za-z]|\[[A-Za-z]{2}\]|-)+"


def _agent_user_regex_problems(pattern: str) -> list[str]:
    m = re.search(r"\$\.requestParameters\.userName\s*=\s*%([^%]*)%", pattern)
    if m is None:
        return ["agent-session filter lacks a regex userName clause"]
    span = m.group(1)
    if "|" in span or not re.fullmatch(rf"\^{_USER_CLASSES}\$", span):
        return ["userName regex is not one anchored character-class alternative"]
    rx = re.compile(span)
    hits = ["agent-platform-session", "AGENT-PLATFORM-SESSION", "Agent-Platform-Session"]
    misses = ["agent-platform-session-x", "xagent-platform-session", "agent-service-account", "agent-platform-sessio"]
    return [f"userName regex misses {n}" for n in hits if not rx.fullmatch(n)] + [
        f"userName regex matches {n}" for n in misses if rx.fullmatch(n)
    ]


def _agent_denied_regex_problems(pattern: str) -> list[str]:
    """A denied call carries requestParameters null: the user or its policy is named only in errorMessage."""
    m = re.search(r"\$\.errorMessage\s*=\s*%([^%]*)%", pattern)
    if m is None:
        return ["agent-session filter lacks a regex errorMessage clause (denied calls carry requestParameters null)"]
    span = m.group(1)
    alts = span.split("|")
    if (
        len(alts) != 2
        or not re.fullmatch(rf"user\.{_USER_CLASSES}\\s", alts[0])
        or alts[1] != "policy.agent-platform-session-"
    ):
        return ["errorMessage regex is not user.<character classes>\\s | policy.agent-platform-session-"]
    rx = re.compile(span)
    hits = [
        _denied_message("user agent-platform-session"),
        _denied_message("user AGENT-PLATFORM-SESSION"),
        "User: arn:aws:iam::ACCOUNT:user/agent-platform-session is not authorized to perform: iam:TagRole on resource: x",
        _denied_message("policy arn:aws:iam::ACCOUNT:policy/agent-platform-session-guard"),
    ]
    misses = [
        _denied_message("user agent-platform-session-x"),
        _denied_message("user agent-service-account"),
        _denied_message("policy arn:aws:iam::ACCOUNT:policy/agent-platform-github-ci-apply-boundary"),
    ]
    return [f"errorMessage regex misses {r}" for r in hits if not rx.search(r)] + [
        f"errorMessage regex matches {r}" for r in misses if rx.search(r)
    ]


_REQUIRED_CLAUSES = {
    "platform-security-platform-role-iam-change": (
        '$.eventSource = "iam.amazonaws.com"',
        "$.readOnly IS FALSE",
        '"*:policy/agent-platform-github-ci-apply-boundary"',
        '"*:policy/platform-security-detector-admin"',
        '"CreatePolicyVersion"',
        '"SetDefaultPolicyVersion"',
        '"DeletePolicy"',
    ),
    "platform-security-denied-iam-write": (
        '$.eventSource = "iam.amazonaws.com"',
        "$.readOnly IS FALSE",
        '$.errorCode = "AccessDenied*"',
    ),
    "platform-security-detector-tamper": (
        '$.eventSource = "cloudtrail.amazonaws.com"',
        '$.requestParameters.roleName = "platform-security-*"',
        '$.requestParameters.logGroupName = "platform-security-*"',
        '$.eventName != "CreateLogStream"',
        '$.requestParameters.bucketName = "platform-security-trail-*"',
        '$.requestParameters.alarmName = "platform-security-*"',
        '"DeleteAlarms"',
        '"DisableAlarmActions"',
        '$.eventSource = "scheduler.amazonaws.com"',
    ),
    "platform-security-alerts-topic-change": (
        '$.eventSource = "sns.amazonaws.com"',
        '"*:agent-platform-alerts"',
        '"*:agent-platform-alerts:*"',
    ),
    HEARTBEAT: ('$.eventSource = "iam.amazonaws.com"',),
    AGENT_SESSION: (
        '$.eventSource = "iam.amazonaws.com"',
        "$.readOnly IS FALSE",
        '"*:policy/agent-platform-session-*"',
    ),
}


def pattern_problems(patterns: dict[str, str]) -> list[str]:
    problems = [f"filter set {sorted(patterns)} != alarm set"] if set(patterns) != ALARMS else []
    with_regex = 0
    for name, pattern in patterns.items():
        problems += [f"{name}: missing clause {c}" for c in _REQUIRED_CLAUSES.get(name, ()) if c not in pattern]
        if len(pattern) > 1024:
            problems.append(f"{name}: {len(pattern)} characters > 1024")
        if "${" in pattern or "%{" in pattern:
            problems.append(f"{name}: carries a template sequence")
        spans = re.findall(r"%([^%]*)%", pattern)
        with_regex += bool(spans)
        if len(spans) > 2:
            problems.append(f"{name}: {len(spans)} regex spans > 2")
        problems += [f"{name}: regex uses {sorted(set(s) - _REGEX_ALLOWED)}" for s in spans if set(s) - _REGEX_ALLOWED]
    if with_regex > 5:
        problems.append(f"{with_regex} patterns carry a regex (> 5)")
    agent = patterns.get(AGENT_SESSION, "")
    problems += _agent_user_regex_problems(agent)
    problems += _agent_denied_regex_problems(agent)
    role = patterns.get("platform-security-platform-role-iam-change", "")
    problems += _role_regex_problems(role)
    problems += _denied_role_regex_problems(role)
    if "errorCode" in role:
        problems.append("role-change filter conditions on errorCode (denied attempts must match)")
    if set(re.findall(r"\$\.([\w.]+)", patterns.get(HEARTBEAT, ""))) != {"eventSource"}:
        problems.append("heartbeat is not a bare iam eventSource match")
    return problems


_TRIAGE_FIELDS = (
    "eventTime",
    "userIdentity.arn",
    "eventName",
    "requestParameters.roleName",
    "errorCode",
    "errorMessage",
    "eventID",
)


def triage_problems(alarms_text: str) -> list[str]:
    """Every alarm email's Insights query must show the target role -- in errorMessage for a denied call."""
    triage = _local_string(alarms_text, "platform_security_alarm_triage") or ""
    m = re.search(r"fields ([^|]+)\|", triage)
    fields = {f.strip() for f in m.group(1).split(",")} if m else set()
    return [f"triage query lacks field {f}" for f in _TRIAGE_FIELDS if f not in fields]


def fixture_problems(fixture_text: str, filter_names: set[str]) -> list[str]:
    problems = ["fixture carries a twelve-digit number"] if re.search(r"\d{12}", fixture_text) else []
    events = (yaml.safe_load(fixture_text) or {}).get("events") or []
    named = {n for e in events for n in e.get("must_match") or []}
    problems += [f"fixture never names {n}" for n in sorted(filter_names - named)]
    problems += [f"fixture names unknown filter {n}" for n in sorted(named - filter_names)]
    problems += [f"fixture event {e.get('name')} lacks an event body" for e in events if not isinstance(e.get("event"), dict)]
    for e in events:
        body = e.get("event") if isinstance(e.get("event"), dict) else {}
        if str(body.get("errorCode", "")).startswith("AccessDenied") and (
            body.get("requestParameters") is not None or "on resource: " not in str(body.get("errorMessage", ""))
        ):
            problems.append(
                f"fixture event {e.get('name')} is denied but not CloudTrail's denied shape "
                "(requestParameters null, errorMessage naming the resource)"
            )
    return problems


_AGENT_CLASSES = "[Aa][Gg][Ee][Nn][Tt]-[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm]-[Ss][Ee][Ss][Ss][Ii][Oo][Nn]"
_AGENT_USER_RX = f"%^{_AGENT_CLASSES}$%"
_AGENT_ERR_RX = f"%user.{_AGENT_CLASSES}\\s|policy.agent-platform-session-%"
# (label, filter name, old, new): each edits the agent-session filter and must be detected.
AGENT_SESSION_PATTERN_REDS: list[tuple[str, str, str, str]] = [
    ("agent userName exact case", AGENT_SESSION, _AGENT_USER_RX, '"agent-platform-session"'),
    ("agent errorMessage clause dropped", AGENT_SESSION, f" || ($.errorMessage = {_AGENT_ERR_RX})", ""),
    ("agent errorMessage policy alternative dropped", AGENT_SESSION, "|policy.agent-platform-session-%", "%"),
    ("agent policyArn clause dropped", AGENT_SESSION, "*:policy/agent-platform-session-*", "x"),
    ("agent readOnly clause dropped", AGENT_SESSION, " && ($.readOnly IS FALSE)", ""),
]
