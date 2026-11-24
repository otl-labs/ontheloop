"""Verb egress-revert-drill-control: the hook-less drill, the red path that makes the drill discriminating."""

from __future__ import annotations

from collections.abc import Sequence

from scripts.personas import egress_revert_drill


def main(argv: Sequence[str]) -> int:
    return egress_revert_drill.arm(hooked=False)
