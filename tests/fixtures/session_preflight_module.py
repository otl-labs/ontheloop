"""Shared singleton load of scripts/session/preflight.py under the "session_preflight" module name.

Several tests/session/preflight/ concern-split test modules (post rec-2709 Wave 4 decomposition of
the former tests/test_session_preflight.py monolith) need `_preflight.main()` plus
`patch("session_preflight.<name>")` interception to keep working: session_preflight.py's own code
resolves module-global names (e.g. PREFLIGHT_REPORT, subprocess,
sys.executable) on ITS OWN module object, while `unittest.mock.patch` resolves the
"session_preflight.<name>" string via `sys.modules["session_preflight"]` at patch-application time.

Those two lookups only agree if every consumer shares the SAME loaded module object. If each test
file independently re-ran `importlib.util.spec_from_file_location("session_preflight", ...)`, the
last one imported during pytest collection would silently win the `sys.modules["session_preflight"]`
slot while every other file's own `_preflight.main()` call kept dispatching through its own
now-orphaned copy -- decoupling `patch("session_preflight.PREFLIGHT_REPORT")` (and similar) from the
main() it is meant to intercept (a vacuous-pass hazard: the patch would silently fail to take effect).

Unlike tests/fixtures/validate_module.py (Wave 1), this loader is explicitly IDEMPOTENT: it reuses
an existing sys.modules["session_preflight"] entry if one is already present. This guards the
intermediate-commit window (Wave 4 OPEN RISK 1) where the not-yet-deleted monolith
tests/test_session_preflight.py -- which still carries its own unconditional top-of-file loader --
can be collected alongside this package. Scoped collection (pytest tests/session/preflight) never
co-collects the monolith, so this only matters defensively; by the final commit the monolith is
deleted and the dual-loader window no longer exists in the merged state.

Python's own import cache (`sys.modules["tests.fixtures.session_preflight_module"]`) guarantees
this module's body executes exactly once per process; every consumer does
`from tests.fixtures.session_preflight_module import preflight as _preflight` and gets the
identical object.

MODULE_PATH is also re-exported (public, no leading underscore) so tests/session/preflight/
test_context_docs.py's TestRetiredStagingEstate -- which reads the source text of
scripts/session/preflight.py directly to assert a retired symbol is absent -- has a single
source of truth for the path instead of recomputing it locally.
"""

import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "session" / "preflight.py"

if "session_preflight" in sys.modules:
    preflight = sys.modules["session_preflight"]
else:
    _spec = importlib.util.spec_from_file_location("session_preflight", MODULE_PATH)
    assert _spec and _spec.loader
    preflight = importlib.util.module_from_spec(_spec)  # type: ignore[arg-type]
    sys.modules["session_preflight"] = preflight
    _spec.loader.exec_module(preflight)  # type: ignore[union-attr]
