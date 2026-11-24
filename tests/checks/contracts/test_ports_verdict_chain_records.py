"""Pins ports.yaml's SA-14 (verdict chain shell) and PC-12 (Decision 263 clause 4) records."""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[3]


def _ports() -> dict:
    return yaml.safe_load((_REPO / "docs/contracts/ports.yaml").read_text(encoding="utf-8"))


def test_sa14_records_the_verdict_chain_shell() -> None:
    rows = [r for r in _ports()["structural_assumptions"] if r["id"] == "SA-14"]
    assert len(rows) == 1
    row = rows[0]
    assert row["location"] == "scripts/personas/"
    assert row["owning_item"] == "T4.24:c9"
    tokens = ("persona-verdict.yml", "workflow_run", "head_sha", "Checks API", "github-actions[bot]", "docs/plans/")
    for token in (*tokens, "OIDC token-request", "AssumeRoleWithWebIdentity", "Credentials", "Error Code"):
        assert token in row["assumption"], token


def test_pc12_records_decision_263_clause_4() -> None:
    rows = [r for r in _ports()["coupling_findings"] if r["id"] == "PC-12"]
    assert len(rows) == 1
    row = rows[0]
    assert {"verdict_plane", "persona_compute_host", "secrets"} <= set(row["ports"])
    assert row["owning_item"] == "dec-263"
    assert (_REPO / row["location"]).is_file()
    assert "Decision 263 clause 4" in row["description"]
    assert "assumes no AWS identity" in row["description"]
    assert "key-adding proxy keeps the key out of the persona process" in row["description"]
    assert "scripts/personas/egress_launcher.py" in row["description"]


def test_sa14_names_app_installation_tokens() -> None:
    (row,) = [r for r in _ports()["structural_assumptions"] if r["id"] == "SA-14"]
    for token in ("installation token", "github_app_token", "docs/plans/PLAN-persona-verdict-app-post.yaml"):
        assert token in row["assumption"], token


def test_pc14_records_the_app_post_coupling() -> None:
    (row,) = [r for r in _ports()["coupling_findings"] if r["id"] == "PC-14"]
    assert {"verdict_plane", "secrets"} <= set(row["ports"])
    assert row["status"] == "accepted" and row["owning_item"] == "dec-273"
    assert (_REPO / row["location"]).is_file()


def test_sa11_and_sa13_keep_their_pinned_phrases_and_cite_their_plans() -> None:
    rows = {r["id"]: r for r in _ports()["structural_assumptions"]}
    assert "an adopting repository is guaranteed none" in rows["SA-11"]["assumption"]
    assert "docs/plans/approved/persona-contracts-verdict-slice.yaml" in rows["SA-11"]["assumption"]
    assert "T4.24 c16" in rows["SA-13"]["assumption"] and "fails loudly" in rows["SA-13"]["assumption"]
    assert "docs/plans/approved/inference-tier-selection.yaml" in rows["SA-13"]["assumption"]
    for plan in ("persona-contracts-verdict-slice", "persona-run-identity-contract", "inference-tier-selection"):
        assert (_REPO / f"docs/plans/approved/{plan}.yaml").is_file()


def test_sa15_names_the_container_floor_variant_assumptions() -> None:
    (row,) = [r for r in _ports()["structural_assumptions"] if r["id"] == "SA-15"]
    for token in (
        "egress_floor_container.py",
        "LiveRestore observed and not gated",
        "GHCR's blob host",
        "--pids-limit",
        "--dns",
    ):
        assert token in row["assumption"], token
