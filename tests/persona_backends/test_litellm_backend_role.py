import dataclasses

from tests.persona_backends import fakes
from tests.persona_backends.fakes import ScriptedTransport as T
from tests.persona_backends.fakes import attempt, drive, error, final, tool

FB = "warm_fetched_escape_hatch"
READ = {"blob": "notes", "mode": "lines"}


def _turns(events):
    return [e for e in events if "turn" in e]


def test_fallback_role_later_turn_has_no_probe_turn():
    t = T([tool(READ, attempts=(attempt(FB),)), error("empty_output", (attempt(FB, "empty_output"),)), final()])
    _, events = drive(t, role=FB)
    assert [(x["probe_turn"], x["failed_over"], x["role"]) for x in _turns(events)] == [(0, False, FB)] * 3


def test_fallback_role_deadline_after_probe_counts_probe(monkeypatch):
    real_wait = fakes.asyncio.wait_for

    async def short(coro, _limit):
        return await real_wait(coro, 0.01)

    monkeypatch.setattr(fakes.asyncio, "wait_for", short)
    flight = dataclasses.replace(attempt(FB, "cancelled", 1), latency_ms=0)
    fill = fakes.Fill((), flight, None, fakes.record(FB))
    _, events = drive(fakes.CapturingTransport([fill]), role=FB, clock=fakes.FakeClock())
    turn = _turns(events)[0]
    assert (turn["probe_turn"], turn["failed_over"], turn["role"]) == (1, False, FB)
