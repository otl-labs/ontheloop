"""Package conftest for tests/session/preflight/ (rec-2709 Wave 4).

Hoists the module-level autouse fixture `_disable_reader_and_git_fetch` from the former
tests/test_session_preflight.py monolith VERBATIM. Layers UNDER the root tests/conftest.py
(global recursion/socket/env-clearing autouse fixtures) without redeclaring any of them.

Importing tests.fixtures.session_preflight_module here (noqa F401) guarantees
sys.modules["session_preflight"] is registered before this fixture's string-target patch()
calls resolve it -- every tests/session/preflight/test_*.py module does the same import, so
Python's own import cache makes this a no-op after the first collection, but declaring it here
too keeps the conftest self-sufficient regardless of collection order.
"""

from unittest.mock import MagicMock, patch

import pytest

from scripts.preflight.recs_signals_source import RecsSignalInputs
from tests.fixtures.platform_roadmap_state import live_state_dict
from tests.fixtures.session_preflight_module import preflight as _preflight  # noqa: F401


@pytest.fixture(autouse=True)
def _disable_reader_and_git_fetch(request: pytest.FixtureRequest):
    """Prevent all tests from hitting the real DuckLake reader and from doing real git fetches.

    Reader: every warehouse read in this module transits _make_reader().named(verb)
    (Decision 84 I-3). The default stub returns [] for every verb so read_priority_queue()
    does not sys.exit(1) and the rec counters report empty rather than reaching the network.
    Tests that need specific rows or failures re-patch scripts.preflight._common._make_reader. The telemetry
    gauge's event reader (_make_event_reader) gets the same stub.

    Git fetch: check_main_freshness() shells out to ``git fetch origin main``; patch it to
    a deterministic stub for every test except TestCheckMainFreshness (which exercises the
    real function via subprocess.run mocking).

    Plan walk: main() calls recs_cache._landed_followon_rec_ids() (an unbounded walk of the plan corpus)
    before the recs read; it is stubbed to {} for every test except TestAdHocLaneDerivations, which owns
    the real function and reaches it with a tmp plans_dir (rec-4273: unstubbed, it doubled every
    main()-level test and pushed the preflight.py coverage child past its cap).
    """
    from contextlib import ExitStack  # noqa: PLC0415

    reader_stub = MagicMock()
    reader_stub.named.return_value = []
    reader_stub.current_state.return_value = []

    freshness_stub = {
        "status": "ok",
        "fetched_at": "2026-05-24T00:00:00+00:00",
        "commits_behind": 0,
        "commits_ahead": 0,
        "main_files_changed_since_branch": [],
    }
    class_name = request.cls.__name__ if request.cls else ""
    module_name = request.module.__name__ if request.module else ""

    # warm_sync (small tables) and fetch_recs_signal_inputs (the two bounded recs reads) are main()'s only
    # reader touches; stub both so main() integration tests never hit the network. An ok-but-empty recs
    # read => main derives empty signals (0 open recs etc.).
    recs_inputs_stub = RecsSignalInputs(
        "ok",
        {"open_count": 0, "aging_count": 0, "non_automatable_count": 0},
        [],
        None,
        {"calls": 2, "bytes": {}},
    )
    warm_sync_stub = {
        "drained": {},
        "pulled": {},
        "rows": {"ops_decisions": [], "ops_priority_queue": []},
        "reader_ok": {"ops_decisions": True, "ops_priority_queue": True},
    }

    with ExitStack() as stack:
        stack.enter_context(patch("scripts.preflight._common._make_reader", return_value=reader_stub))
        # test_telemetry_health owns the event-reader seam: its main()-level tests patch it with their own reader.
        if not module_name.endswith("test_telemetry_health"):
            stack.enter_context(patch("scripts.preflight._common._make_event_reader", return_value=reader_stub))
        stack.enter_context(patch("scripts.sync.ops.sync", return_value={"drained": {}, "pulled": {}}))
        stack.enter_context(patch("scripts.sync.ops.warm_sync", return_value=warm_sync_stub))
        stack.enter_context(patch("session_preflight._sync_ops_pull", return_value={}))
        stack.enter_context(patch("session_preflight.fetch_recs_signal_inputs", return_value=recs_inputs_stub))
        stack.enter_context(patch("session_preflight.platform_roadmap.compute_state_dict", new=live_state_dict))
        if class_name != "TestAdHocLaneDerivations":
            stack.enter_context(patch("scripts.preflight.recs_cache._landed_followon_rec_ids", return_value={}))
        if class_name != "TestCheckMainFreshness":
            stack.enter_context(patch("scripts.preflight.env_git.check_main_freshness", return_value=freshness_stub))
        # dependabot.check_stranded_prs shells out to `gh pr list` on every main() run and,
        # unlike the other gh signals, carries NO creds guard to short-circuit it. Stub it for
        # every main()-integration module here; test_dependabot owns the real function and must
        # see it unpatched (same escape-hatch shape as TestCheckMainFreshness above).
        if not module_name.endswith("test_dependabot"):
            stack.enter_context(patch("scripts.preflight.dependabot.check_stranded_prs", return_value=None))
        yield
