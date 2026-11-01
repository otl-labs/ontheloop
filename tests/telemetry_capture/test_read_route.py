"""Mirror tests for scripts/telemetry_capture/read_route.py: RD-1's routing by the deploy output and RD-2's loud resolution.

boto3 is faked at its Session, so every SSM and Lambda API call the module makes is counted and nothing reaches AWS.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.telemetry_capture import read_route, runner, state
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_named_reads import NAMED_READS

SMOKE, PROD = "ducklake_smoke", "ducklake_telemetry"
EVENT_VERB = sorted(EVENT_READS)[0]
OPS_VERB = sorted(NAMED_READS)[0]
DENIED = "AccessDenied for a principal the message must never carry"


class _Api:
    def __init__(self, service: str, aws: Aws) -> None:
        self.service, self.aws = service, aws

    def get_parameter(self, *, Name: str) -> dict[str, Any]:  # noqa: N803 -- boto3's keyword
        return {"Parameter": {"Value": self.aws.answer(self.service, Name)}}

    def get_function_url_config(self, *, FunctionName: str) -> dict[str, Any]:  # noqa: N803 -- boto3's keyword
        return {"FunctionUrl": self.aws.answer(self.service, FunctionName)}


class Aws:
    """A fake boto3.Session: answers maps (service, name) to a value, or to an exception it raises."""

    def __init__(self, answers: dict[tuple[str, str], Any] | None = None) -> None:
        self.answers, self.calls = dict(answers or {}), []

    def answer(self, service: str, name: str) -> Any:
        self.calls.append((service, name))
        value = self.answers.get((service, name), RuntimeError(DENIED))
        if isinstance(value, BaseException):
            raise value
        return value

    def session(self, profile_name: str | None = None) -> Any:
        aws = self

        class _Session:
            def client(self, service: str, region_name: str) -> _Api:
                assert region_name == read_route.REGION
                return _Api(service, aws)

        return _Session()


class Ops:
    def __init__(self, verbs: dict[str, Any] | None = None) -> None:
        self.verbs = verbs if verbs is not None else {OPS_VERB: {"params": "ops"}, EVENT_VERB: {"params": "shared"}}
        self.calls: list[tuple[str, Any]] = []

    def describe(self) -> dict[str, Any]:
        self.calls.append(("describe", None))
        return dict(self.verbs)

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append((verb, params))
        return [{"reader": "ops", "verb": verb}]

    def current_state(self, table: str) -> str:
        return f"ops:{table}"


class Events:
    built = 0

    def __init__(self, profile: str | None = None) -> None:
        type(self).built += 1
        self.profile, self.calls = profile, []

    def describe(self) -> dict[str, Any]:
        self.calls.append("describe")
        return {EVENT_VERB: {"params": "telemetry"}}

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        self.calls.append(verb)
        return [{"reader": "telemetry", "verb": verb}]

    def binding(self) -> dict[str, Any]:
        return {"meta_schema": PROD, "data_path_is_smoke": False}


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Aws:
    import boto3

    fake = Aws()
    monkeypatch.setattr(boto3, "Session", fake.session)
    for name in (read_route.CATALOG_ENV, read_route.READER_URL_ENV, "AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(name, raising=False)
    Events.built = 0
    monkeypatch.setattr(read_route, "TelemetryReader", Events)
    return fake


def _unresolved(profile: str | None = None) -> read_route.TelemetryCatalogUnresolved:
    with pytest.raises(read_route.TelemetryCatalogUnresolved) as raised:
        read_route.resolve_catalog(profile)
    return raised.value


def test_catalog_resolves_env_then_ssm_and_refuses_unknown_values(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    ssm = ("ssm", read_route.CATALOG_PARAMETER)
    aws.answers[ssm] = f" {SMOKE}\n"
    assert read_route.resolve_catalog() == SMOKE and aws.calls == [ssm]
    monkeypatch.setenv(read_route.CATALOG_ENV, PROD)
    assert read_route.resolve_catalog() == PROD and aws.calls == [ssm]

    monkeypatch.setenv(read_route.CATALOG_ENV, "ducklake_ops")
    error = _unresolved()
    assert str(error) == f"the telemetry catalog from {read_route.CATALOG_ENV} names no known catalog"
    monkeypatch.delenv(read_route.CATALOG_ENV)
    source = f"SSM {read_route.CATALOG_PARAMETER}"
    unknown, unreadable = f"the telemetry catalog from {source} names no known catalog", f"is unreadable from {source}"
    for answer, expected in (
        ("value-never-echoed", unknown),
        ("  ", f"the telemetry catalog {unreadable}"),
        (None, f"the telemetry catalog {unreadable}"),
        (RuntimeError(DENIED), f"the telemetry catalog {unreadable}"),
    ):
        aws.answers[ssm] = answer
        error = _unresolved()
        assert str(error) == expected and error.__cause__ is None and error.__context__ is None


def test_event_verbs_route_by_the_deploy_output(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(read_route.CATALOG_ENV, SMOKE)
    ops = Ops()
    smoke = read_route.make_routed_reader(ops=ops)
    with pytest.raises(read_route.NoEventReaderOnSmoke):
        smoke.named(EVENT_VERB, day="d")
    assert ops.calls == []
    assert (smoke.catalog, smoke.shared, smoke.event_function) == (SMOKE, True, read_route.SHARED_READER_FUNCTION)
    assert smoke.binding() is None and Events.built == 0

    monkeypatch.setenv(read_route.CATALOG_ENV, PROD)
    ops = Ops()
    prod = read_route.make_routed_reader("a-profile", ops=ops)
    assert prod.named(EVENT_VERB, day="d") == [{"reader": "telemetry", "verb": EVENT_VERB}] and ops.calls == []
    assert prod.named(EVENT_VERB) and Events.built == 1 and prod._events.profile == "a-profile"
    assert (prod.catalog, prod.shared, prod.event_function) == (PROD, False, read_route.READER_FUNCTION)


def test_an_event_verb_on_the_smoke_route_raises_without_a_call(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    resolved: list[str | None] = []
    monkeypatch.setattr(read_route, "resolve_catalog", lambda profile=None: resolved.append(profile) or SMOKE)
    ops = Ops()
    routed = read_route.make_routed_reader(ops=ops)
    assert routed.named(OPS_VERB, day="d") == [{"reader": "ops", "verb": OPS_VERB}]
    for verb in sorted(EVENT_READS):
        with pytest.raises(read_route.NoEventReaderOnSmoke) as raised:
            routed.named(verb, day="d")
        assert isinstance(raised.value, read_route.TelemetryCatalogUnresolved)
        assert str(raised.value) == f"the telemetry catalog route {SMOKE} has no event reader"
    assert ops.calls == [(OPS_VERB, {"day": "d"})] and Events.built == 0 and aws.calls == [] and resolved == [None]


def test_describe_merges_ops_rows_with_the_event_readers_rows(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(read_route.CATALOG_ENV, PROD)
    ops = Ops()
    routed = read_route.make_routed_reader(ops=ops)
    merged = routed.describe()
    assert merged == {OPS_VERB: {"params": "ops"}, EVENT_VERB: {"params": "telemetry"}}
    merged.clear()
    assert routed.describe() == {OPS_VERB: {"params": "ops"}, EVENT_VERB: {"params": "telemetry"}}
    assert ops.calls == [("describe", None)] and routed._events.calls == ["describe"]

    fetched = Ops()
    routed = read_route.make_routed_reader(ops=fetched, ops_verbs={OPS_VERB: {"params": "fetched"}})
    assert routed.describe() == {OPS_VERB: {"params": "fetched"}, EVENT_VERB: {"params": "telemetry"}} and fetched.calls == []

    monkeypatch.setenv(read_route.CATALOG_ENV, SMOKE)
    ops = Ops()
    assert read_route.make_routed_reader(ops=ops).describe() == ops.verbs and Events.built == 2


def test_ops_reads_always_use_the_ops_reader(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    for catalog in (SMOKE, PROD):
        monkeypatch.setenv(read_route.CATALOG_ENV, catalog)
        ops = Ops()
        routed = read_route.make_routed_reader(ops=ops)
        assert routed.named(OPS_VERB, n=1) == [{"reader": "ops", "verb": OPS_VERB}] and ops.calls == [(OPS_VERB, {"n": 1})]
        assert routed.current_state("ops_recommendations") == "ops:ops_recommendations"
        with pytest.raises(AttributeError):
            routed._private  # noqa: B018 -- the attribute read is the assertion
    monkeypatch.delenv(read_route.CATALOG_ENV)
    unresolvable = read_route.make_routed_reader(ops=Ops())
    assert unresolvable.named(OPS_VERB) and aws.calls == [] and Events.built == 0


def test_routed_reader_resolves_lazily(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[str | None] = []
    monkeypatch.setattr(read_route, "make_reader", lambda profile=None: built.append(profile) or Ops())
    routed = read_route.make_routed_reader("a-profile")
    assert built == ["a-profile"] and aws.calls == [] and Events.built == 0
    for use in (lambda: routed.named(EVENT_VERB), routed.describe, lambda: routed.catalog, routed.binding):
        with pytest.raises(read_route.TelemetryCatalogUnresolved):
            use()
    assert aws.calls == [("ssm", read_route.CATALOG_PARAMETER)] * 4 and Events.built == 0


def test_telemetry_reader_url_resolution_order(monkeypatch: pytest.MonkeyPatch) -> None:
    import boto3

    fake = Aws()
    monkeypatch.setattr(boto3, "Session", fake.session)
    for name in (read_route.READER_URL_ENV, "AWS_ACCESS_KEY_ID", "AWS_LAMBDA_FUNCTION_NAME"):
        monkeypatch.delenv(name, raising=False)
    ssm, api = ("ssm", read_route.READER_URL_PARAMETER), ("lambda", read_route.READER_FUNCTION)
    reader = read_route.TelemetryReader()

    with pytest.raises(RuntimeError) as raised:
        reader._reader_url()
    message = str(raised.value)
    assert read_route.READER_URL_ENV in message and read_route.READER_URL_PARAMETER in message
    assert read_route.READER_FUNCTION in message and DENIED not in message and fake.calls == [ssm, api]

    fake.answers[api] = "https://api.example/"
    assert reader._reader_url() == "https://api.example"
    fake.answers[ssm] = "https://ssm.example/"
    assert reader._reader_url() == "https://ssm.example"
    monkeypatch.setenv(read_route.READER_URL_ENV, "https://env.example/")
    fake.calls.clear()
    assert reader._reader_url() == "https://env.example" and fake.calls == []


def test_telemetry_reader_binding_reads_its_describe(monkeypatch: pytest.MonkeyPatch) -> None:
    payloads: list[dict[str, Any]] = []
    answers = [{"catalog": {"meta_schema": PROD, "data_path_is_smoke": False}, "verbs": {}}, {"verbs": {}}, {"catalog": PROD}]
    monkeypatch.setattr(
        read_route.TelemetryReader, "_invoke", lambda self, payload: payloads.append(payload) or answers.pop(0)
    )
    reader = read_route.TelemetryReader()
    assert reader.binding() == {"meta_schema": PROD, "data_path_is_smoke": False}
    assert reader.binding() == {} and reader.binding() == {}
    assert payloads == [{"action": "describe"}] * 3

    monkeypatch.setenv(read_route.CATALOG_ENV, PROD)
    monkeypatch.setattr(read_route, "TelemetryReader", Events)
    assert read_route.make_routed_reader(ops=Ops()).binding() == {"meta_schema": PROD, "data_path_is_smoke": False}


def test_event_verb_set_equals_the_registry() -> None:
    assert read_route.EVENT_VERBS == frozenset(EVENT_READS) and len(read_route.EVENT_VERBS) == len(EVENT_READS)
    assert not read_route.EVENT_VERBS & set(NAMED_READS)


def test_known_catalogs_are_one_tuple_shared_with_capture() -> None:
    assert read_route.KNOWN_CATALOGS is runner.KNOWN_CATALOGS is state.KNOWN_CATALOGS
    assert state.KNOWN_CATALOGS == (SMOKE, PROD) and read_route.DEFAULT_CATALOG is state.DEFAULT_CATALOG == SMOKE


def test_the_read_clients_import_where_fcntl_is_absent() -> None:
    code = (
        "import sys; sys.modules['fcntl'] = None\n"
        "import scripts.agent_sdk.mcp_server, scripts.telemetry_verify.__main__\n"
        "from scripts.telemetry_capture import read_route\n"
        "print(read_route.DEFAULT_CATALOG)\n"
    )
    root = Path(__file__).resolve().parents[2]
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace", check=False
    )
    assert (done.returncode, done.stdout.strip()) == (0, SMOKE), done.stderr[-2000:]


CONTENT_VERB = sorted(read_route.CONTENT_VERBS)[0]


def test_content_route_resolves_env_then_ssm_then_the_function_url(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    reader = read_route.content_reader("a-profile")
    assert aws.calls == []
    monkeypatch.setenv(read_route.CONTENT_READER_URL_ENV, "https://env.example/")
    assert reader._reader_url() == "https://env.example" and aws.calls == []
    monkeypatch.delenv(read_route.CONTENT_READER_URL_ENV)
    aws.answers[("ssm", read_route.CONTENT_READER_URL_PARAMETER)] = "https://ssm.example/\n"
    assert reader._reader_url() == "https://ssm.example"
    aws.answers[("ssm", read_route.CONTENT_READER_URL_PARAMETER)] = None
    aws.answers[("lambda", read_route.CONTENT_READER_FUNCTION)] = "https://fn.example/"
    assert reader._reader_url() == "https://fn.example"


def test_content_route_errors_name_a_source_never_a_value(aws: Aws) -> None:
    with pytest.raises(RuntimeError) as raised:
        read_route.content_reader()._reader_url()
    assert read_route.CONTENT_READER_URL_ENV in str(raised.value) and DENIED not in str(raised.value)


def test_content_and_event_verbs_never_cross_routes(aws: Aws, monkeypatch: pytest.MonkeyPatch) -> None:
    assert read_route.CONTENT_VERBS.isdisjoint(read_route.EVENT_VERBS)
    monkeypatch.setenv(read_route.CATALOG_ENV, PROD)
    ops = Ops()
    routed = read_route.make_routed_reader(ops=ops)
    with pytest.raises(read_route.WrongRoute):
        routed.named(CONTENT_VERB, session_id="x")
    assert ops.calls == [] and Events.built == 0
    sent: list[Any] = []
    monkeypatch.setattr(read_route.DuckLakeReader, "named", lambda self, verb, **p: sent.append(verb) or [])
    with pytest.raises(read_route.WrongRoute):
        read_route.content_reader().named(EVENT_VERB)
    with pytest.raises(read_route.WrongRoute):
        read_route.content_reader().named(OPS_VERB)
    assert sent == [] and read_route.content_reader().named(CONTENT_VERB) == [] and sent == [CONTENT_VERB]
