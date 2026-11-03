"""priority_queue-surface tests: degraded-mode cache fallback, terminal printing, DuckLake-reader
read path, priority-queue-source cache reporting, sync-collapse (single warm_sync call), Phase-B
verb-call dedup (zero reader verb calls), worker-thread error propagation (rec-2709 Wave 4).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.preflight import _common

boto3 = pytest.importorskip("boto3")

from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: E402


class TestReadPriorityQueueDegraded:
    """Tests for read_priority_queue() -- verb hard-fail and degraded-mode cache fallback."""

    def test_hard_fail_when_verb_fails_with_creds_ok(self) -> None:
        """A verb failure with credentials ok is an infrastructure fault -> SystemExit(1) (Decision 60)."""
        reader = MagicMock()
        reader.named.side_effect = RuntimeError("ducklake_reader 'named_read' failed (HTTP 500)")
        with patch("scripts.preflight._common._make_reader", return_value=reader):
            with pytest.raises(SystemExit):
                _preflight.read_priority_queue(creds_status="ok")

    def test_cache_fallback_returns_rows_when_creds_unavailable(self, tmp_path: Path) -> None:
        """creds_status != 'ok' -> rows from the local cache, the reader never queried."""
        cache = tmp_path / ".priority-queue.jsonl"
        cache.write_text(
            '{"rec_id": "rec-9", "rank": "1", "rationale": "cached", "north_star_impact": "high"}\n'
            "\n"  # blank lines tolerated
            '{"rec_id": "rec-8", "rank": "2", "rationale": "cached2", "north_star_impact": "low"}\n',
            encoding="utf-8",
        )
        with (
            patch.object(_common, "PRIORITY_QUEUE_FILE", cache),
            patch("scripts.preflight._common._make_reader") as mock_reader,
        ):
            result = _preflight.read_priority_queue(creds_status="unavailable")
        mock_reader.assert_not_called()
        assert [r["rec_id"] for r in result] == ["rec-9", "rec-8"]
        assert result[0]["rank"] == 1

    def test_empty_when_cache_absent_and_creds_unavailable(self, tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
        """Absent cache + creds down -> [] with a loud warning, never a crash."""
        missing = tmp_path / "does-not-exist.jsonl"
        with patch.object(_common, "PRIORITY_QUEUE_FILE", missing):
            result = _preflight.read_priority_queue(creds_status="unavailable")
        assert result == []
        assert "priority queue unavailable" in capsys.readouterr().err


class TestPrintPriorityQueue:
    """Tests for print_priority_queue() terminal output."""

    def test_empty_queue_prints_section_header(self) -> None:
        """Empty list prints the section header with (empty)."""
        printed: list[str] = []

        def capture(*args: object, **kwargs: object) -> None:
            printed.append(" ".join(str(a) for a in args))

        with patch("builtins.print", side_effect=capture):
            _preflight.print_priority_queue([])
        output = "\n".join(printed)
        assert "--- Priority Queue (top 5) ---" in output
        assert "(empty)" in output

    def test_entries_use_expected_format(self) -> None:
        """Entries are printed with #<rank> rec-NNN: [impact=...] -- <rationale>."""
        items = [
            {
                "rank": 1,
                "rec_id": "rec-100",
                "north_star_impact": "high",
                "rationale": "Top priority work",
            },
            {
                "rank": 2,
                "rec_id": "rec-200",
                "north_star_impact": "medium",
                "rationale": "Second item",
            },
        ]
        printed: list[str] = []

        def capture(*args: object, **kwargs: object) -> None:
            printed.append(" ".join(str(a) for a in args))

        with patch("builtins.print", side_effect=capture):
            _preflight.print_priority_queue(items)
        output = "\n".join(printed)
        assert "--- Priority Queue (top 5) ---" in output
        assert "#1 rec-100:" in output
        assert "[impact=high]" in output
        assert "-- Top priority work" in output
        assert "#2 rec-200:" in output


class TestReadPriorityQueueReader:
    """Tests for read_priority_queue() -- priority_queue_current named verb (Decision 84 I-3)."""

    _PQ_ROWS = [
        {"rec_id": "rec-20", "rank": 2, "rationale": "second", "north_star_impact": "low"},
        {"rec_id": "rec-10", "rank": 1, "rationale": "top", "north_star_impact": "high"},
    ]

    def test_reader_path_returns_shaped_sorted_rows(self) -> None:
        """Verb success -> rows shaped {rank, rec_id, rationale, north_star_impact} and rank-sorted."""
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = list(self._PQ_ROWS)

            result = _preflight.read_priority_queue()

        MockReader.assert_called_once_with(table="ops_priority_queue")
        MockReader.return_value.named.assert_called_once_with("priority_queue_current")
        assert len(result) == 2
        assert result[0]["rec_id"] == "rec-10"  # sorted by rank
        assert result[0]["rank"] == 1
        assert set(result[0].keys()) == {"rank", "rec_id", "rationale", "north_star_impact"}

    def test_reader_string_rank_cast_to_int(self) -> None:
        """String ranks from the reader are cast to int during shaping."""
        rows = [{"rec_id": "rec-99", "rank": "1", "rationale": "r", "north_star_impact": "medium"}]
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = rows

            result = _preflight.read_priority_queue()

        assert result[0]["rank"] == 1

    def test_reader_empty_returns_empty_list(self) -> None:
        """Verb returns [] -> function returns [] (empty queue, not an error)."""
        with patch("scripts.preflight._common._make_reader") as MockReader:
            MockReader.return_value.named.return_value = []

            result = _preflight.read_priority_queue()

        assert result == []


class TestPriorityQueueSourceCache:
    """priority_queue_source reports 'cache' when credentials are unavailable."""

    def test_report_source_cache_when_creds_down(self, tmp_path: Path) -> None:
        preflight_report = tmp_path / ".preflight-report.json"
        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("claude/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="unavailable"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.recs_cache._count_recommendations_reader", return_value=(0, 0, 0, [])),
            patch("scripts.preflight.priority_queue.read_priority_queue", return_value=[]),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 2",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

        data = json.loads(preflight_report.read_text(encoding="utf-8"))
        assert data["creds_status"] == "unavailable"
        assert data["priority_queue_source"] == "cache"


class TestSyncCollapse:
    """sync_ops.sync is called exactly once; the standalone _sync_ops_pull is not called in main()."""

    _FULL_MAIN_PATCHES: dict = {}  # class-level placeholder; built per test

    @staticmethod
    def _full_main_ctx(tmp_path: Path, extra: dict | None = None):
        """Return a list of patch context managers sufficient to run main() in isolation."""
        patches = [
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            # Phase B / pre-Phase-A subprocess users -- patch by name so main() never
            # shells out to real git (tests/CLAUDE.md isolation: no real subprocess in unit tests).
            patch("scripts.preflight.env_git._get_recent_main_commits", return_value=[]),
            patch("scripts.preflight.env_git.run_log_sync", return_value={"status": "skipped", "files": []}),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 1.5",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", tmp_path / ".preflight-report.json"),
            patch("builtins.print"),
        ]
        if extra:
            for tgt, kwargs in extra.items():
                patches.append(patch(tgt, **kwargs))
        return patches

    def test_sync_called_exactly_once(self, tmp_path: Path) -> None:
        """scripts.sync.ops.warm_sync is called once (creds ok); recommendation_sync comes from 'pulled'."""
        sync_call_count: list[int] = []

        def tracking_sync(profile: str = "agent_platform") -> dict:
            sync_call_count.append(1)
            return {
                "drained": {},
                "pulled": {"ops_recommendations": 10},
                "rows": {"ops_recommendations": [], "ops_decisions": [], "ops_priority_queue": []},
                "reader_ok": {"ops_recommendations": True, "ops_decisions": True, "ops_priority_queue": True},
            }

        from contextlib import ExitStack  # noqa: PLC0415

        with ExitStack() as stack:
            for p in self._full_main_ctx(tmp_path):
                stack.enter_context(p)
            stack.enter_context(patch("scripts.sync.ops.warm_sync", side_effect=tracking_sync))
            _preflight.main()

        assert len(sync_call_count) == 1, f"warm_sync called {len(sync_call_count)} times; expected exactly 1"
        report_path = tmp_path / ".preflight-report.json"
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["recommendation_sync"] == {"ops_recommendations": 10}

    def test_sync_ops_pull_not_called_in_main(self, tmp_path: Path) -> None:
        """_sync_ops_pull (= _rebuild_local_cache) is never called from main() after the sync collapse."""
        pull_calls: list[int] = []

        from contextlib import ExitStack  # noqa: PLC0415

        with ExitStack() as stack:
            for p in self._full_main_ctx(tmp_path):
                stack.enter_context(p)
            stack.enter_context(patch("session_preflight._sync_ops_pull", side_effect=lambda: pull_calls.append(1) or {}))
            _preflight.main()

        assert pull_calls == [], "_sync_ops_pull must not be called from main() after the sync collapse"


class TestVerbDedup:
    """Phase B issues ZERO reader verb calls -- every signal is served from the warm-sync rows (D4).

    Before neon-egress-reduction D4, main() de-duplicated the Phase-B reader fan-out down to one call
    per verb. D4 supersedes that: the warm-up sync (warm_sync) pulls the tables ONCE and Phase B
    derives every signal from those in-memory rows, so the per-verb count is now ZERO. This is the
    main()-level encoding of acceptance criterion 1 (zero additional Phase-B reader verb calls).
    """

    @staticmethod
    def _make_counting_reader(verb_calls: dict[str, int]) -> MagicMock:
        """Return a reader stub that counts named() calls per verb."""
        reader = MagicMock()

        def _named(verb: str, **kwargs: object) -> list:
            verb_calls[verb] = verb_calls.get(verb, 0) + 1
            return []

        reader.named.side_effect = _named
        return reader

    def _run_main_with_counting_reader(self, tmp_path: Path, verb_calls: dict[str, int]) -> None:
        counting_reader = self._make_counting_reader(verb_calls)
        preflight_report = tmp_path / ".preflight-report.json"
        # warm_sync returns NON-empty rows for all three tables so the derivations have real input;
        # the counting reader must STILL see zero verb calls in Phase B (everything served from rows).
        warm_sync_rows = {
            "drained": {},
            "pulled": {"ops_recommendations": 2, "ops_decisions": 1, "ops_priority_queue": 0},
            "rows": {
                "ops_recommendations": [
                    {
                        "id": "rec-001",
                        "status": "open",
                        "source": "manual",
                        "automatable": True,
                        "title": "t1",
                        "context": "c1",
                        "created_timestamp": "2026-06-10T00:00:00+00:00",
                    },
                    {
                        "id": "rec-002",
                        "status": "closed",
                        "source": "ci_rca",
                        "automatable": False,
                        "title": "t2",
                        "context": "c2",
                        "created_timestamp": "2026-06-11T00:00:00+00:00",
                    },
                ],
                "ops_decisions": [
                    {"id": "dec-001", "last_updated_timestamp": "2026-06-12T00:00:00+00:00"},
                ],
                "ops_priority_queue": [],
            },
            "reader_ok": {"ops_recommendations": True, "ops_decisions": True, "ops_priority_queue": True},
        }
        with (
            patch("scripts.preflight._common._make_reader", return_value=counting_reader),
            patch("scripts.sync.ops.warm_sync", return_value=warm_sync_rows),
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            # Patch subprocess users by name so the verb-count assertions are not perturbed
            # by real git calls (tests/CLAUDE.md isolation: no real subprocess in unit tests).
            patch("scripts.preflight.env_git._get_recent_main_commits", return_value=[]),
            patch("scripts.preflight.env_git.run_log_sync", return_value={"status": "skipped", "files": []}),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            # The creds-gated warm-up gauge is a reader call by design (one per run, not Phase B): stubbed here.
            patch(
                "scripts.preflight.telemetry_health.check_telemetry_health",
                return_value={"status": "pass", "catalog": "ducklake_smoke", "tables": {}},
            ),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            _preflight.main()

    def test_open_recs_not_called_in_phase_b(self, tmp_path: Path) -> None:
        """open_recs verb is NOT queried in Phase B -- the open count is derived from the warm-sync rows (D4)."""
        verb_calls: dict[str, int] = {}
        self._run_main_with_counting_reader(tmp_path, verb_calls)
        count = verb_calls.get("open_recs", 0)
        assert count == 0, f"open_recs called {count} times; expected 0 (served from the warm-sync rows, D4)"

    def test_decisions_max_updated_not_called_in_phase_b(self, tmp_path: Path) -> None:
        """decisions_max_updated verb is NOT queried in Phase B -- the timestamp is derived from rows (D4)."""
        verb_calls: dict[str, int] = {}
        self._run_main_with_counting_reader(tmp_path, verb_calls)
        count = verb_calls.get("decisions_max_updated", 0)
        assert count == 0, f"decisions_max_updated called {count} times; expected 0 (derived from rows, D4)"

    def test_no_reader_verb_calls_in_phase_b(self, tmp_path: Path) -> None:
        """The whole Phase-B fan-out issues ZERO reader verb calls (acceptance criterion 1)."""
        verb_calls: dict[str, int] = {}
        self._run_main_with_counting_reader(tmp_path, verb_calls)
        assert verb_calls == {}, f"Phase B issued reader verb calls {verb_calls}; expected none (served from rows, D4)"


class TestErrorPropagation:
    """Worker thread exceptions and SystemExit propagate to the main thread via future.result()."""

    def test_worker_sysexit_propagates(self, tmp_path: Path) -> None:
        """sys.exit(1) from read_priority_queue (verb failure, creds ok) re-raises in main thread."""
        preflight_report = tmp_path / ".preflight-report.json"
        with (
            patch("scripts.preflight.env_git.check_venv", return_value=True),
            patch("scripts.preflight.env_git.get_git_status", return_value=("agent/test", False, [])),
            patch("scripts.preflight.aws_infra.check_terraform_pending", return_value=False),
            patch("scripts.preflight.aws_infra.check_credentials", return_value="ok"),
            patch(
                "scripts.preflight.session_history.read_session_history",
                return_value={"status": "empty", "window_days": 2, "recent_sessions": [], "last_session": ""},
            ),
            patch("scripts.preflight.priority_queue.read_priority_queue", side_effect=SystemExit(1)),
            patch(
                "scripts.preflight.context_docs.read_context_files",
                return_value={
                    "roadmap_phase": "Phase 1.5",
                    "open_decisions_count": 0,
                    "strategic_review_due": False,
                    "recommendations_count": 0,
                },
            ),
            patch("scripts.preflight.ci_rca_signals._check_ci_rca_liveness", return_value=None),
            patch("session_preflight.PREFLIGHT_REPORT", preflight_report),
            patch("builtins.print"),
        ):
            with pytest.raises(SystemExit):
                _preflight.main()
