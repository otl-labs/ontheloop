"""VP step 4: rec-4365's regression guard and closure artifact (the pure-Python YAML parse-cost
budget for one preflight main() call), plus the shared yaml_parse_spy fixture's own positive
controls and a package-fixture wiring check.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

boto3 = pytest.importorskip("boto3")

from scripts import contract_rules  # noqa: E402
from scripts.preflight import recs_cache  # noqa: E402
from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402
from tests.fixtures.yaml_parse_spy import install as install_yaml_parse_spy  # noqa: E402

# 4.85 MB before this plan's fix, ~143 KB after (plan-time prototype). Any single pure-Python
# parse of the roadmap (747 KB) or of the contracts corpus (1.28 MB) exceeds this bound.
PURE_PYTHON_YAML_BUDGET = 512 * 1024

# Bound at import, before the package conftest's autouse fixture swaps compute_state_dict for the
# live_state_dict memo and stubs _landed_followon_rec_ids.
_REAL_COMPUTE_STATE_DICT = _preflight.platform_roadmap.compute_state_dict
_REAL_LANDED_FOLLOWON_REC_IDS = recs_cache._landed_followon_rec_ids
_REPO_ROOT = Path(__file__).resolve().parents[3]


def _run_main_under_seams(preflight_report: Path) -> None:
    """Run one preflight main() call through exactly the scope row's seam list -- this is the
    credentials-unavailable branch of main(), the ONLY branch this guard measures. Never patch
    _scan_provisional_contracts, _check_endstate_drift, decision_conditions.preflight_bucket or
    check_data_quality_coverage here: those are the measured YAML loaders."""
    with (
        patch("scripts.preflight.env_git.check_venv", return_value=True),
        patch("scripts.preflight.env_git.get_git_status", return_value=("claude/test", False, [])),
        patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
        patch("scripts.preflight.aws_infra.check_credentials", return_value="unavailable"),
        patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
        patch("scripts.preflight.ci_rca_signals._check_convergence_sensor_liveness", return_value=None),
        patch("session_preflight.platform_roadmap.compute_state_dict", return_value={}),
        patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
        patch("builtins.print"),
    ):
        _preflight.main()


class TestYamlParseBudget:
    def test_one_main_call_keeps_pure_python_yaml_under_budget(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Measures the credentials-unavailable branch of main() over the REAL repo tree (never a
        tmp fixture -- _common.ROOT is not patched here).

        compute_state_dict is stubbed to {} because the plan-corpus walkers are measured by
        TestPlanCorpusWalkerBudget below, not here. That stub also
        means _check_endstate_drift's frame needs docs/PROJECT_CONTEXT.md's live
        roadmap_tier_id_set stamp to reach either of its two parses at all (without it the
        function returns before parsing anything) -- a removed stamp would read as this guard's
        libyaml-frame assertion failing to find that frame, never as a loader regression.
        """
        assert yaml.__with_libyaml__
        preflight_report = tmp_path / ".preflight-report.json"
        log = install_yaml_parse_spy(monkeypatch)

        _run_main_under_seams(preflight_report)

        assert "contracts.py:load_all_contracts" in log.libyaml_frames
        assert "preflight/context_docs.py:_check_endstate_drift" in log.libyaml_frames

        unmeasurable = [e for e in log.pure_entries if e.size is None]
        assert not unmeasurable, f"unmeasurable pure-Python YAML parse(s) (never treated as 0): {unmeasurable}"

        total = sum(e.size for e in log.pure_entries)
        assert total <= PURE_PYTHON_YAML_BUDGET, (
            f"{total} bytes of pure-Python YAML parsing exceeds the {PURE_PYTHON_YAML_BUDGET}-byte budget -- "
            + "; ".join(f"{e.size}B at {e.stack[:3]}" for e in log.pure_entries)
        )


class TestPlanCorpusWalkerBudget:
    def test_plan_walkers_stay_under_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The REAL compute_state_dict and _landed_followon_rec_ids over the live docs/plans corpus."""
        assert yaml.__with_libyaml__
        log = install_yaml_parse_spy(monkeypatch)

        _REAL_COMPUTE_STATE_DICT(_REPO_ROOT / "docs" / "ROADMAP-PLATFORM.yaml")
        _REAL_LANDED_FOLLOWON_REC_IDS(_REPO_ROOT / "docs" / "plans")

        assert "platform_roadmap_state.py:compute_followon_state" in log.libyaml_frames
        assert "preflight/recs_cache.py:_landed_followon_rec_ids" in log.libyaml_frames
        unmeasurable = [e for e in log.pure_entries if e.size is None]
        assert not unmeasurable, f"unmeasurable pure-Python YAML parse(s) (never treated as 0): {unmeasurable}"
        assert log.pure_bytes <= PURE_PYTHON_YAML_BUDGET, (
            f"{log.pure_bytes} bytes of pure-Python YAML exceeds the {PURE_PYTHON_YAML_BUDGET}-byte budget -- "
            + "; ".join(f"{e.size}B at {e.stack[:3]}" for e in log.pure_entries[:5])
        )


class TestYamlParseSpy:
    """Positive controls: the spy records what it claims to, so the budget node above proves
    something rather than vacuously passing on a spy that recorded nothing."""

    def test_records_pure_and_libyaml_constructions(self, monkeypatch: pytest.MonkeyPatch) -> None:
        log = install_yaml_parse_spy(monkeypatch)
        text = "a: 1\n"

        yaml.safe_load(text)
        assert len(log.pure_entries) == 1
        assert log.pure_entries[0].size == len(text)

        contract_rules.parse_yaml(text)
        libyaml_entries = [e for e in log.entries if e.kind == "libyaml"]
        assert len(libyaml_entries) == 1
        assert any(frame.startswith("contract_rules.py:parse_yaml") for frame in libyaml_entries[0].stack)

    def test_guard_sees_a_disabled_libyaml_loader(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Mutation proof: with the libyaml loader disabled, the same seam list goes OVER budget
        and the overage is attributed to contracts.py:load_all_contracts -- proof the guard can
        actually fail, not just pass."""
        preflight_report = tmp_path / ".preflight-report.json"
        log = install_yaml_parse_spy(monkeypatch)
        monkeypatch.setattr(contract_rules, "_SAFE_LOADER", yaml.SafeLoader)

        _run_main_under_seams(preflight_report)

        total = sum(e.size or 0 for e in log.pure_entries)
        assert total > PURE_PYTHON_YAML_BUDGET
        assert any("contracts.py:load_all_contracts" in e.stack for e in log.pure_entries)


class TestPackageFixtureStubs:
    def test_compute_state_dict_is_the_live_memo(self) -> None:
        """Under the package autouse fixture (tests/session/preflight/conftest.py), main()'s own
        compute_state_dict is the live per-process memo (rec-3208's actual fix, rec-4006's
        conftest stub) -- not a bare {} default that would silently drop live roadmap state from
        every main()-level test that does not override it."""
        from tests.fixtures.platform_roadmap_state import live_state_dict

        assert _preflight.platform_roadmap.compute_state_dict is live_state_dict
