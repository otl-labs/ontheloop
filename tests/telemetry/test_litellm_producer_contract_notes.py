"""The litellm producer's contract words (PLAN-litellm-telemetry-producer): the loss key, the retry ordinal, the ports row and
the adapter status. Each row is found by content, never by position."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import yaml

CONTRACTS = Path(__file__).resolve().parents[2] / "docs" / "contracts"


def load(name: str) -> dict[str, Any]:
    return yaml.safe_load((CONTRACTS / name).read_text(encoding="utf-8"))


def squash(value: Any) -> str:
    return " ".join(str(value).split())


def walk(value: Any) -> Iterator[Any]:
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


def test_observations_metadata_declares_the_loss_key() -> None:
    metadata = load("telemetry_observations.yaml")["fields"]["metadata"]
    assert "loss (litellm, envelope LL-4: cancelled | usage_not_recorded)" in squash(metadata["semantics"])


def test_agents_host_run_ref_counts_run_persona_invocations() -> None:
    doc = load("telemetry_agents.yaml")
    assert "the run_persona invocation (litellm, LL-1); no job runs both" in squash(doc["fields"]["host_run_ref"]["semantics"])
    assert "'<session ref>/end#0/run_close'" in squash(doc["run_shapes"]["top_level"]["external_ref"])


def test_ports_records_the_litellm_adapter_and_pc13() -> None:
    doc = load("ports.yaml")
    adapters = [n for n in walk(doc["ports"]) if isinstance(n, dict) and n.get("id") == "litellm"]
    assert [(a["status"], a["location"]) for a in adapters] == [("implemented", "src/persona_backends/")]
    rows = [r for r in doc["coupling_findings"] if "github_actions_shell" in str(r) and "LL-1" in str(r)]
    assert len(rows) == 1 and set(rows[0]["ports"]) == {"persona_compute_host", "verdict_plane"}


def test_executor_personas_counts_provider_attempts() -> None:
    assert "one per provider attempt" in squash(load("executor-personas.yaml"))
