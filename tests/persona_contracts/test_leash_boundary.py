"""The leash's own enforcers are executor boundary paths: a rec targeting them is never automatable."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts.ops_portal import risk_scoring

LEASH_PATHS = [
    "scripts/persona_contracts/render.py",
    "scripts/checks/contracts/validate_persona_contracts.py",
    "scripts/checks/contracts/validate_persona_contract_binding.py",
    "tests/persona_contracts/test_render.py",
    "tests/checks/contracts/test_validate_persona_contract_binding.py",
]


@pytest.mark.parametrize("path", LEASH_PATHS)
def test_rec_targeting_leash_path_is_not_automatable(path: str) -> None:
    risk_scoring._capabilities_cache = None
    with patch.object(risk_scoring, "_compute_risk_score", return_value=0.0):
        assert risk_scoring.compute_automatable(path, "S") is False
    risk_scoring._capabilities_cache = None


def test_unrelated_path_stays_automatable_under_the_same_patch() -> None:
    risk_scoring._capabilities_cache = None
    with patch.object(risk_scoring, "_compute_risk_score", return_value=0.0):
        assert risk_scoring.compute_automatable("scripts/unrelated_helper.py", "S") is True
    risk_scoring._capabilities_cache = None
