"""scripts/ci/security_detector_probe.py fails closed, never leaks, and files once per transition."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from scripts.ci import security_detector_probe as probe
from scripts.executor.rec_write_guidance import validate_source

REGION = "zz-test-1"
ACCOUNT = "4" * 12
TOPIC = f"arn:aws:sns:{REGION}:{ACCOUNT}:agent-platform-alerts"
ENDPOINT = "operator@example.test"
_LEAK_RE = re.compile(r"arn:|\d{12}")


def _alarm(name: str, **over: Any) -> dict:
    alarm = {
        "AlarmName": name,
        "AlarmArn": f"arn:aws:cloudwatch:{REGION}:{ACCOUNT}:alarm:{name}",
        "ActionsEnabled": True,
        "AlarmActions": [TOPIC],
        "StateValue": "OK",
    }
    alarm.update(over)
    return alarm


class _CloudWatch:
    def __init__(self, alarms: list[dict], history: dict[str, list[dict]] | None = None, error: Exception | None = None):
        self.alarms, self.history, self.error = alarms, history or {}, error

    def describe_alarms(self, AlarmNames: list[str]) -> dict:  # noqa: N803
        if self.error:
            raise self.error
        return {"MetricAlarms": [a for a in self.alarms if a["AlarmName"] in AlarmNames]}

    def describe_alarm_history(self, AlarmName: str, **_: Any) -> dict:  # noqa: N803
        return {"AlarmHistoryItems": self.history.get(AlarmName, [])}


class _Sns:
    def __init__(self, subs: list[dict], attrs: dict | None = None):
        self.subs, self.attrs = subs, attrs if attrs is not None else {"Policy": '{"Statement": [{"Effect": "Allow"}]}'}

    def list_subscriptions_by_topic(self, TopicArn: str, NextToken: str | None = None) -> dict:  # noqa: N803
        if NextToken is None and len(self.subs) > 1:
            return {"Subscriptions": self.subs[:1], "NextToken": "t"}
        return {"Subscriptions": self.subs[1:] if NextToken else self.subs}

    def get_topic_attributes(self, TopicArn: str) -> dict:  # noqa: N803
        return {"Attributes": self.attrs}


class _Sts:
    def get_caller_identity(self) -> dict:
        return {"Account": ACCOUNT}


def _confirmed() -> list[dict]:
    return [{"Protocol": "email", "Endpoint": ENDPOINT, "SubscriptionArn": f"{TOPIC}:sub-id"}]


def _healthy() -> dict:
    return {
        "cloudwatch": _CloudWatch([_alarm(n) for n in probe.EXPECTED_ALARMS]),
        "sns": _Sns(_confirmed()),
    }


def _run(env: dict, **kw: Any) -> list[str]:
    return probe.check(env["cloudwatch"], env["sns"], _Sts(), REGION, **kw)


def _denied(op: str) -> ClientError:
    msg = f"User: arn:aws:sts::{ACCOUNT}:assumed-role/x/y is not authorized to perform {op} on {TOPIC}"
    return ClientError({"Error": {"Code": "AccessDenied", "Message": msg}}, op)


def _scenario(name: str) -> dict:
    env = _healthy()
    cw: _CloudWatch = env["cloudwatch"]
    if name == "alarm missing":
        cw.alarms = [a for a in cw.alarms if a["AlarmName"] != "platform-security-detector-tamper"]
    elif name == "actions disabled":
        cw.alarms[0]["ActionsEnabled"] = False
    elif name == "wrong topic":
        cw.alarms[0]["AlarmActions"] = [TOPIC + "-other"]
    elif name in ("heartbeat ALARM", "heartbeat INSUFFICIENT_DATA"):
        state = name.split()[1]
        cw.alarms = [
            a if a["AlarmName"] != probe.HEARTBEAT_ALARM else _alarm(probe.HEARTBEAT_ALARM, StateValue=state)
            for a in cw.alarms
        ]
    elif name == "subscription pending":
        env["sns"] = _Sns([{"Protocol": "email", "Endpoint": ENDPOINT, "SubscriptionArn": "PendingConfirmation"}])
    elif name == "no subscription":
        env["sns"] = _Sns([])
    elif name == "failed alarm action":
        cw.history = {"platform-security-denied-iam-write": [{"HistorySummary": f"Failed to execute action {TOPIC}"}]}
    elif name == "topic KMS key":
        env["sns"] = _Sns(_confirmed(), {"KmsMasterKeyId": f"arn:aws:kms:{REGION}:{ACCOUNT}:key/k"})
    elif name == "topic policy Deny":
        env["sns"] = _Sns(_confirmed(), {"Policy": '{"Statement": [{"Effect": "Deny", "Principal": "*"}]}'})
    elif name == "AccessDenied":
        cw.error = _denied("DescribeAlarms")
    return env


_FAILURE_CLASSES = {
    "alarm missing": "alarm missing",
    "actions disabled": "actions disabled",
    "wrong topic": "does not target the alerts topic",
    "heartbeat ALARM": "heartbeat state ALARM",
    "heartbeat INSUFFICIENT_DATA": "heartbeat state INSUFFICIENT_DATA",
    "subscription pending": "pending confirmation",
    "no subscription": "no email subscription",
    "failed alarm action": "failed alarm action",
    "topic KMS key": "KmsMasterKeyId",
    "topic policy Deny": "Deny statement",
    "AccessDenied": "aws error AccessDenied",
}


def _main(env: dict, argv: list[str], filed: list[dict], previous: Any = "success") -> int:
    def caller(url: str) -> Any:
        if isinstance(previous, Exception):
            raise previous
        runs = [{"id": 2, "conclusion": None}] + ([{"id": 1, "conclusion": previous}] if previous else [])
        return {"workflow_runs": runs}

    def filer(fields: dict) -> str:
        filed.append(copy.deepcopy(fields))
        return "rec-9999"

    return probe.main(argv, clients=(env["cloudwatch"], env["sns"], _Sts(), REGION), gh_caller=caller, rec_filer=filer)


class TestSecurityDetectorProbe:
    def test_healthy_reports_no_failures(self) -> None:
        assert _run(_healthy()) == []

    @pytest.mark.parametrize("name", sorted(_FAILURE_CLASSES))
    def test_each_failure_class_is_reported(self, name: str) -> None:
        failures = _run(_scenario(name))
        assert any(_FAILURE_CLASSES[name] in f for f in failures), failures

    @pytest.mark.parametrize("name", sorted(_FAILURE_CLASSES))
    def test_output_and_rec_never_leak(
        self, name: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GITHUB_RUN_ID", "2")
        filed: list[dict] = []
        assert _main(_scenario(name), ["--file-on-transition"], filed) == 1
        rendered = capsys.readouterr().out + repr(filed) + repr(_run(_scenario(name)))
        assert filed, "a transition from success must file"
        assert ENDPOINT not in rendered
        assert not _LEAK_RE.search(rendered), rendered

    def test_subscription_only_skips_alarm_legs(self) -> None:
        assert _run(_scenario("alarm missing"), subscription_only=True) == []
        assert _run(_scenario("subscription pending"), subscription_only=True) != []

    @pytest.mark.parametrize(
        ("previous", "files"),
        [("success", True), (None, True), (RuntimeError("api down"), True), ("failure", False)],
    )
    def test_transition_rule(self, previous: Any, files: bool, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GITHUB_RUN_ID", "2")
        filed: list[dict] = []
        assert _main(_scenario("heartbeat ALARM"), ["--file-on-transition"], filed, previous=previous) == 1
        assert bool(filed) is files
        if files:
            assert filed[0]["source"] == probe.REC_SOURCE and filed[0]["priority"] == "High"

    def test_previous_run_lookup_skips_the_current_run(self) -> None:
        payload = {"workflow_runs": [{"id": 7, "conclusion": None}, {"id": 6, "conclusion": "failure"}]}
        assert probe.previous_run_succeeded(lambda _u: payload, "https://api", "7") is False
        assert probe.previous_run_succeeded(lambda _u: {"workflow_runs": [{"id": 7}]}, "https://api", "7") is None
        with pytest.raises(RuntimeError):
            probe.previous_run_succeeded(lambda _u: None, "https://api", "7")

    def test_bootstrap_window_exits_zero_and_files_nothing(self, capsys: pytest.CaptureFixture[str]) -> None:
        filed: list[dict] = []
        assert _main(_scenario("alarm missing"), ["--file-on-transition", "--bootstrap-window"], filed) == 0
        assert filed == []
        assert "::warning::" in capsys.readouterr().out

    def test_main_exit_codes(self) -> None:
        assert _main(_healthy(), [], []) == 0
        filed: list[dict] = []
        assert _main(_scenario("topic policy Deny"), [], filed) == 1
        assert filed == [], "without --file-on-transition nothing is filed"

    def test_filing_failure_still_exits_one(self, capsys: pytest.CaptureFixture[str]) -> None:
        def boom(_fields: dict) -> str:
            raise RuntimeError(f"writer at {TOPIC} refused")

        env = _scenario("alarm missing")
        rc = probe.main(
            ["--file-on-transition"],
            clients=(env["cloudwatch"], env["sns"], _Sts(), REGION),
            gh_caller=lambda _u: {"workflow_runs": []},
            rec_filer=boom,
        )
        assert rc == 1
        out = capsys.readouterr().out
        assert "rec filing failed: RuntimeError" in out and not _LEAK_RE.search(out)

    def test_source_is_registered(self) -> None:
        validate_source(probe.REC_SOURCE)  # raises on an unregistered source, as file_rec would at write time
        with pytest.raises(ValueError):
            validate_source("security_detector_stale_unregistered")

    def test_expected_alarms_match_the_hcl(self) -> None:
        alarms_tf = (Path(__file__).resolve().parents[1] / "terraform/bootstrap/platform_security_alarms.tf").read_text(
            encoding="utf-8"
        )
        hcl_names = set(re.findall(r'^\s*alarm_name\s*=\s*"([^"]+)"', alarms_tf, re.M))
        assert hcl_names and set(probe.EXPECTED_ALARMS) == hcl_names

    def test_edge_classes(self) -> None:
        assert probe.error_code(RuntimeError("x")) == "RuntimeError"
        env = _healthy()

        class _BadSts:
            def get_caller_identity(self) -> dict:
                raise _denied("GetCallerIdentity")

        assert probe.check(env["cloudwatch"], env["sns"], _BadSts(), REGION) == ["identity: aws error AccessDenied"]
        paged = _Sns(_confirmed() + [{"Protocol": "email", "SubscriptionArn": "PendingConfirmation"}])
        assert probe.check_subscription(paged, TOPIC) == []
        assert probe.check_topic(_Sns([], {"Policy": "not json"}), TOPIC) == ["topic: alerts topic policy is not parseable"]
        assert probe.check_topic(_Sns([], {"Policy": '{"Statement": {"Effect": "Deny"}}'}), TOPIC) != []

    def test_github_caller(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(RuntimeError):
            probe._github_caller("")("https://api")

        class _Resp:
            def __enter__(self) -> "_Resp":
                return self

            def __exit__(self, *_: Any) -> None:
                return None

            def read(self) -> bytes:
                return b'{"workflow_runs": []}'

        seen: list[Any] = []
        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout: seen.append(req) or _Resp())
        assert probe._github_caller("tok")("https://api/x") == {"workflow_runs": []}
        assert seen[0].get_header("Authorization") == "Bearer tok"

    def test_default_filer_and_clients(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("scripts.ops_data_portal.file_rec", lambda fields: f"rec-{len(fields)}")
        assert probe._file_rec({"a": "b"}) == "rec-1"

        class _Session:
            def __init__(self, profile_name: str | None = None, region: str | None = REGION):
                self.profile_name, self.region_name = profile_name, region

            def client(self, name: str, region_name: str) -> str:
                return f"{name}@{region_name}"

        monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
        monkeypatch.setattr("boto3.Session", _Session)
        assert probe._make_clients("p") == ("cloudwatch@" + REGION, "sns@" + REGION, "sts@" + REGION, REGION)
        assert probe._make_clients(None)[3] == REGION
        monkeypatch.setattr("boto3.Session", lambda **_: _Session(region=None))
        with pytest.raises(SystemExit):
            probe._make_clients(None)


def test_rec_fields_pass_the_portal_gate() -> None:
    """The probe's automated rec names the platform-security-* family (Decision 202), which the glossary lists."""
    from scripts.ops_portal.risk_scoring import _derive_computed_fields  # noqa: PLC0415
    from scripts.ops_portal.write_validators import gate_write  # noqa: PLC0415

    fields = dict(probe.build_rec_fields(["x"]))
    _derive_computed_fields(fields)
    gate_write("ops_recommendations", fields, insert=True)
