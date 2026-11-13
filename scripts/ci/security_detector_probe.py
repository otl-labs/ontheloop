#!/usr/bin/env python3
"""Read-only liveness probe for the platform-security-* IAM-change detector (rec-4044, Decision 202).

A second channel that does not depend on SNS delivery: it reads the six detector alarms and the
alerts topic directly and fails when the detector could be silently blind --

  - an expected alarm is missing, has ActionsEnabled false, or does not target the alerts topic;
  - the IAM-event heartbeat alarm is not OK (the trail's global-event path has gone quiet);
  - any detector alarm's history records a failed action in the last 25 hours (a publish the topic
    refused -- the direct symptom of tampering with the CI-writable alerts topic);
  - the topic carries a KmsMasterKeyId or a policy with any Deny statement;
  - no email subscription on the topic is confirmed.

Public-repo boundary (Decision 101): Actions logs are public, so every rendered line, failure string
and filed rec carries ONLY alarm names, failure classes and AWS error codes -- never an Endpoint, an
ARN or an account id. Every botocore error is rendered as its error code only, and a final redaction
pass scrubs anything ARN- or account-shaped as defence in depth.

--file-on-transition files ONE High rec (source security_detector_stale) only when the previous
completed scheduled run of security-detector-liveness.yml concluded success, or no previous run
exists; if that lookup fails it files anyway (noise over silence). --bootstrap-window reports
failures as warnings, files nothing and exits 0 (the window before the detector's admin apply).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

HEARTBEAT_ALARM = "platform-security-iam-event-heartbeat"
EXPECTED_ALARMS = frozenset(
    {
        "platform-security-platform-role-iam-change",
        "platform-security-denied-iam-write",
        "platform-security-detector-tamper",
        "platform-security-alerts-topic-change",
        "platform-security-agent-session-iam-change",
        HEARTBEAT_ALARM,
    }
)
ALERTS_TOPIC_NAME = "agent-platform-alerts"
WORKFLOW_FILE = "security-detector-liveness.yml"
REC_SOURCE = "security_detector_stale"
HISTORY_WINDOW = timedelta(hours=25)

_ARN_RE = re.compile(r"arn:\S*")
_ACCOUNT_RE = re.compile(r"\d{12}")


def redact(text: str) -> str:
    return _ACCOUNT_RE.sub("<redacted>", _ARN_RE.sub("<redacted>", text))


def error_code(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = (response.get("Error") or {}).get("Code")
        if code:
            return redact(str(code))
    return type(exc).__name__


def alerts_topic_arn(sts: Any, region: str) -> str:
    account = sts.get_caller_identity()["Account"]
    return f"arn:aws:sns:{region}:{account}:{ALERTS_TOPIC_NAME}"


def check_subscription(sns: Any, topic_arn: str) -> list[str]:
    subs: list[dict] = []
    kwargs: dict[str, Any] = {"TopicArn": topic_arn}
    while True:
        page = sns.list_subscriptions_by_topic(**kwargs)
        subs.extend(page.get("Subscriptions") or [])
        token = page.get("NextToken")
        if not token:
            break
        kwargs["NextToken"] = token
    email = [s for s in subs if s.get("Protocol") == "email"]
    if not email:
        return ["subscription: no email subscription on the alerts topic"]
    if all(s.get("SubscriptionArn") == "PendingConfirmation" for s in email):
        return ["subscription: email subscription pending confirmation"]
    return []


def check_topic(sns: Any, topic_arn: str) -> list[str]:
    attrs = sns.get_topic_attributes(TopicArn=topic_arn).get("Attributes") or {}
    failures: list[str] = []
    if attrs.get("KmsMasterKeyId"):
        failures.append("topic: KmsMasterKeyId is set on the alerts topic")
    try:
        statements = json.loads(attrs.get("Policy") or "{}").get("Statement") or []
    except (ValueError, AttributeError):
        return failures + ["topic: alerts topic policy is not parseable"]
    if isinstance(statements, dict):
        statements = [statements]
    if any(isinstance(s, dict) and s.get("Effect") == "Deny" for s in statements):
        failures.append("topic: alerts topic policy carries a Deny statement")
    return failures


def check_alarms(cloudwatch: Any, topic_arn: str, now: datetime) -> list[str]:
    alarms = {
        a["AlarmName"]: a for a in cloudwatch.describe_alarms(AlarmNames=sorted(EXPECTED_ALARMS)).get("MetricAlarms") or []
    }
    failures: list[str] = []
    for name in sorted(EXPECTED_ALARMS):
        alarm = alarms.get(name)
        if alarm is None:
            failures.append(f"{name}: alarm missing")
            continue
        if not alarm.get("ActionsEnabled", False):
            failures.append(f"{name}: alarm actions disabled")
        if topic_arn not in (alarm.get("AlarmActions") or []):
            failures.append(f"{name}: alarm does not target the alerts topic")
        if name == HEARTBEAT_ALARM and alarm.get("StateValue") != "OK":
            state = str(alarm.get("StateValue") or "UNKNOWN")
            failures.append(f"{name}: heartbeat state {redact(state)}")
        history = (
            cloudwatch.describe_alarm_history(
                AlarmName=name, HistoryItemType="Action", StartDate=now - HISTORY_WINDOW, EndDate=now
            ).get("AlarmHistoryItems")
            or []
        )
        if any("fail" in str(item.get("HistorySummary", "")).lower() for item in history):
            failures.append(f"{name}: failed alarm action in the last 25 hours")
    return failures


def _leg(label: str, fn: Callable[[], list[str]]) -> list[str]:
    try:
        return fn()
    except Exception as exc:  # noqa: BLE001 -- any read failure is a failure class, rendered as its code only
        return [f"{label}: aws error {error_code(exc)}"]


def check(
    cloudwatch: Any, sns: Any, sts: Any, region: str, now: Optional[datetime] = None, subscription_only: bool = False
) -> list[str]:
    now = now or datetime.now(timezone.utc)
    try:
        topic_arn = alerts_topic_arn(sts, region)
    except Exception as exc:  # noqa: BLE001
        return [f"identity: aws error {error_code(exc)}"]
    failures = _leg("subscription", lambda: check_subscription(sns, topic_arn))
    if not subscription_only:
        failures += _leg("topic", lambda: check_topic(sns, topic_arn))
        failures += _leg("alarms", lambda: check_alarms(cloudwatch, topic_arn, now))
    return [redact(f) for f in failures]


def previous_run_succeeded(caller: Callable[[str], Any], api_base: str, current_run_id: str) -> Optional[bool]:
    """True/False for the previous completed scheduled run's conclusion; None when none exists.
    Raises on an API failure -- the caller treats that as "file anyway"."""
    data = caller(f"{api_base}/actions/workflows/{WORKFLOW_FILE}/runs?event=schedule&status=completed&per_page=5")
    if not isinstance(data, dict):
        raise RuntimeError("workflow-runs lookup returned no payload")
    for run in data.get("workflow_runs") or []:
        if str(run.get("id")) != str(current_run_id):
            return run.get("conclusion") == "success"
    return None


def should_file(lookup: Callable[[], Optional[bool]]) -> bool:
    try:
        previous = lookup()
    except Exception:  # noqa: BLE001 -- noise over silence
        return True
    return previous is None or previous is True


def build_rec_fields(failures: list[str]) -> dict[str, str]:
    detail = "; ".join(failures)
    return {
        "title": "Security detector stale: platform-security IAM-change detector liveness probe failed",
        "file": "terraform/bootstrap/platform_security_alarms.tf",
        "status": "open",
        "source": REC_SOURCE,
        "priority": "High",
        "effort": "S",
        "risk": "high",
        "verification_tier": "V2",
        "context": redact(
            "The hourly security-detector-liveness probe (scripts/ci/security_detector_probe.py, Decision 202) "
            "went from healthy to failing, so the platform-security IAM-change detector may be blind or unable "
            f"to email. Failure classes: {detail}. Investigate with an ADMIN session; the probe prints only "
            "alarm names, failure classes and AWS error codes."
        ),
        "acceptance": "bin/venv-python -m scripts.ci.security_detector_probe --profile agent_platform_admin",
    }


def _github_caller(token: str) -> Callable[[str], Any]:
    import urllib.request  # noqa: PLC0415

    def _call(url: str) -> Any:
        if not token:
            raise RuntimeError("no GitHub token")
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            return json.loads(resp.read())

    return _call


def _file_rec(fields: dict[str, str]) -> str:
    from scripts.ops_data_portal import file_rec  # noqa: PLC0415

    return file_rec(fields)


def _make_clients(profile: Optional[str]) -> tuple[Any, Any, Any, str]:
    import boto3  # noqa: PLC0415

    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    region = os.environ.get("AWS_DEFAULT_REGION") or session.region_name
    if not region:
        raise SystemExit("security_detector_probe: no region (set AWS_DEFAULT_REGION or the profile's region)")
    return (
        session.client("cloudwatch", region_name=region),
        session.client("sns", region_name=region),
        session.client("sts", region_name=region),
        region,
    )


def main(
    argv: Optional[list[str]] = None,
    clients: Optional[tuple[Any, Any, Any, str]] = None,
    gh_caller: Optional[Callable[[str], Any]] = None,
    rec_filer: Optional[Callable[[dict[str, str]], str]] = None,
) -> int:
    parser = argparse.ArgumentParser(description="Read-only liveness probe for the platform-security IAM-change detector.")
    parser.add_argument("--profile", default=None, help="local AWS profile (ADMIN runs)")
    parser.add_argument("--subscription-only", action="store_true", help="check only the alerts email subscription")
    parser.add_argument("--file-on-transition", action="store_true", help="file one rec on a healthy-to-failing transition")
    parser.add_argument("--bootstrap-window", action="store_true", help="report failures as warnings; file nothing; exit 0")
    args = parser.parse_args(argv)

    cloudwatch, sns, sts, region = clients or _make_clients(args.profile)
    failures = check(cloudwatch, sns, sts, region, subscription_only=args.subscription_only)
    if not failures:
        scope = "alerts email subscription confirmed" if args.subscription_only else f"{len(EXPECTED_ALARMS)} alarms healthy"
        print(f"SECURITY DETECTOR OK -- {scope}")
        return 0

    if args.bootstrap_window:
        for failure in failures:
            print(f"::warning::{failure}")
        print("bootstrap window (SECURITY_DETECTOR_PROVISIONED not true): reported as warnings, nothing filed")
        return 0

    for failure in failures:
        print(f"::error::{failure}")
    if args.file_on_transition:
        repo = os.environ.get("GITHUB_REPOSITORY", "")
        caller = gh_caller or _github_caller(os.environ.get("GITHUB_TOKEN", "") or os.environ.get("GH_TOKEN", ""))
        api_base = f"https://api.github.com/repos/{repo}"
        run_id = os.environ.get("GITHUB_RUN_ID", "")
        if should_file(lambda: previous_run_succeeded(caller, api_base, run_id)):
            try:
                rec_id = (rec_filer or _file_rec)(build_rec_fields(failures))
                print(f"filed {redact(str(rec_id))} (source {REC_SOURCE})")
            except Exception as exc:  # noqa: BLE001 -- rendered as its class only (public log)
                print(f"::error::rec filing failed: {type(exc).__name__}")
        else:
            print("previous scheduled run already failed: this episode's rec was filed then; not re-filing")
    return 1


if __name__ == "__main__":
    sys.exit(main())
