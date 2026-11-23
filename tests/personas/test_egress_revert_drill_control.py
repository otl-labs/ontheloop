"""egress_revert_drill_control: main() arms the hook-less drill (arm faked, never run)."""

from __future__ import annotations

import pytest

from scripts.personas import egress_revert_drill
from scripts.personas import egress_revert_drill_control as C


def test_main_arms_the_hookless_drill(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[bool] = []
    monkeypatch.setattr(egress_revert_drill, "arm", lambda hooked: calls.append(hooked) or 7)
    assert C.main([]) == 7 and calls == [False]
