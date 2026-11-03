"""Tenant and project registration for the telemetry writer (T2.36, slice 2a-2, plan 3 part 1; Decision 200).

The resolve_or_register_* primitives compose inside the caller's transaction and own no BEGIN or COMMIT; register_tenant
and register_project wrap them in ducklake_telemetry_txn.run_telemetry_txn. Registration is idempotent under concurrency
by a lock-only UPDATE of the counter row (Decision 84 I-2, Decision 200 lock arm): ids are ULIDs minted once per request
outside the retry loop, the counter row only serializes writers, and a loser replays to the winner's id.

write_scd2 is deliberately not used: its schema_gate refuses the derived purpose column and it owns its own transaction.
The dimension writer here gates the caller-known inputs, adds the derived purpose after the gate, evaluates the contract's
row rules and issues the history MERGE plus the current MERGE in one transaction (Decision 81 cl.8, Decision 210 cl.1).

No function here logs or returns principal.ref (ops_tenants.yaml, Decision 101).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from src.common.ducklake_control_tables import ENTITY_COUNTERS_TABLE
from src.common.ducklake_scd2_schema import (
    CATALOG_ALIAS,
    DuckLakeRuntimeError,
    ScdTableSpec,
    WriteIdentity,
    _build_merge_current_sql,
    _build_merge_history_sql,
    _write_params,
    load_field_semantics,
    resolve_table_spec,
    schema_gate,
)
from src.common.ducklake_telemetry_txn import TelemetryRequestError, invalid_string_field, run_telemetry_txn
from src.common.ducklake_write_rules import check_write, rules_for
from src.common.ducklake_writes import mint_write_identity

TENANTS = "ops_tenants"
PROJECTS = "ops_projects"
_ATTRIBUTES = ("name", "remote")

_TENANT_CACHE: dict[tuple[str, str], str] = {}
_PROJECT_CACHE: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class CallerPrincipal:
    """The signed caller as the registration code sees it. `ref` is confidential and excluded from repr."""

    kind: str  # aws_account | configured (ops_tenants.yaml principal_kind)
    ref: str = field(repr=False)
    role_name: str = ""


@dataclass(frozen=True)
class Registration:
    entity_id: str
    created: bool
    occ_retries: int
    commit_ms: float
    tenant_id: str = ""


def new_entity_id() -> str:
    """A canonical uppercase 26-character ULID (Decision 200 cl.4)."""
    from ulid import ULID  # noqa: PLC0415

    return str(ULID())


def purpose_derivation(semantics: Mapping[str, Any]) -> Mapping[str, Any] | None:
    return semantics.get("ops_tables", {}).get(PROJECTS, {}).get("derivations", {}).get("purpose")


def purpose_for(project_ref: str, derivation: Mapping[str, Any] | None) -> str:
    """The first reserved prefix project_ref starts with, else the default; the map is the registry's, never restated."""
    if not derivation or "prefix_map" not in derivation or "default" not in derivation:
        raise DuckLakeRuntimeError(
            "field_semantics ops_tables.ops_projects.derivations.purpose is missing: regenerate the registry "
            "(bin/venv-python -m scripts.schema_to_field_semantics)"
        )
    for prefix, purpose in derivation["prefix_map"].items():
        if project_ref.startswith(prefix):
            return str(purpose)
    return str(derivation["default"])


def clear_telemetry_caches() -> None:
    _TENANT_CACHE.clear()
    _PROJECT_CACHE.clear()


def cached_tenant_id(kind: str, ref: str) -> str | None:
    return _TENANT_CACHE.get((kind, ref))


def cached_project_id(tenant_id: str, project_ref: str) -> str | None:
    return _PROJECT_CACHE.get((tenant_id, project_ref))


def lock_counter(con: Any, counter_name: str) -> None:
    """Take the registration lock: a lock-only UPDATE of the one counter row. No id is allocated from it."""
    row = con.execute(
        f"SELECT count(*) FROM {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} WHERE counter_name = ?", [counter_name]
    ).fetchone()
    rows = int(row[0])
    if rows != 1:
        raise DuckLakeRuntimeError(
            f"entity counter for {counter_name!r} has {rows} rows (expected exactly 1): run provision_telemetry "
            "(Decision 84 I-2); registration never self-seeds the lock"
        )
    con.execute(
        f"UPDATE {CATALOG_ALIAS}.{ENTITY_COUNTERS_TABLE} SET current_value = current_value + 1 WHERE counter_name = ?",
        [counter_name],
    )


def _spec(table: str, semantics: dict[str, Any] | None) -> ScdTableSpec:
    return resolve_table_spec(table, semantics if semantics is not None else load_field_semantics())


def lookup_tenant(con: Any, kind: str, ref: str, *, semantics: dict[str, Any] | None = None) -> str | None:
    """Read-only: (principal_kind, principal_ref) -> tenant_id on the current table."""
    spec = _spec(TENANTS, semantics)
    row = con.execute(
        f"SELECT tenant_id FROM {CATALOG_ALIAS}.{spec.current_table} WHERE principal_kind = ? AND principal_ref = ?",
        [kind, ref],
    ).fetchone()
    return None if row is None else str(row[0])


def _require_clean(values: Mapping[str, object]) -> None:
    bad = invalid_string_field(values)
    if bad is not None:
        raise TelemetryRequestError(400, "bad_request", {"field": bad})


def _project_row(con: Any, tenant_id: str, project_ref: str, spec: ScdTableSpec) -> dict[str, Any] | None:
    names = [name for name, _ in spec.ordered_columns]
    row = con.execute(
        f"SELECT {', '.join(names)} FROM {CATALOG_ALIAS}.{spec.current_table} WHERE tenant_id = ? AND project_ref = ?",
        [tenant_id, project_ref],
    ).fetchone()
    return None if row is None else dict(zip(names, row, strict=True))


def lookup_project(
    con: Any, tenant_id: str, project_ref: str, *, semantics: dict[str, Any] | None = None
) -> tuple[str, str] | None:
    """Read-only: (tenant_id, project_ref) -> (project_id, registration). Refuses an invalid project_ref first."""
    _require_clean({"project_ref": project_ref})
    row = _project_row(con, tenant_id, project_ref, _spec(PROJECTS, semantics))
    return None if row is None else (str(row["project_id"]), str(row["registration"]))


def _write_dimension(
    con: Any,
    table: str,
    record: dict[str, Any],
    *,
    derived: dict[str, Any],
    identity: WriteIdentity,
    prior: dict[str, Any] | None,
    semantics: dict[str, Any],
) -> None:
    schema_gate(record, semantics, table=table)
    spec = resolve_table_spec(table, semantics)
    row = {**record, **derived}
    created = prior["created_timestamp"] if prior is not None else identity.timestamp
    check_write(spec, rules_for(spec), row, created, identity.timestamp, identity.ulid, prior)
    params = _write_params(spec, row, identity, created)
    con.execute(_build_merge_history_sql(spec), params)
    con.execute(_build_merge_current_sql(spec), params)


def resolve_or_register_tenant(
    con: Any, principal: CallerPrincipal, *, identity: WriteIdentity, semantics: dict[str, Any], candidate_id: str
) -> tuple[str, bool]:
    """The tenant id for the principal, registering the first SCD2 version on a miss. Owns no BEGIN or COMMIT."""
    found = lookup_tenant(con, principal.kind, principal.ref, semantics=semantics)
    if found is not None:
        return found, False
    lock_counter(con, TENANTS)
    record = {
        "tenant_id": candidate_id,
        "principal_kind": principal.kind,
        "principal_ref": principal.ref,
        "status": "active",
        "canonical_tenant_id": None,
    }
    _write_dimension(con, TENANTS, record, derived={}, identity=identity, prior=None, semantics=semantics)
    return candidate_id, True


def resolve_or_register_project(
    con: Any,
    tenant_id: str,
    project_ref: str,
    *,
    explicit: bool,
    attributes: Mapping[str, Any],
    identity: WriteIdentity,
    semantics: dict[str, Any],
    candidate_id: str,
) -> tuple[str, bool]:
    """The project id for (tenant, project_ref), registering on a miss. Owns no BEGIN or COMMIT.

    A hit with explicit=True writes a new SCD2 version only when a SUPPLIED name or remote differs or registration moves
    unregistered -> registered (never back); an omitted attribute means unchanged. A miss with explicit=False and a
    reserved purpose is refused 403 project_unregistered (project-id.yaml).
    """
    supplied = {k: attributes[k] for k in _ATTRIBUTES if k in attributes}
    _require_clean({"project_ref": project_ref, **supplied})
    spec = resolve_table_spec(PROJECTS, semantics)
    prior = _project_row(con, tenant_id, project_ref, spec)
    if prior is not None:
        changed = any(prior[k] != v for k, v in supplied.items()) or prior["registration"] == "unregistered"
        if not explicit or not changed:
            return str(prior["project_id"]), False
        inputs = [name for name, _ in spec.ordered_columns if spec.fields[name].get("role") == "input"]
        record = {**{name: prior[name] for name in inputs}, **supplied, "registration": "registered"}
        derived = {"purpose": prior["purpose"]}
        _write_dimension(con, PROJECTS, record, derived=derived, identity=identity, prior=prior, semantics=semantics)
        return str(prior["project_id"]), False
    purpose = purpose_for(project_ref, purpose_derivation(semantics))
    if not explicit and purpose != "real":
        raise TelemetryRequestError(403, "project_unregistered", {"remedy": "register_project"})
    lock_counter(con, PROJECTS)
    record = {
        "project_id": candidate_id,
        "tenant_id": tenant_id,
        "project_ref": project_ref,
        "registration": "registered" if explicit else "unregistered",
        "status": "active",
        **supplied,
    }
    _write_dimension(con, PROJECTS, record, derived={"purpose": purpose}, identity=identity, prior=None, semantics=semantics)
    return candidate_id, True


def register_tenant(
    con: Any,
    principal: CallerPrincipal,
    *,
    semantics: dict[str, Any] | None = None,
    metric_sink: Callable[[str, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Registration:
    """Register (or find) the signed caller's tenant in one retried transaction; cache the id after the commit."""
    sem = semantics if semantics is not None else load_field_semantics()
    identity, candidate = mint_write_identity(), new_entity_id()
    outcome = run_telemetry_txn(
        con,
        lambda c: resolve_or_register_tenant(c, principal, identity=identity, semantics=sem, candidate_id=candidate),
        metric_sink=metric_sink,
        sleep=sleep,
    )
    tenant_id, created = outcome.result
    _TENANT_CACHE[(principal.kind, principal.ref)] = tenant_id
    return Registration(tenant_id, created, outcome.occ_retries, outcome.commit_ms, tenant_id)


def register_project(
    con: Any,
    principal: CallerPrincipal,
    project_ref: str,
    *,
    attributes: Mapping[str, Any] | None = None,
    semantics: dict[str, Any] | None = None,
    metric_sink: Callable[[str, float], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Registration:
    """Explicitly register a project under the caller's already-registered tenant, in one retried transaction."""
    sem = semantics if semantics is not None else load_field_semantics()
    identity, candidate = mint_write_identity(), new_entity_id()

    def body(c: Any) -> tuple[str, str, bool]:
        tenant_id = lookup_tenant(c, principal.kind, principal.ref, semantics=sem)
        if tenant_id is None:
            raise TelemetryRequestError(403, "tenant_unresolved", {"remedy": "register_tenant"})
        project_id, created = resolve_or_register_project(
            c,
            tenant_id,
            project_ref,
            explicit=True,
            attributes=attributes or {},
            identity=identity,
            semantics=sem,
            candidate_id=candidate,
        )
        return tenant_id, project_id, created

    outcome = run_telemetry_txn(con, body, metric_sink=metric_sink, sleep=sleep)
    tenant_id, project_id, created = outcome.result
    _TENANT_CACHE[(principal.kind, principal.ref)] = tenant_id
    _PROJECT_CACHE[(tenant_id, project_ref)] = project_id
    return Registration(project_id, created, outcome.occ_retries, outcome.commit_ms, tenant_id)
