"""rec-4044 acceptance: the platform-security-* IAM-change detector is declared as specified.

Pins terraform/bootstrap/platform_security_{admin_policy,trail,alarms}.tf (Decision 202): a
multi-region, global-events, log-file-validated trail homed in var.aws_region with a protected
CloudTrail-only bucket; exactly five alarms targeting the alerts topic, each filter pattern carrying
its required clauses inside CloudWatch Logs' documented limits; the platform-security-* name family;
no region literal or provider alias; and no CI identity statement able to write the detector. The
heartbeat alarm is now fed by platform-security-heartbeat-canary (its own declaration, roles, cadence
and grant are pinned by test_platform_security_heartbeat_canary.py); this module covers the re-shaped
alarm, the tamper pattern's scheduler-write clause and the extended CI-reach check.
Denied IAM calls carry requestParameters null, so the role-change filter also matches the role
CloudTrail names in errorMessage, and the fixture's denied events must carry that real shape.
Every predicate returns a problem list, so each red case drives the real predicate.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Callable

import pytest

from scripts.checks.iam_tf import _read_coverage as rc
from tests.checks.iam_tf._platform_security_hcl import (
    _attr,
    _block_body,
    _local_map,
    _local_string,
    _mask,
    _resource_body,
    _resources,
    _strip_comments,
)
from tests.checks.iam_tf._platform_security_patterns import (
    ALARMS,
    HEARTBEAT,
    fixture_problems,
    pattern_problems,
    triage_problems,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BOOTSTRAP_DIR = _REPO_ROOT / "terraform" / "bootstrap"
_FIXTURE_EVENTS = _REPO_ROOT / "tests" / "fixtures" / "platform_security_filter_events.yaml"
_DETECTOR_FILES = (
    "platform_security_admin_policy.tf",
    "platform_security_trail.tf",
    "platform_security_alarms.tf",
    "platform_security_heartbeat_canary.tf",
)


def _read_detector_texts() -> dict[str, str]:
    return {name: (_BOOTSTRAP_DIR / name).read_text(encoding="utf-8") for name in _DETECTOR_FILES}


def _alarm_names(alarms_text: str) -> set[str]:
    names: set[str] = set()
    for kind, rtype, rname in _resources(alarms_text):
        if kind == "resource" and rtype == "aws_cloudwatch_metric_alarm":
            raw = _attr(_resource_body(alarms_text, rtype, rname) or "", "alarm_name") or ""
            names.add(raw.strip('"'))
    return names


TOPIC_TEMPLATE = "arn:aws:sns:${var.aws_region}:${var.account_id}:agent-platform-alerts"
_REGION_RE = re.compile(r"\b[a-z]{2}(?:-gov)?-(?:north|south|east|west|central|northeast|southeast|northwest|southwest)-\d\b")
_READ_VERB_PREFIXES = ("Get", "Describe", "List", "Lookup", "Filter", "Test")
_DETECTOR_TARGETS = (
    "arn:aws:logs:REGION:ACCT:log-group:platform-security-cloudtrail",
    "arn:aws:logs:REGION:ACCT:log-group:platform-security-cloudtrail:log-stream:ACCT_CloudTrail_REGION",
    "arn:aws:cloudwatch:REGION:ACCT:alarm:platform-security-iam-event-heartbeat",
    "arn:aws:cloudtrail:REGION:ACCT:trail/platform-security-trail",
    "arn:aws:s3:::platform-security-trail-ACCT-REGION",
    "arn:aws:s3:::platform-security-trail-ACCT-REGION/AWSLogs/ACCT/object",
    "arn:aws:iam::ACCT:role/platform-security-cloudtrail-logs",
    "arn:aws:iam::ACCT:policy/platform-security-detector-admin",
    "arn:aws:states:REGION:ACCT:stateMachine:platform-security-heartbeat-canary",
    "arn:aws:states:REGION:ACCT:execution:platform-security-heartbeat-canary:probe",
    "arn:aws:scheduler:REGION:ACCT:schedule/platform-security-heartbeat/platform-security-heartbeat-canary",
    "arn:aws:scheduler:REGION:ACCT:schedule-group/platform-security-heartbeat",
    "arn:aws:iam::ACCT:role/platform-security-heartbeat-canary",
    "arn:aws:iam::ACCT:role/platform-security-heartbeat-scheduler",
)
_NAME_ATTRS = {
    "aws_iam_policy": "name",
    "aws_iam_role": "name",
    "aws_iam_role_policy": "name",
    "aws_s3_bucket": "bucket",
    "aws_cloudwatch_log_group": "name",
    "aws_cloudtrail": "name",
    "aws_cloudwatch_metric_alarm": "alarm_name",
    "aws_sfn_state_machine": "name",
    "aws_scheduler_schedule": "name",
    "aws_scheduler_schedule_group": "name",
}


def _texts() -> dict[str, str]:
    return _read_detector_texts()


def _is_true(body: str, name: str) -> bool:
    return _attr(body, name) == "true"


def trail_problems(trail_text: str) -> list[str]:
    body = _resource_body(trail_text, "aws_cloudtrail", "platform_security")
    if body is None:
        return ["aws_cloudtrail.platform_security missing"]
    problems = [
        f"trail {a} is not true"
        for a in ("is_multi_region_trail", "include_global_service_events", "enable_log_file_validation", "enable_logging")
        if not _is_true(body, a)
    ]
    selectors = [_block_body(body, m.end() - 1) for m in re.finditer(r"\bevent_selector\s*\{", _mask(body))]
    if not any(
        re.search(r'read_write_type\s*=\s*"All"', s) and re.search(r"include_management_events\s*=\s*true", s)
        for s in selectors
    ):
        problems.append("trail does not log All management events")
    if "aws_cloudwatch_log_group.platform_security_trail.arn" not in (_attr(body, "cloud_watch_logs_group_arn") or ""):
        problems.append("trail does not deliver to the platform-security log group")
    if not re.search(r"prevent_destroy\s*=\s*true", _strip_comments(body)):
        problems.append("trail lacks prevent_destroy")
    return problems


def bucket_problems(trail_text: str) -> list[str]:
    problems: list[str] = []
    bucket = _resource_body(trail_text, "aws_s3_bucket", "platform_security_trail") or ""
    if not re.search(r"prevent_destroy\s*=\s*true", _strip_comments(bucket)):
        problems.append("bucket lacks prevent_destroy")
    if not re.search(
        r'status\s*=\s*"Enabled"', _resource_body(trail_text, "aws_s3_bucket_versioning", "platform_security_trail") or ""
    ):
        problems.append("bucket versioning not Enabled")
    pab = _resource_body(trail_text, "aws_s3_bucket_public_access_block", "platform_security_trail") or ""
    problems += [
        f"public access block {f} not true"
        for f in ("block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets")
        if not _is_true(pab, f)
    ]
    if "sse_algorithm" not in (
        _resource_body(trail_text, "aws_s3_bucket_server_side_encryption_configuration", "platform_security_trail") or ""
    ):
        problems.append("bucket has no default SSE")
    lifecycle = _resource_body(trail_text, "aws_s3_bucket_lifecycle_configuration", "platform_security_trail") or ""
    if not re.search(r"expiration\s*\{\s*days\s*=\s*400\s*\}", lifecycle):
        problems.append("bucket lacks the 400-day expiry")
    policy = _resource_body(trail_text, "aws_s3_bucket_policy", "platform_security_trail") or ""
    stmts = (
        rc._split_top_level_objects(rc._extract_bracket_block(policy, policy.find("Statement = [") + len("Statement = [") - 1))
        if "Statement = [" in policy
        else []
    )
    allows = [s for s in stmts if re.search(r'Effect\s*=\s*"Allow"', s)]
    if not allows:
        problems.append("bucket policy has no Allow statement")
    for stmt in allows:
        if not re.search(r'Principal\s*=\s*\{\s*Service\s*=\s*"cloudtrail\.amazonaws\.com"\s*\}', stmt):
            problems.append("bucket policy Allow principal is not only cloudtrail.amazonaws.com")
        if '"aws:SourceArn"' not in stmt:
            problems.append("bucket policy Allow lacks aws:SourceArn")
    return problems


def alarm_problems(alarms_text: str) -> list[str]:
    problems: list[str] = []
    if _local_string(alarms_text, "platform_security_alerts_topic_arn") != TOPIC_TEMPLATE:
        problems.append("alerts topic local is not the agent-platform-alerts ARN template")
    names = _alarm_names(alarms_text)
    if names != ALARMS:
        problems.append(f"alarm set {sorted(names)} != {sorted(ALARMS)}")
    for kind, rtype, rname in _resources(alarms_text):
        if kind != "resource" or rtype != "aws_cloudwatch_metric_alarm":
            continue
        body = _resource_body(alarms_text, rtype, rname) or ""
        name = (_attr(body, "alarm_name") or "").strip('"')
        get = lambda a: _attr(body, a)  # noqa: E731
        if get("alarm_actions") != "[local.platform_security_alerts_topic_arn]":
            problems.append(f"{name}: alarm_actions is not the alerts topic")
        if f'aws_cloudwatch_log_metric_filter.platform_security["{name}"]' not in (get("metric_name") or ""):
            problems.append(f"{name}: metric_name is not its own filter's metric")
        if name == HEARTBEAT:
            expected = {
                "comparison_operator": '"LessThanThreshold"',
                "treat_missing_data": '"breaching"',
                "evaluation_periods": "4",
                "datapoints_to_alarm": "4",
                "period": "900",
                "statistic": '"Sum"',
                "threshold": "1",
                "ok_actions": "[local.platform_security_alerts_topic_arn]",
            }
        else:
            expected = {
                "comparison_operator": '"GreaterThanOrEqualToThreshold"',
                "treat_missing_data": '"notBreaching"',
                "evaluation_periods": "1",
                "period": "300",
                "threshold": "1",
            }
        problems += [f"{name}: {a} = {get(a)} (expected {v})" for a, v in expected.items() if get(a) != v]
    return problems


def naming_problems(texts: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for fname, text in texts.items():
        for kind, rtype, rname in _resources(text):
            if not rname.startswith("platform_security"):
                problems.append(f"{fname}: {kind}.{rtype}.{rname} address lacks the platform_security prefix")
            attr_name = _NAME_ATTRS.get(rtype)
            raw = _attr(_resource_body(text, rtype, rname) or "", attr_name) if kind == "resource" and attr_name else None
            if kind == "resource" and attr_name and raw is None:
                problems.append(
                    f"{fname}: {rtype}.{rname} has no {attr_name} (an auto-generated name would escape the family)"
                )
            elif raw is not None and not raw.strip('"').startswith("platform-security-"):
                problems.append(f"{fname}: {rtype}.{rname} {attr_name} {raw} lacks the platform-security- prefix")
    patterns = _local_map(texts.get("platform_security_alarms.tf", ""), "platform_security_filter_patterns")
    problems += [f"filter {k} lacks the platform-security- prefix" for k in patterns if not k.startswith("platform-security-")]
    return problems


def region_problems(texts: dict[str, str]) -> list[str]:
    problems: list[str] = []
    for fname, text in texts.items():
        problems += [f"{fname}: region literal {m.group(0)}" for m in _REGION_RE.finditer(text)]
        code = _strip_comments(text)
        if re.search(r'\bprovider\s+"', code) or re.search(r"\bprovider\s*=", code) or re.search(r"\balias\s*=", code):
            problems.append(f"{fname}: declares a provider or provider alias")
    return problems


def _policy_doc_statements(text: str) -> list[dict]:
    """statement { } blocks of every data aws_iam_policy_document in a file."""
    out: list[dict] = []
    for kind, rtype, rname in _resources(text):
        if kind != "data" or rtype != "aws_iam_policy_document":
            continue
        m = re.search(rf'data\s+"aws_iam_policy_document"\s+"{rname}"\s*\{{', text)
        body = _block_body(text, m.end() - 1) if m else ""
        for sm in re.finditer(r"\bstatement\s*\{", _strip_comments(body)):
            stmt = _block_body(body, sm.end() - 1)
            eff = re.search(r'effect\s*=\s*"(\w+)"', stmt)
            acts = re.search(r"\bactions\s*=\s*\[(.*?)\]", stmt, re.S)
            res = re.search(r"\bresources\s*=\s*\[(.*?)\]", stmt, re.S)
            out.append(
                {
                    "sid": rname,
                    "effect": eff.group(1) if eff else "Allow",
                    "actions": re.findall(r'"([^"]+)"', acts.group(1)) if acts else [],
                    "resources_raw": res.group(1) if res else "",
                }
            )
    return out


def ci_statements() -> list[dict]:
    root = rc._read_root_text(_BOOTSTRAP_DIR)
    stmts = rc._parse_bootstrap_statements(root, "github_ci_apply") + rc._parse_managed_policy_statements(
        root, "github_ci_apply_reads"
    )
    for path in sorted((_REPO_ROOT / "terraform" / "personal").glob("oidc*.tf")):
        stmts += _policy_doc_statements(path.read_text(encoding="utf-8"))
    return stmts


def _mutating(action: str) -> bool:
    service, _, verb = action.partition(":")
    if action == "*":
        return True
    if service not in {"logs", "cloudwatch", "cloudtrail", "s3", "iam", "states", "scheduler"}:
        return False
    return not (verb.startswith(_READ_VERB_PREFIXES) or verb in {"StartQuery", "StopQuery"})


def ci_grant_problems(statements: list[dict]) -> list[str]:
    problems: list[str] = []
    for stmt in statements:
        if (stmt.get("effect") or "Allow") != "Allow":
            continue
        writes = [a for a in stmt.get("actions") or [] if _mutating(a)]
        if not writes:
            continue
        for raw in re.findall(r'"([^"]+)"', stmt.get("resources_raw") or ""):
            concrete = raw.replace("${var.account_id}", "ACCT").replace("${var.aws_region}", "REGION")
            concrete = re.sub(r"\$\{[^}]*\}", "\x00", concrete)
            hit = [t for t in _DETECTOR_TARGETS if fnmatch.fnmatchcase(t, concrete)]
            if hit:
                problems.append(f"CI statement {stmt.get('sid')} grants {writes} on {raw} (reaches {hit[0]})")
    return problems


def _sub(text: str, old: str, new: str, count: int = 1) -> str:
    assert old in text, f"mutation anchor {old!r} not found"
    return text.replace(old, new, count)


def _trail() -> str:
    return _texts()["platform_security_trail.tf"]


def _alarms() -> str:
    return _texts()["platform_security_alarms.tf"]


def _patterns() -> dict[str, str]:
    return _local_map(_alarms(), "platform_security_filter_patterns")


_ROLE = "platform-security-platform-role-iam-change"
_ROLE_RX = "%^[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Dd][Ee][Vv]$|^[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Aa][Dd][Mm][Ii][Nn]$%"
_ERR_RX = "%role.[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Dd][Ee][Vv]\\s|role.[Pp][Ll][Aa][Tt][Ff][Oo][Rr][Mm][Aa][Dd][Mm][Ii][Nn]\\s%"
_IAM = '$.eventSource = "iam.amazonaws.com"'
_LOG_GROUP_ARN = '"arn:aws:logs:${var.aws_region}:${var.account_id}:log-group:platform-security-*"'

# (label, predicate, file key, old, new[, count]) -- one HCL text edit each.
_TEXT_REDS: list[tuple] = [
    ("single-region trail", "trail", "trail", "is_multi_region_trail         = true", "is_multi_region_trail         = false"),
    ("global events off", "trail", "trail", "include_global_service_events = true", "include_global_service_events = false"),
    ("log validation off", "trail", "trail", "enable_log_file_validation    = true", "enable_log_file_validation    = false"),
    ("write-only trail", "trail", "trail", 'read_write_type           = "All"', 'read_write_type           = "WriteOnly"'),
    ("trail missing prevent_destroy", "trail", "trail", "prevent_destroy = true", "prevent_destroy = false", -1),
    ("bucket missing versioning", "bucket", "trail", 'status = "Enabled"\n  }', 'status = "Suspended"\n  }'),
    ("public access flag off", "bucket", "trail", "restrict_public_buckets = true", "restrict_public_buckets = false"),
    ("short expiry", "bucket", "trail", "days = 400", "days = 30"),
    (
        "bucket policy wildcard principal",
        "bucket",
        "trail",
        'Principal = { Service = "cloudtrail.amazonaws.com" }',
        'Principal = "*"',
    ),
    ("missing alarm", "alarm", "alarms", '"platform-security-denied-iam-write"\n', '"platform-security-other"\n'),
    ("alarm targets another topic", "alarm", "alarms", "[local.platform_security_alerts_topic_arn]", "[local.other_topic]"),
    ("heartbeat notBreaching", "alarm", "alarms", 'treat_missing_data  = "breaching"', 'treat_missing_data  = "notBreaching"'),
    ("heartbeat two periods", "alarm", "alarms", "evaluation_periods  = 4", "evaluation_periods  = 2"),
    ("heartbeat hourly period", "alarm", "alarms", "period              = 900", "period              = 3600"),
    ("heartbeat M-of-N", "alarm", "alarms", "datapoints_to_alarm = 4", "datapoints_to_alarm = 2"),
    ("non-prefixed name", "naming", "trail", '"platform-security-cloudtrail"\n', '"agent-platform-cloudtrail"\n'),
    (
        "unnamed inline policy",
        "naming",
        "heartbeat_canary",
        '  name   = "platform-security-heartbeat-canary"\n  role   = aws_iam_role.platform_security_heartbeat_canary.id',
        "  role   = aws_iam_role.platform_security_heartbeat_canary.id",
    ),
]
# (label, filter name, replacement pattern) -- or (label, filter name, old, new) as an in-pattern edit.
_PATTERN_REDS: list[tuple] = [
    ("roleName exact case", _ROLE, _ROLE_RX, '"PlatformDev"'),
    ("roleName regex grouped", _ROLE, "%^[Pp]", "%^(?i)[Pp]"),
    ("policyArn clause dropped", _ROLE, "platform-security-detector-admin", "x"),
    ("errorMessage clause dropped", _ROLE, f" || ($.errorMessage = {_ERR_RX})", ""),
    ("errorMessage exact case", _ROLE, _ERR_RX, '"*role platformadmin *"'),
    ("errorMessage unanchored name", _ROLE, "[Mm][Ii][Nn]\\s%", "[Mm][Ii][Nn]%"),
    ("denied clause dropped", "platform-security-denied-iam-write", f"{{ {_IAM} }}"),
    ("three regex spans", HEARTBEAT, f"{{ ({_IAM}) && ($.a = %a%) && ($.b = %b%) && ($.c = %c%) }}"),
    ("interpolated pattern", "platform-security-alerts-topic-change", "sns.amazonaws.com", "${var.x}"),
    ("overlong pattern", "platform-security-detector-tamper", "{ ", "{ " + " " * 300),
    ("heartbeat narrowed", HEARTBEAT, f"{{ ({_IAM}) && ($.readOnly IS FALSE) }}"),
    (
        "scheduler clause dropped",
        "platform-security-detector-tamper",
        ' || (($.eventSource = "scheduler.amazonaws.com") && ($.readOnly IS FALSE))',
        "",
    ),
]
_MGMT = '  event_selector {\n    read_write_type           = "All"\n    include_management_events = true\n  }\n'


_DEPENDS = "  depends_on = [\n    aws_s3_bucket_policy"


def _data_first(mgmt: str) -> str:
    assert _MGMT in _trail() and _DEPENDS in _trail(), "the selector text this reordering relies on moved"
    return _trail().replace(_MGMT, "", 1).replace(_DEPENDS, mgmt + "\n" + _DEPENDS, 1)


_OTHER_REDS: dict[str, Callable[[], list[str]]] = {
    "data selector first, write-only management second": lambda: trail_problems(
        _data_first(_MGMT.replace('"All"', '"WriteOnly"'))
    ),
    "sixth alarm": lambda: alarm_problems(
        _alarms()
        + '\nresource "aws_cloudwatch_metric_alarm" "platform_security_extra" {\n  alarm_name = "platform-security-x"\n}\n'
    ),
    "fixture account id": lambda: fixture_problems(
        _FIXTURE_EVENTS.read_text(encoding="utf-8").replace("ACCOUNT", "1234" * 3, 1), ALARMS
    ),
    "fixture misses a filter": lambda: fixture_problems(
        _FIXTURE_EVENTS.read_text(encoding="utf-8").replace(
            "platform-security-alerts-topic-change", "platform-security-detector-tamper"
        ),
        ALARMS,
    ),
    "fixture denied event carries requestParameters": lambda: fixture_problems(
        _FIXTURE_EVENTS.read_text(encoding="utf-8").replace("requestParameters: null", "requestParameters: {roleName: x}", 1),
        ALARMS,
    ),
    "triage query drops errorMessage": lambda: triage_problems(_alarms().replace("errorCode, errorMessage,", "errorCode,")),
    "non-prefixed address": lambda: naming_problems(
        {**_texts(), "x.tf": 'resource "aws_sns_topic" "d" {\n  name = "platform-security-x"\n}\n'}
    ),
    "region literal": lambda: region_problems({"x.tf": 'locals {\n  r = "eu-west-2"\n}\n'}),
    "provider alias": lambda: region_problems({"x.tf": 'provider "aws" {\n  alias = "use1"\n}\n'}),
    "CI writes the log group": lambda: ci_grant_problems(
        [{"sid": "Bad", "effect": "Allow", "actions": ["logs:PutMetricFilter"], "resources_raw": _LOG_GROUP_ARN}]
    ),
    "CI wildcard write": lambda: ci_grant_problems(
        [{"sid": "Bad", "effect": "Allow", "actions": ["cloudwatch:*"], "resources_raw": '"*"'}]
    ),
    "CI writes the canary": lambda: ci_grant_problems(
        [
            {
                "sid": "Bad",
                "effect": "Allow",
                "actions": ["states:UpdateStateMachine"],
                "resources_raw": '"arn:aws:states:${var.aws_region}:${var.account_id}:stateMachine:platform-security-*"',
            }
        ]
    ),
    "CI disables the schedule": lambda: ci_grant_problems(
        [
            {
                "sid": "Bad",
                "effect": "Allow",
                "actions": ["scheduler:UpdateSchedule"],
                "resources_raw": (
                    '"arn:aws:scheduler:${var.aws_region}:${var.account_id}:schedule/platform-security-*/platform-security-*"'
                ),
            }
        ]
    ),
}
_PREDICATES: dict[str, Callable[[str], list[str]]] = {
    "trail": trail_problems,
    "bucket": bucket_problems,
    "alarm": alarm_problems,
}


def _text_red(label: str, pred: str, key: str, old: str, new: str, count: int = 1) -> list[str]:
    texts = _texts()
    fname = f"platform_security_{key}.tf"
    mutated = _sub(texts[fname], old, new, count)
    return naming_problems({**texts, fname: mutated}) if pred == "naming" else _PREDICATES[pred](mutated)


def _pattern_red(label: str, name: str, old: str, new: str | None = None) -> list[str]:
    patterns = _patterns()
    return pattern_problems({**patterns, name: old if new is None else _sub(patterns[name], old, new)})


class TestPlatformRoleIamChangeDetector:
    def test_trail(self) -> None:
        assert trail_problems(_trail()) == []

    def test_trail_selector_order_is_irrelevant(self) -> None:
        assert trail_problems(_data_first(_MGMT)) == []

    def test_bucket(self) -> None:
        assert bucket_problems(_trail()) == []

    def test_alarms(self) -> None:
        assert alarm_problems(_alarms()) == []

    def test_triage_query(self) -> None:
        assert triage_problems(_alarms()) == []

    def test_filter_patterns(self) -> None:
        assert pattern_problems(_patterns()) == []

    def test_fixture(self) -> None:
        assert fixture_problems(_FIXTURE_EVENTS.read_text(encoding="utf-8"), set(_patterns())) == []

    def test_names(self) -> None:
        assert naming_problems(_texts()) == []

    def test_single_region(self) -> None:
        assert region_problems(_texts()) == []

    def test_out_of_ci_reach(self) -> None:
        statements = ci_statements()
        assert len({s.get("sid") for s in statements}) > 10, "CI statement parse is vacuous"
        assert ci_grant_problems(statements) == []

    @pytest.mark.parametrize("case", _TEXT_REDS, ids=[c[0] for c in _TEXT_REDS])
    def test_hcl_red_case(self, case: tuple) -> None:
        assert _text_red(*case) != [], f"red case {case[0]!r} was not detected"

    @pytest.mark.parametrize("case", _PATTERN_REDS, ids=[c[0] for c in _PATTERN_REDS])
    def test_pattern_red_case(self, case: tuple) -> None:
        assert _pattern_red(*case) != [], f"red case {case[0]!r} was not detected"

    @pytest.mark.parametrize("label", sorted(_OTHER_REDS))
    def test_other_red_case(self, label: str) -> None:
        assert _OTHER_REDS[label]() != [], f"red case {label!r} was not detected"
