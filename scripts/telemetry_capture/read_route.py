"""Telemetry read routing (PLAN-telemetry-catalog-aware-capture, rules RD-1 and RD-2).

Every telemetry read client routes the event verbs by the catalog the deployed writer serves, read from the writer's deploy
output /agent-platform/ducklake/telemetry_catalog (DUCKLAKE_TELEMETRY_CATALOG overrides it, as DUCKLAKE_READER_URL does the
reader URL): ducklake_telemetry's events are served by the telemetry reader's own Function URL, and ducklake_smoke has no
event reader since slice 2c's cleanup (the shared reader serves no event verb), so an event read on that route raises
NoEventReaderOnSmoke before any call. Ops reads always use the shared reader. Nothing resolves at construction, so a
failure raises at the first use, inside the caller's own handler. Errors name a source, never a value, a URL or an AWS
error text (Decision 101). The content route (Decision 239) is a further function_url client for the telemetry content reader:
a content verb on the event route, or an event verb on the content route, raises WrongRoute before any call.
"""

from __future__ import annotations

import os
from typing import Any

from scripts.aws_profile import resolve_aws_profile
from scripts.telemetry_capture.state import DEFAULT_CATALOG, KNOWN_CATALOGS
from src.common.ducklake_content_read_verbs import CONTENT_READS
from src.common.ducklake_event_read_verbs import EVENT_READS
from src.common.ducklake_reader_client import DuckLakeReader, make_reader

CATALOG_ENV = "DUCKLAKE_TELEMETRY_CATALOG"
CATALOG_PARAMETER = "/agent-platform/ducklake/telemetry_catalog"
READER_URL_ENV = "DUCKLAKE_TELEMETRY_READER_URL"
READER_URL_PARAMETER = "/agent-platform/ducklake/telemetry_reader_url"
READER_FUNCTION = "agent-platform-ducklake-telemetry-reader"
SHARED_READER_FUNCTION = "agent-platform-ducklake-reader"
CONTENT_READER_URL_ENV = "DUCKLAKE_TELEMETRY_CONTENT_READER_URL"
CONTENT_READER_URL_PARAMETER = "/agent-platform/ducklake/telemetry_content_reader_url"
CONTENT_READER_FUNCTION = "agent-platform-ducklake-telemetry-content"
REGION = "eu-west-2"
EVENT_VERBS = frozenset(EVENT_READS)
CONTENT_VERBS = frozenset(CONTENT_READS)


class WrongRoute(RuntimeError):
    """A content verb was sent to the event route, or an event verb to the content route (raised before any call)."""


class TelemetryCatalogUnresolved(RuntimeError):
    """The writer's catalog deploy output is unreadable or names no known catalog."""


class NoEventReaderOnSmoke(TelemetryCatalogUnresolved):
    """The deploy output names ducklake_smoke, whose events no deployed reader serves (Decision 236)."""


def _client(service: str, profile: str | None, region: str) -> Any:
    import boto3  # noqa: PLC0415

    return boto3.Session(profile_name=resolve_aws_profile(profile)).client(service, region_name=region)


def _parameter(name: str, profile: str | None, region: str = REGION) -> str | None:
    try:
        value = _client("ssm", profile, region).get_parameter(Name=name)["Parameter"]["Value"]
    except Exception:  # noqa: BLE001 -- the caller names the source; the AWS error text is never kept
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _function_url(name: str, profile: str | None, region: str) -> str | None:
    try:
        url = _client("lambda", profile, region).get_function_url_config(FunctionName=name).get("FunctionUrl")
    except Exception:  # noqa: BLE001 -- the caller names the source; the AWS error text is never kept
        return None
    return url if isinstance(url, str) and url else None


def resolve_catalog(profile: str | None = None) -> str:
    """The deploy output's catalog: the env override, else the SSM parameter; one of KNOWN_CATALOGS or a loud failure."""
    source, value = CATALOG_ENV, os.environ.get(CATALOG_ENV) or None
    if value is None:
        source, value = f"SSM {CATALOG_PARAMETER}", _parameter(CATALOG_PARAMETER, profile)
    if value is None:
        raise TelemetryCatalogUnresolved(f"the telemetry catalog is unreadable from {source}")
    if value not in KNOWN_CATALOGS:
        raise TelemetryCatalogUnresolved(f"the telemetry catalog from {source} names no known catalog")
    return value


class TelemetryReader(DuckLakeReader):
    """The telemetry reader's Function URL behind the shared reader client (its _invoke, retries and deadline error)."""

    def _reader_url(self) -> str:
        url = (
            os.environ.get(READER_URL_ENV)
            or _parameter(READER_URL_PARAMETER, self._profile, self._region)
            or _function_url(READER_FUNCTION, self._profile, self._region)
        )
        if url:
            return url.rstrip("/")
        raise RuntimeError(
            f"{READER_URL_ENV} not set, SSM {READER_URL_PARAMETER!r} unavailable and lambda:GetFunctionUrlConfig on "
            f"{READER_FUNCTION!r} failed -- cannot reach the telemetry reader"
        )

    def binding(self) -> dict[str, Any]:
        """The catalog object of the reader's describe: its pinned meta_schema and whether its data path is smoke's."""
        catalog = self._invoke({"action": "describe"}).get("catalog")
        return dict(catalog) if isinstance(catalog, dict) else {}


class ContentReader(DuckLakeReader):
    """The telemetry content reader's Function URL behind the shared reader client; serves the content verbs only."""

    def _reader_url(self) -> str:
        url = (
            os.environ.get(CONTENT_READER_URL_ENV)
            or _parameter(CONTENT_READER_URL_PARAMETER, self._profile, self._region)
            or _function_url(CONTENT_READER_FUNCTION, self._profile, self._region)
        )
        if url:
            return url.rstrip("/")
        raise RuntimeError(
            f"{CONTENT_READER_URL_ENV} not set, SSM {CONTENT_READER_URL_PARAMETER!r} unavailable and "
            f"lambda:GetFunctionUrlConfig on {CONTENT_READER_FUNCTION!r} failed -- cannot reach the telemetry content reader"
        )

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if verb not in CONTENT_VERBS:
            raise WrongRoute("the content route serves only the content verbs")
        return super().named(verb, **params)


def content_reader(profile: str | None = None) -> ContentReader:
    """The content route's reader; its URL resolves on the first call, so a failure raises inside the caller's handler."""
    return ContentReader(profile=profile)


class RoutedReader:
    """Event verbs to the reader serving the writer's catalog, everything else to *ops*; resolved on first use, then cached."""

    def __init__(self, ops: Any, profile: str | None = None, ops_verbs: dict[str, Any] | None = None) -> None:
        self._ops, self._profile, self._ops_verbs = ops, profile, ops_verbs
        self._catalog: str | None = None
        self._events: Any = None
        self._described: dict[str, Any] | None = None

    @property
    def catalog(self) -> str:
        if self._catalog is None:
            self._catalog = resolve_catalog(self._profile)
        return self._catalog

    @property
    def shared(self) -> bool:
        return self.catalog == DEFAULT_CATALOG

    @property
    def event_function(self) -> str:
        return SHARED_READER_FUNCTION if self.shared else READER_FUNCTION

    def _event_reader(self) -> Any:
        if self._events is None:
            self._events = self._ops if self.shared else TelemetryReader(profile=self._profile)
        return self._events

    def _ops_describe(self) -> dict[str, Any]:
        return dict(self._ops_verbs) if self._ops_verbs is not None else dict(self._ops.describe())

    def named(self, verb: str, **params: Any) -> list[dict[str, Any]]:
        if verb in CONTENT_VERBS:
            raise WrongRoute("a content verb is not served on the event route")
        if verb not in EVENT_VERBS:
            return list(self._ops.named(verb, **params))
        if self.shared:
            raise NoEventReaderOnSmoke(f"the telemetry catalog route {self.catalog} has no event reader")
        return list(self._event_reader().named(verb, **params))

    def describe(self) -> dict[str, Any]:
        if self._described is None:
            if self.shared:
                described = self._ops_describe()
            else:
                events = self._event_reader().describe()
                described = {verb: meta for verb, meta in self._ops_describe().items() if verb not in EVENT_VERBS}
                described.update(events)
            self._described = described
        return dict(self._described)

    def binding(self) -> dict[str, Any] | None:
        return None if self.shared else self._event_reader().binding()

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._ops, name)


def make_routed_reader(profile: str | None = None, ops: Any = None, ops_verbs: dict[str, Any] | None = None) -> RoutedReader:
    """The routed reader over *ops* (the shared reader by default); *ops_verbs* is a describe already fetched from *ops*."""
    return RoutedReader(ops if ops is not None else make_reader(profile=profile), profile, ops_verbs)
