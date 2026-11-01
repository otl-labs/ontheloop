"""Red-before/green-after invariants for the telemetry event-journal contract re-model
(Decision 199/200, PLAN-telemetry-event-journal-contracts).

Loads every scope contract through scripts.contracts load_contract + resolve_refs and asserts
the event-journal, identity, content, dimension, lexicon and substrate invariants named in the
plan's acceptance criteria. Phrase assertions are case-insensitive over whitespace-normalised
text. This module does not exist on the pre-change tree (red before); every test below fails
importing scripts.contracts symbols that pre-date this plan only insofar as the CONTRACT bodies
themselves lacked the shapes asserted here.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from scripts.contracts import load_contract, load_contract_meta, resolve_refs

_CONTRACTS_DIR = Path(__file__).resolve().parents[3] / "docs" / "contracts"


def _norm(text: str) -> str:
    return " ".join(text.split()).lower()


def _load(name: str):
    return load_contract(_CONTRACTS_DIR / f"{name}.yaml")


def _resolved(name: str) -> dict:
    doc = _load(name)
    return resolve_refs(doc, _CONTRACTS_DIR)


class TestSessionsEventJournalShape:
    def test_sessions_event_journal_shape(self) -> None:
        doc = _load("telemetry_sessions")
        resolved = _resolved("telemetry_sessions")

        assert doc.contract.contract_version == 2
        assert doc.governance.partition_by == (
            "history=year(session_started_at), month(session_started_at), day(session_started_at)"
        )

        event_kind = resolved["event_kind"]
        assert set(event_kind.dq_intent["accepted_values"]["values"]) == {
            "open",
            "resume",
            "compact",
            "close",
            "annotate",
        }

        # Shared envelope fields present and tightened NOT NULL.
        for field in ("event_id", "event_timestamp", "session_started_at", "tenant_id", "session_id"):
            assert resolved[field].dq_intent["not_null"] is True, field

        # ingested_at retired in favour of the envelope's created_timestamp.
        assert "ingested_at" not in doc.fields
        assert resolved["created_timestamp"].dq_intent["not_null"] is True

        # outcome is stored once (per-event-kind requirement), never table-wide not-null,
        # and "running" is no longer an accepted stored value.
        outcome = doc.fields["outcome"]
        assert "not_null" not in outcome.dq_intent
        assert outcome.dq_intent["required_when"] == {"event_kind": ["close"]}
        assert "running" not in outcome.dq_intent["accepted_values"]["values"]

        # start/end/duration and the roll-ups are DERIVED (timing: read), no physical column.
        derived_fields = (
            "duration_seconds",
            "process_event_total",
            "rework_total",
            "exception_total",
            "steps_completed_total",
        )
        for derived_field in derived_fields:
            derivation = doc.fields[derived_field].derivation
            assert derivation is not None, derived_field
            assert derivation["timing"] == "read", derived_field

        # steps_total is STORED (known at open), not a *_total derived roll-up.
        assert doc.fields["steps_total"].derivation is None

        # Post-close facts are owned by the annotate event_kind.
        for annotate_field in ("pr_url", "ci_outcome", "coverage_after"):
            assert doc.fields[annotate_field].dq_intent["required_when"] == {"event_kind": ["annotate"]}, annotate_field


class TestObservationsEventJournalShape:
    def test_observations_event_journal_shape(self) -> None:
        doc = _load("telemetry_observations")
        resolved = _resolved("telemetry_observations")

        assert doc.contract.contract_version == 2

        observation_type = doc.fields["observation_type"]
        values = set(observation_type.dq_intent["accepted_values"]["values"])
        assert {"turn", "tool_call"} <= values
        assert values == {"phase", "step", "turn", "tool_call", "process_event", "model_call"}

        event_kind = resolved["event_kind"]
        assert set(event_kind.dq_intent["accepted_values"]["values"]) == {"open", "close", "point"}

        # model_call grain: four token classes stored once per point row.
        for token_field in ("tokens_input", "tokens_output", "tokens_cache_read", "tokens_cache_creation"):
            required_when = doc.fields[token_field].dq_intent["required_when"]
            assert required_when == {"event_kind": ["point"], "observation_type": ["model_call"]}, token_field
        assert "cost_usd_reported" in doc.fields
        cost_usd = doc.fields["cost_usd"]
        assert cost_usd.derivation is not None
        assert cost_usd.derivation["timing"] == "read"
        assert "cost_usd_reported" in cost_usd.derivation["formula"]

        # outcome vocabulary and its close/tool_call scoping.
        outcome = doc.fields["outcome"]
        assert set(outcome.dq_intent["accepted_values"]["values"]) == {"success", "error", "blocked", "interrupted"}
        assert outcome.dq_intent["required_when"] == {"event_kind": ["close"], "observation_type": ["tool_call"]}

        # process_event name grammar declared.
        assert "<source>:<signature>" in doc.fields["name"].semantics

        # Retired stored friction columns absent; NO stored friction_class/classifier_version/tier.
        for retired in ("tier", "friction_class", "classifier_version", "ingested_at"):
            assert retired not in doc.fields, retired

        # session_ref NOT NULL, parent_observation_ref nullable.
        assert resolved["session_id"].dq_intent["not_null"] is True
        parent_obs = resolved["parent_observation_id"]
        assert "not_null" not in parent_obs.dq_intent


class TestTranscriptsContentModel:
    def test_transcripts_content_model(self) -> None:
        doc = _load("telemetry_transcripts")
        resolved = _resolved("telemetry_transcripts")

        assert doc.contract.contract_version == 2

        event_kind = resolved["event_kind"]
        assert set(event_kind.dq_intent["accepted_values"]["values"]) == {"point"}

        content = doc.fields["content"]
        content_uri = doc.fields["content_uri"]
        assert content.nullable is True
        assert content_uri.nullable is True
        assert content.dq_intent["content_inline_threshold_bytes"] == 65536

        for required_field in ("content_sha256", "content_bytes", "content_truncated"):
            assert doc.fields[required_field].nullable is False
            assert doc.fields[required_field].dq_intent["not_null"] is True

        purpose_values = set(doc.fields["purpose"].dq_intent["accepted_values"]["values"])
        assert purpose_values == {"prompt", "response", "thinking", "tool_input", "tool_result", "tool_output", "system"}
        assert "transcript" not in purpose_values

        origin_values = set(doc.fields["origin"].dq_intent["accepted_values"]["values"])
        assert origin_values == {"human", "harness", "agent", "tool"}

        assert resolved["session_id"].dq_intent["not_null"] is True
        assert resolved["observation_id"].dq_intent["not_null"] is True

        for retired in ("local_path", "s3_key", "size_bytes", "created_at", "ingested_at"):
            assert retired not in doc.fields, retired


class TestAgentsEventJournalShape:
    def test_agents_event_journal_shape(self) -> None:
        doc = _load("telemetry_agents")
        resolved = _resolved("telemetry_agents")

        assert doc.contract.contract_version == 2

        agent_run_id = doc.fields["agent_run_id"]
        assert "telemetry_agents:agent_run_id" in agent_run_id.semantics
        assert agent_run_id.nullable is False

        entity_ref_note = doc.fields["session_id"]  # sanity: session_id present
        assert entity_ref_note is not None

        assert resolved["session_id"].dq_intent["not_null"] is True
        observation = resolved["observation_id"]
        assert "not_null" not in observation.dq_intent

        for total_field in ("tokens_input_total", "tokens_output_total"):
            derivation = doc.fields[total_field].derivation
            assert derivation is not None, total_field
            assert derivation["timing"] == "read"
            assert "model_call" in derivation["formula"]

        assert doc.fields["duration_seconds"].derivation is not None

        assert "ingested_at" not in doc.fields


class TestEventEnvelopeSharedFields:
    def test_event_envelope_shared_fields(self) -> None:
        envelope = _load("telemetry-event-envelope")
        expected_fields = {
            "event_id",
            "event_kind",
            "event_timestamp",
            "session_started_at",
            "source_ordinal",
            "external_ref",
            "entity_ref",
            "producer",
            "producer_version",
            "parser_version",
            "created_timestamp",
        }
        assert expected_fields <= set(envelope.fields)
        for name, spec in envelope.fields.items():
            if name in expected_fields:
                assert spec.description, name
                assert spec.semantics, name
                assert spec.dq_intent is not None, name

        for table in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
            table_doc = _load(table)
            for field_name in expected_fields:
                spec = table_doc.fields.get(field_name)
                assert spec is not None, f"{table}.{field_name} missing"
                assert spec.ref == f"telemetry-event-envelope.yaml#/contract/fields/{field_name}", f"{table}.{field_name}"


class TestTenantAndProjectIdentity:
    def test_tenant_and_project_identity(self) -> None:
        tenant = _load("tenant-id")
        assert tenant.contract.status.value == "ratified"
        tenant_field = tenant.fields["tenant_id"]
        combined = f"{tenant_field.description or ''} {tenant_field.semantics or ''}".lower()
        assert "opaque" in combined
        assert "immutable" in combined
        assert "write boundary" in combined

        project = _load("project-id")
        assert project.contract.contract_version == 2
        # Live-text surfaces only (description/semantics/governance_notes) -- amendment_log and
        # previous_versions may legitimately name the retired default to explain what changed.
        live_parts = [project.contract.description or "", project.governance_notes or ""]
        for spec in project.fields.values():
            live_parts.extend([spec.description or "", spec.semantics or "", spec.governance_notes or ""])
        project_live_text = _norm(" ".join(live_parts))
        assert "theseus" not in project_live_text
        assert "git-remote" not in project_live_text and "git remote" not in project_live_text

        for table in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
            table_doc = _load(table)
            for key in ("tenant_id", "project_id"):
                spec = table_doc.fields[key]
                assert spec.dq_intent_local["not_null"] is True, f"{table}.{key}"


class TestEntityKeyIdentityRule:
    def test_entity_key_identity_rule(self) -> None:
        for name in ("observation-id", "parent-observation-id", "session-id"):
            doc = _load(name)
            assert doc.contract.contract_version == 2, name
            assert doc.previous_versions, name

        envelope_text = _norm(open(_CONTRACTS_DIR / "telemetry-event-envelope.yaml", encoding="utf-8").read())
        assert "sha256" in envelope_text
        assert "domain tag" in envelope_text

        # No live description/semantics field still claims mint-once-and-propagate.
        stale_pattern = re.compile(r"minted (?:exactly )?once|never re-mints", re.IGNORECASE)
        for name in ("observation-id", "parent-observation-id", "session-id"):
            doc = _load(name)
            for field_name, spec in doc.fields.items():
                for attr in ("description", "semantics", "governance_notes"):
                    text = getattr(spec, attr, None) or ""
                    assert not stale_pattern.search(text), f"{name}.{field_name}.{attr}"
            assert not stale_pattern.search(doc.governance_notes or ""), name


class TestLexiconTemporalAndIdentity:
    def test_lexicon_temporal_and_identity(self) -> None:
        lexicon = yaml.safe_load(open(_CONTRACTS_DIR / "telemetry-lexicon.yaml", encoding="utf-8"))
        temporal = lexicon["lexicon"]["temporal"]
        assert temporal["event_time_column"] == "event_timestamp"
        for table in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
            assert temporal["partition"][table] == (
                "history=year(session_started_at), month(session_started_at), day(session_started_at)"
            )

        identity = lexicon["lexicon"]["identity"]
        assert identity["normative_spec"] == "docs/contracts/telemetry-event-envelope.yaml"
        assert "hash" in identity["scheme"].lower()

        # Class D envelope still resolves (validate_telemetry_lexicon_tables reads it).
        meta = load_contract_meta(_CONTRACTS_DIR / "telemetry-lexicon.yaml")
        assert meta.evaluator.check == "validate_telemetry_lexicon_tables"


class TestStorageSubstrateDisposition:
    def test_storage_substrate_disposition(self) -> None:
        substrate = yaml.safe_load(open(_CONTRACTS_DIR / "storage-substrate.yaml", encoding="utf-8"))
        telemetry_tables = substrate["tables"]["telemetry_tables"]
        text = _norm(yaml.safe_dump(telemetry_tables))
        assert "year(session_started_at), month(session_started_at), day(session_started_at)" in text
        assert any(ref.endswith("telemetry-event-envelope.yaml") for ref in telemetry_tables["contract_ref"])
        assert "blob port" in text or "blob-port" in text


class TestDataModelingStandardNotes:
    def test_data_modeling_standard_notes(self) -> None:
        standard = yaml.safe_load(open(_CONTRACTS_DIR / "data-modeling-standard.yaml", encoding="utf-8"))
        rules_by_id = {r["id"]: r for r in standard["rules"]}

        identity_rule = rules_by_id["identity-ulid-at-boundary"]
        assert "Decision 199" in identity_rule["statement"]
        assert "telemetry" in identity_rule["statement"].lower()

        assert "identity-ulid-at-boundary's event-table carve-out, Decision 199" in standard["design_time_walk"]

        derived_rule = rules_by_id["derived-state"]
        assert "derivation.timing: read" in derived_rule["statement"]
        assert "T2.52" in derived_rule["statement"]


_CALENDAR_DAY_TRIPLE = "history=year(session_started_at), month(session_started_at), day(session_started_at)"
_BARE_DAY_RE = re.compile(r"(?<!month\(session_started_at\), )day\(session_started_at\)")
_SKIP_KEYS = ("amendment_log", "previous_versions")


def _collect_live_strings(node: object) -> list[str]:
    """Recursively collect every string leaf, skipping amendment_log/previous_versions at any depth."""
    if isinstance(node, dict):
        strings: list[str] = []
        for key, value in node.items():
            if key in _SKIP_KEYS:
                continue
            strings.extend(_collect_live_strings(value))
        return strings
    if isinstance(node, list):
        return [s for item in node for s in _collect_live_strings(item)]
    if isinstance(node, str):
        return [node]
    return []


def test_partition_by_is_calendar_day_triple() -> None:
    """rec-4065 acceptance node (module-level per the Decision 201 trailer census)."""
    for table in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
        doc = _load(table)
        assert doc.governance.partition_by == _CALENDAR_DAY_TRIPLE, table

    lexicon = yaml.safe_load(open(_CONTRACTS_DIR / "telemetry-lexicon.yaml", encoding="utf-8"))
    temporal = lexicon["lexicon"]["temporal"]
    for table in ("telemetry_sessions", "telemetry_observations", "telemetry_transcripts", "telemetry_agents"):
        assert temporal["partition"][table] == _CALENDAR_DAY_TRIPLE, table

    for path in sorted(_CONTRACTS_DIR.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            continue
        for raw in _collect_live_strings(data):
            text = _norm(raw)
            assert not _BARE_DAY_RE.search(text), f"{path.name}: bare day(session_started_at) in {raw!r}"
            assert "day() partition" not in text, f"{path.name}: 'day() partition' in {raw!r}"
            assert "day()-partition" not in text, f"{path.name}: 'day()-partition' in {raw!r}"


class TestObservationsPersonaBackend:
    def test_backend_and_billing_shape_dimensions(self) -> None:
        doc = _load("telemetry_observations")
        ip = yaml.safe_load((_CONTRACTS_DIR / "inference-provider.yaml").read_text(encoding="utf-8"))

        expected = {
            "persona_backend": set(ip["vocabulary"]["axes"]["persona_backend"]["values"]),
            "billing_shape": set(ip["tier_model"]["billing_shape_vocabulary"]),
        }
        assert expected == {
            "persona_backend": {"litellm", "claude_cli"},
            "billing_shape": {"metered_marginal", "fixed_non_rollover_allowance", "local"},
        }

        for name, values in expected.items():
            spec = doc.fields[name]
            assert spec.ref is None
            assert set(spec.dq_intent["accepted_values"]["values"]) == values

        assert doc.fields["billing_shape"].dq_intent["required_when"] == {
            "event_kind": ["point"],
            "observation_type": ["model_call"],
        }
        assert "required_when" not in doc.fields["persona_backend"].dq_intent

        assert "codex_cli" not in doc.fields["persona_backend"].dq_intent["accepted_values"]["values"]
        assert "cost_usd_reported" in doc.fields["billing_shape"].semantics

        cost_usd = doc.fields["cost_usd"]
        assert "billing_shape" in cost_usd.derivation["inputs"]
        assert "fixed_non_rollover_allowance" in cost_usd.derivation["formula"]
        assert "'local'" in cost_usd.derivation["formula"]
        assert "cost_usd_reported" in cost_usd.derivation["formula"]


class TestTokenClassesDisjoint:
    def test_tokens_input_is_uncached_and_rollup_covers_four_classes(self) -> None:
        obs = yaml.safe_load((_CONTRACTS_DIR / "telemetry_observations.yaml").read_text("utf-8"))["fields"]
        agents_doc = yaml.safe_load((_CONTRACTS_DIR / "telemetry_agents.yaml").read_text("utf-8"))
        agents = agents_doc["fields"]

        assert _norm(obs["tokens_input"]["description"]) == _norm(
            "Uncached input (prompt) tokens for this model_call: the prompt tokens the provider reports as neither a "
            "cache read nor a cache write, stored once on its single point row."
        )
        semantics = _norm(obs["tokens_input"]["semantics"])
        for sentence in (
            (
                "One of the four token classes stored on model_call (Decision 199, replacing the phase/step roll-up path). "
                "bigint."
            ),
            (
                "The four token classes are disjoint. tokens_input excludes tokens_cache_read and tokens_cache_creation, so a "
                "model_call's total prompt is tokens_input + tokens_cache_read + tokens_cache_creation, derived at read and "
                "never stored. An inclusive export such as OpenTelemetry's gen_ai.usage.input_tokens is that sum. Anthropic's "
                "input_tokens already means this; a producer for a provider that reports an inclusive prompt total subtracts "
                "the cached tokens it reports (rec-4028 owns the per-provider mapping)."
            ),
            (
                "Coverage (Decision 181 cl.2): the claude_code producer's one-to-one mapping is enforced by "
                "tests/turn_capture/test_observations.py; every other producer is an unenforced residual owned by rec-4028; "
                "the writer cannot decide disjointness from one row (Decision 210 cl.1)."
            ),
        ):
            assert _norm(sentence) in semantics
        assert _norm(
            "Prompt tokens served from the provider's prompt cache; disjoint from tokens_input and "
            "tokens_cache_creation (see tokens_input). A provider with no cache-read class records 0."
        ) in _norm(obs["tokens_cache_read"]["semantics"])
        assert _norm(
            "Prompt tokens written to the provider's prompt cache; disjoint from tokens_input and "
            "tokens_cache_read (see tokens_input). A provider with no cache-write class records 0."
        ) in _norm(obs["tokens_cache_creation"]["semantics"])

        assert _norm(
            "Uncached prompt tokens only (telemetry_observations.yaml tokens_input); disjoint from "
            "tokens_cache_read_total and tokens_cache_creation_total."
        ) in _norm(agents["tokens_input_total"]["semantics"])
        assert _norm(
            "tokens_input_total, tokens_output_total, tokens_cache_read_total and tokens_cache_creation_total"
        ) in _norm(agents_doc["governance_notes"])

        for fields, name in (
            (obs, "tokens_input"),
            (obs, "tokens_cache_read"),
            (obs, "tokens_cache_creation"),
            (agents, "tokens_input_total"),
        ):
            entries = [e for e in fields[name]["amendment_log"] if "PLAN-telemetry-cache-token-totals" in e["summary"]]
            assert len(entries) == 1, name
            assert entries[0]["semantic_break"] is True and entries[0]["change_class"] == "governance_note_add", name

        for name, column, sibling in (
            ("tokens_cache_read_total", "tokens_cache_read", "tokens_cache_creation_total"),
            ("tokens_cache_creation_total", "tokens_cache_creation", "tokens_cache_read_total"),
        ):
            spec = agents[name]
            derivation = spec["derivation"]
            assert derivation["timing"] == "read" and derivation["realized"] is False
            assert derivation["derived_by"] == "reader_verb:agent_run_token_rollup"
            assert f"telemetry_observations.{column}" in derivation["inputs"]
            assert f"sum({column})" in derivation["formula"] and "'model_call'" in derivation["formula"]
            assert "tokens_input_total" in spec["semantics"] and sibling in spec["semantics"]
            label = "cache-read" if column == "tokens_cache_read" else "cache-creation"
            assert _norm(spec["description"]) == _norm(
                f"Total {label} tokens across the agent run. DERIVED at read via the child session's own model_call "
                "observations, same mechanism as tokens_input_total."
            )
            assert _norm("so the run's total prompt is the sum of the three.") in _norm(spec["semantics"])
            entries = [e for e in spec["amendment_log"] if "PLAN-telemetry-cache-token-totals" in e["summary"]]
            assert len(entries) == 1
            assert entries[0]["change_class"] == "field_add" and entries[0]["semantic_break"] is False


def test_capture_provenance_and_usage_columns_are_nullable_and_ruled() -> None:
    pattern_sha = r"^([0-9a-f]{40}|[0-9a-f]{64})$"
    expected = {
        "telemetry_sessions": {
            "harness_session_key": ("string", {"pattern": r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"}),
            "checkout_sha": ("string", {"pattern": pattern_sha}),
            "checkout_base_sha": ("string", {"pattern": pattern_sha}),
            "checkout_dirty": ("boolean", {}),
        },
        "telemetry_observations": {
            "usage_final": ("boolean", {}),
            "usage_source": ("string", {"accepted_values": {"values": ["record", "spawn_result"]}}),
            "requested_model": ("string", {}),
            "reasoning_effort": ("string", {"pattern": r"^[a-z][a-z0-9_-]{0,31}$"}),
            "cache_creation_1h_tokens": ("bigint", {"at_most": "tokens_cache_creation"}),
            "cache_creation_5m_tokens": ("bigint", {"at_most": "tokens_cache_creation"}),
        },
    }
    for table, fields in expected.items():
        raw = yaml.safe_load((_CONTRACTS_DIR / f"{table}.yaml").read_text(encoding="utf-8"))
        assert raw["contract"]["contract_version"] == 2, table
        for name, (iceberg_type, rules) in fields.items():
            spec = raw["fields"][name]
            assert spec["nullable"] is True and spec["iceberg_type"] == iceberg_type, name
            assert spec["dq_intent"] == rules, name
            assert spec["populated_by"] and spec["description"] and spec["semantics"], name
        entries = [e for e in raw["amendment_log"] if "PLAN-telemetry-column-additions" in e["summary"]]
        assert len(entries) == 1 and entries[0]["semantic_break"] is False, table
        assert entries[0]["change_class"] == "field_add" and entries[0]["migration_story"], table


def test_duration_seconds_declares_truncation_toward_zero() -> None:
    for name in ("telemetry_sessions", "telemetry_agents"):
        field = _resolved(name)["duration_seconds"]
        assert "truncate toward zero to whole seconds" in _norm(field.derivation["formula"]), name
        assert "truncated toward zero to whole seconds" in _norm(field.semantics), name
