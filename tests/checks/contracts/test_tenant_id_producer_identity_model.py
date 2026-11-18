"""rec-4207's acceptance: the producer identity model is recorded where the identity contract reads it (Decision 230)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit

_CONTRACTS = Path(__file__).resolve().parents[3] / "docs" / "contracts"


def test_producer_identity_model_is_recorded() -> None:
    tenant = yaml.safe_load((_CONTRACTS / "tenant-id.yaml").read_text(encoding="utf-8"))
    note = " ".join(tenant["governance_notes"].split())
    for needle in (
        "rec-4207",
        "signed caller's account",
        "house tenant",
        "github_ci_branch",
        "PlatformDev",
        "never a direct invoke",
        "Decision 255",
        "agent-platform-session",
        "rec-4480",
        "rec-4420",
        "telemetry-writer-principals.yaml",
        "github_ci_persona_verdict",
    ):
        assert needle in note, needle
    assert any(entry["date"] == "2026-10-19" and entry["semantic_break"] is False for entry in tenant["amendment_log"])

    writer = yaml.safe_load((_CONTRACTS / "ducklake_telemetry_writer.yaml").read_text(encoding="utf-8"))
    assert writer["contract"]["governance"]["principal_classes"] == [
        "github_ci_branch",
        "PlatformDev",
        "agent-platform-session",
        "github_ci_persona_verdict",
    ]
    registry = yaml.safe_load((_CONTRACTS / "telemetry-writer-principals.yaml").read_text(encoding="utf-8"))
    assert list(registry["classes"]) == writer["contract"]["governance"]["principal_classes"]
