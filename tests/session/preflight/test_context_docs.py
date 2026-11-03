"""context_docs-surface tests: roadmap-state slimming, context-file reading (roadmap phase,
decisions, sessions, recs count), telemetry-health stub and endstate-drift detection
(rec-2709 Wave 4).
"""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.contract_rules import parse_yaml
from scripts.preflight import _common as preflight_common

boto3 = pytest.importorskip("boto3")

from tests.fixtures.session_preflight_module import MODULE_PATH as _MODULE_PATH  # noqa: E402
from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402
from tests.fixtures.yaml_parse_spy import install as install_yaml_parse_spy  # noqa: E402


class TestSlimRoadmapState:
    def test_keeps_only_actionable_subsets(self) -> None:
        full = {
            "next_eligible": [{"id": "T-1.6"}],
            "strategic_pending": [{"id": "T-2.1"}],
            "in_progress": [{"id": "T-1.5"}],
            "blocked": [{"id": "T-1.7"}],
            "active_tier": "T-1",
            "platform_tier_item_consumers": {"T-1.6": ["product-A"]},
        }
        slim = _preflight._slim_roadmap_state(full)
        assert slim == {
            "next_eligible": [{"id": "T-1.6"}],
            "strategic_pending": [{"id": "T-2.1"}],
        }

    def test_handles_missing_fields(self) -> None:
        slim = _preflight._slim_roadmap_state({})
        assert slim == {"next_eligible": [], "strategic_pending": []}

    def test_full_mode_includes_ratifiable_cds(self) -> None:
        full = {"ratifiable_cds": [{"id": "CD.6", "realization_evidence": "Realized."}]}
        slim = _preflight._slim_roadmap_state(full, full=True)
        assert slim["ratifiable_cds"] == [{"id": "CD.6", "realization_evidence": "Realized."}]

    def test_full_mode_defaults_ratifiable_cds_to_empty(self) -> None:
        slim = _preflight._slim_roadmap_state({}, full=True)
        assert slim["ratifiable_cds"] == []


class TestReadContextFiles:
    def test_roadmap_phase_extracted(self, tmp_path: Path) -> None:
        roadmap = tmp_path / "ROADMAP.md"
        roadmap.write_text("# Roadmap\n\n## Phase 1.5: Schema Flattening\n", encoding="utf-8")
        with (
            patch("scripts.preflight._common.ROADMAP_FILE", roadmap),
            patch("scripts.preflight._common.DECISIONS_FILE", tmp_path / "missing.md"),
        ):
            result = _preflight.read_context_files()
        assert result["roadmap_phase"] == "Phase 1.5: Schema Flattening"

    def test_roadmap_phase_defaults_unknown_when_missing(self, tmp_path: Path) -> None:
        with (
            patch("scripts.preflight._common.ROADMAP_FILE", tmp_path / "missing.md"),
            patch("scripts.preflight._common.DECISIONS_FILE", tmp_path / "missing2.md"),
        ):
            result = _preflight.read_context_files()
        assert result["roadmap_phase"] == "unknown"

    def test_open_decisions_counted(self, tmp_path: Path) -> None:
        decisions = tmp_path / "DECISIONS.md"
        decisions.write_text(
            "## Decision 1: Foo (Agent-decided -- pending review)\n## Decision 2: Bar (Decided)\n## Decision 3: Baz\n",
            encoding="utf-8",
        )
        with (
            patch("scripts.preflight._common.ROADMAP_FILE", tmp_path / "missing.md"),
            patch("scripts.preflight._common.DECISIONS_FILE", decisions),
        ):
            result = _preflight.read_context_files()
        # Decision 1 and 3 are open; Decision 2 is Decided
        assert result["open_decisions_count"] == 2

    def test_open_decisions_count_parity_with_pre_consolidation_regex_on_live_file(self) -> None:
        """DAF-03 (PLAN-daf-authoring-grammar): open_decisions_count now enumerates headers via
        decisions_md.iter_decision_headings() instead of a hand-rolled '^## Decision \\d+[^\\n]*'
        regex. Derive the expected count independently by re-running the PRE-CONSOLIDATION regex
        over the CURRENT live docs/DECISIONS.md file (never a hardcoded literal -- Decision 55 /
        test-count-coupling) and assert both enumerations agree under the same open/closed paren
        heuristic.
        """
        from scripts.preflight import _common as preflight_common

        content = preflight_common.DECISIONS_FILE.read_text(encoding="utf-8")
        old_headers = re.findall(r"^## Decision \d+[^\n]*", content, re.MULTILINE)
        expected_open = sum(
            1
            for header in old_headers
            if not re.search(r"\(Decided\)|\(Resolved\)|\(Closed\)|\(Done\)", header, re.IGNORECASE)
        )

        result = _preflight.read_context_files()
        assert result["open_decisions_count"] == expected_open

    def test_read_context_files_has_no_recent_sessions(self, tmp_path: Path) -> None:
        with (
            patch("scripts.preflight._common.ROADMAP_FILE", tmp_path / "missing.md"),
            patch("scripts.preflight._common.DECISIONS_FILE", tmp_path / "missing2.md"),
        ):
            result = _preflight.read_context_files()
        assert "recent_sessions" not in result

    def test_missing_files_return_defaults(self, tmp_path: Path) -> None:
        with (
            patch("scripts.preflight._common.ROADMAP_FILE", tmp_path / "missing.md"),
            patch("scripts.preflight._common.DECISIONS_FILE", tmp_path / "missing2.md"),
        ):
            result = _preflight.read_context_files()
        assert result["roadmap_phase"] == "unknown"
        assert result["open_decisions_count"] == 0
        assert result["recommendations_count"] is None


class TestReadContextFilesRecsCount:
    """read_context_files() takes the open count from the tally preflight fetched; no reader call (Decision 215)."""

    def _call(self, tmp_path: Path, reader: MagicMock, **kw):
        with (
            patch("scripts.preflight._common._make_reader", return_value=reader),
            patch("scripts.preflight._common.ROADMAP_FILE", tmp_path / "missing.md"),
            patch("scripts.preflight._common.DECISIONS_FILE", tmp_path / "missing2.md"),
        ):
            return _preflight.read_context_files(**kw)

    def test_recommendations_count_is_the_passed_tally_count(self, tmp_path: Path) -> None:
        reader = MagicMock()
        assert self._call(tmp_path, reader, open_recs_count=3)["recommendations_count"] == 3
        reader.named.assert_not_called()

    def test_recommendations_count_is_none_when_unavailable_and_no_fallback_call(self, tmp_path: Path) -> None:
        reader = MagicMock()
        assert self._call(tmp_path, reader)["recommendations_count"] is None
        reader.named.assert_not_called()


class TestRetiredStagingEstate:
    """Decision 84: the legacy staging drain is gone from preflight."""

    def test_main_no_longer_drains_pending(self) -> None:
        source = _MODULE_PATH.read_text(encoding="utf-8")
        assert "drain_pending" not in source, "preflight must not reference the retired staging drain"


class TestEndstateDrift:
    """Tests for _check_endstate_drift() -- VP step 6 (endstate drift cases)."""

    _OLD_IDS = ["T1.1", "T1.2"]
    _NEW_ID = "ZZ9.99"
    _STAMP_COMMIT = "abc1234"

    def _make_old_roadmap_yaml(self) -> str:
        return (
            "tier_items:\n"
            "  - id: T1.1\n"
            "    name: Item 1\n"
            "    status: not_started\n"
            "  - id: T1.2\n"
            "    name: Item 2\n"
            "    status: not_started\n"
        )

    def _make_new_roadmap_yaml(self) -> str:
        return (
            "tier_items:\n"
            "  - id: T1.1\n"
            "    name: Item 1\n"
            "    status: not_started\n"
            "  - id: T1.2\n"
            "    name: Item 2\n"
            "    status: not_started\n"
            "  - id: ZZ9.99\n"
            "    name: New Item\n"
            "    status: not_started\n"
        )

    def _make_completed_roadmap_yaml(self) -> str:
        return (
            "tier_items:\n"
            "  - id: T1.1\n"
            "    name: Item 1\n"
            "    status: complete\n"
            "  - id: T1.2\n"
            "    name: Item 2\n"
            "    status: not_started\n"
        )

    def _hash_of(self, ids: list[str]) -> str:
        return hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()

    def _make_context_md(self, stamped_hash: str, commit: str = "abc1234") -> str:
        return f"Derived from ROADMAP-PLATFORM.yaml @ {commit} (2026-06-28).\nroadmap_tier_id_set sha256: {stamped_hash}\n"

    def _setup_tmpdir(self, tmp_path: Path, context_text: str, roadmap_yaml: str) -> None:
        (tmp_path / "docs").mkdir(parents=True)
        (tmp_path / "docs" / "PROJECT_CONTEXT.md").write_text(context_text, encoding="utf-8")
        (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(roadmap_yaml, encoding="utf-8")

    def test_endstate_in_sync_not_stale(self, tmp_path: Path) -> None:
        """Identical ID set: stamped hash matches current roadmap -> not stale, no warning."""
        current_hash = self._hash_of(self._OLD_IDS)
        context = self._make_context_md(current_hash)
        roadmap = self._make_old_roadmap_yaml()
        self._setup_tmpdir(tmp_path, context, roadmap)

        with patch("scripts.preflight._common.ROOT", tmp_path):
            result = _preflight._check_endstate_drift()

        assert result["stale"] is False
        assert result["current_hash"] == current_hash
        assert result["new_ids"] == []

    def test_endstate_new_id_stale_names_new_id(self, tmp_path: Path) -> None:
        """New tier_item ID added to roadmap -> stale=True, new_ids contains the new ID."""
        old_hash = self._hash_of(self._OLD_IDS)
        context = self._make_context_md(old_hash, self._STAMP_COMMIT)
        roadmap = self._make_new_roadmap_yaml()
        self._setup_tmpdir(tmp_path, context, roadmap)

        git_result = MagicMock()
        git_result.returncode = 0
        git_result.stdout = self._make_old_roadmap_yaml()

        with (
            patch("scripts.preflight._common.ROOT", tmp_path),
            patch("session_preflight.subprocess.run", return_value=git_result),
        ):
            result = _preflight._check_endstate_drift()

        assert result["stale"] is True
        assert self._NEW_ID in result["new_ids"]
        assert result["current_hash"] != old_hash

    def test_endstate_completed_item_unchanged_ids_not_stale(self, tmp_path: Path) -> None:
        """Completing an item changes status but NOT the ID set -> hash unchanged -> not stale."""
        current_hash = self._hash_of(self._OLD_IDS)
        context = self._make_context_md(current_hash)
        roadmap = self._make_completed_roadmap_yaml()
        self._setup_tmpdir(tmp_path, context, roadmap)

        with patch("scripts.preflight._common.ROOT", tmp_path):
            result = _preflight._check_endstate_drift()

        assert result["stale"] is False
        assert result["new_ids"] == []


class TestEndstateDriftParser:
    """rec-4365: both _check_endstate_drift roadmap parses use the libyaml loader."""

    _OLD_IDS = ["T1.1", "T1.2"]

    def test_roadmap_parses_use_libyaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        old_hash = hashlib.sha256("\n".join(sorted(self._OLD_IDS)).encode()).hexdigest()
        context = f"Derived from ROADMAP-PLATFORM.yaml @ abc1234 (2026-06-28).\nroadmap_tier_id_set sha256: {old_hash}\n"
        old_roadmap_yaml = (
            "tier_items:\n  - id: T1.1\n    name: Item 1\n    status: not_started\n"
            "  - id: T1.2\n    name: Item 2\n    status: not_started\n"
        )
        new_roadmap_yaml = old_roadmap_yaml + "  - id: ZZ9.99\n    name: New Item\n    status: not_started\n"
        (tmp_path / "docs").mkdir(parents=True)
        (tmp_path / "docs" / "PROJECT_CONTEXT.md").write_text(context, encoding="utf-8")
        (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(new_roadmap_yaml, encoding="utf-8")

        git_result = MagicMock()
        git_result.returncode = 0
        git_result.stdout = old_roadmap_yaml

        log = install_yaml_parse_spy(monkeypatch)
        with (
            patch("scripts.preflight._common.ROOT", tmp_path),
            patch("session_preflight.subprocess.run", return_value=git_result),
        ):
            result = _preflight._check_endstate_drift()

        assert result["stale"] is True
        assert result["reason"] == "stamp_ref_new_ids_named"
        assert len(log.entries) == 2
        assert log.pure_entries == []


class TestEndstateStampRefResolves:
    """docs/PROJECT_CONTEXT.md's Source stamp ref half resolves through _check_endstate_drift's
    own matcher, so the drift check reaches its git-show attribution branch instead of reporting
    stale with an empty new_ids list, and that ref names a commit whose ROADMAP-PLATFORM.yaml
    tier_item id set hashes to the stamped fingerprint.
    """

    _PROBE_ROADMAP = "tier_items:\n  - id: PROBE.1\n    name: Probe item\n    status: not_started\n"

    def _repo_root(self) -> Path:
        return preflight_common.ROOT

    def _real_stamp_line(self) -> str:
        text = (self._repo_root() / "docs" / "PROJECT_CONTEXT.md").read_text(encoding="utf-8")
        stamped = [line for line in text.splitlines() if "roadmap_tier_id_set sha256:" in line]
        assert stamped, "docs/PROJECT_CONTEXT.md carries no roadmap_tier_id_set stamp line"
        return stamped[0]

    def _drive_drift(self, tmp_path: Path) -> tuple[dict[str, object], list[list[str]]]:
        """Run the REAL _check_endstate_drift over the REAL stamp line copied into tmp_path,
        beside a probe roadmap whose id set differs from the stamped one. Returns the drift
        result and every argv the recorder captured."""
        (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "PROJECT_CONTEXT.md").write_text(self._real_stamp_line() + "\n", encoding="utf-8")
        (tmp_path / "docs" / "ROADMAP-PLATFORM.yaml").write_text(self._PROBE_ROADMAP, encoding="utf-8")
        recorded: list[list[str]] = []

        def _record(cmd: list[str], **kwargs: object) -> MagicMock:
            recorded.append(list(cmd))
            return MagicMock(returncode=1, stdout="")

        with (
            patch("scripts.preflight._common.ROOT", tmp_path),
            patch("session_preflight.subprocess.run", side_effect=_record),
        ):
            result = _preflight._check_endstate_drift()
        return result, recorded

    def _recorded_show_ref(self, recorded: list[list[str]]) -> str:
        """The ref _check_endstate_drift resolved, lifted from the git show it only reaches once
        its own ref grammar matches. An empty recording means the matcher never matched."""
        shows = [cmd for cmd in recorded if cmd[:2] == ["git", "show"]]
        assert shows, "the Source stamp ref half never matched _check_endstate_drift's ref grammar"
        target = shows[0][2]
        assert target.endswith(":docs/ROADMAP-PLATFORM.yaml"), target
        return target.split(":", 1)[0]

    def test_stamp_ref_half_reaches_the_attribution_branch(self, tmp_path: Path) -> None:
        result, recorded = self._drive_drift(tmp_path)
        assert result["stale"] is True
        ref = self._recorded_show_ref(recorded)
        assert re.fullmatch(r"[0-9a-f]{7,40}", ref), ref

    def _assert_commit_present(self, root: str, ref: str) -> None:
        """rec-3668: a missing pinned commit is a hard failure naming its remedy, never a skip --
        session_start_deepen_history.sh is advisory and fail-open, so a failed unshallow must
        surface here rather than silently passing an unverified stamp."""
        present = subprocess.run(
            ["git", "cat-file", "-e", f"{ref}^{{commit}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=root,
        )
        assert present.returncode == 0, (
            f"stamped commit {ref} is absent from this checkout (shallow or partial clone) -- "
            "run `git fetch --unshallow origin main` before re-running"
        )

    def test_stamp_assertion_fails_rather_than_skips_when_commit_absent(self) -> None:
        absent_ref = "0" * 40
        with patch.object(subprocess, "run", return_value=MagicMock(returncode=1, stdout="", stderr="")):
            with pytest.raises(AssertionError, match="git fetch --unshallow origin main"):
                self._assert_commit_present(str(self._repo_root()), absent_ref)

    def test_stamp_ref_names_commit_hashing_to_the_stamped_fingerprint(self, tmp_path: Path) -> None:
        _result, recorded = self._drive_drift(tmp_path)
        ref = self._recorded_show_ref(recorded)
        root = str(self._repo_root())
        self._assert_commit_present(root, ref)
        show = subprocess.run(
            ["git", "show", f"{ref}:docs/ROADMAP-PLATFORM.yaml"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=root,
        )
        assert show.returncode == 0, show.stderr
        items = (parse_yaml(show.stdout) or {}).get("tier_items", [])
        old_ids = sorted({str(i["id"]) for i in items if isinstance(i, dict) and "id" in i})
        digest = hashlib.sha256("\n".join(old_ids).encode()).hexdigest()
        stamped = re.search(r"roadmap_tier_id_set sha256:\s*([a-f0-9]{64})", self._real_stamp_line())
        assert stamped, "the Source stamp line carries no sha256 half"
        assert digest == stamped.group(1)
