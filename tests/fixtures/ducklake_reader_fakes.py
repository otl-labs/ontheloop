"""Shared reader-handler fakes (tests/CLAUDE.md shared-helper home)."""

from __future__ import annotations


class FakeCon:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.closed = False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchall(self):  # liveness-probe support (D2 warm-connection cache)
        return []

    def close(self):
        self.closed = True


class CountCon:
    """A connection whose every statement answers one integer row (a dq_check violation count)."""

    def __init__(self, count: int = 0) -> None:
        self.count = count
        self.executed: list[tuple[str, object]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return (self.count,)

    def close(self) -> None:
        pass
