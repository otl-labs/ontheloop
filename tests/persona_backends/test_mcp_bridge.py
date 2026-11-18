import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp.client.stdio import StdioServerParameters

from src.persona_backends.mcp_bridge import BridgeFailed, BridgeTimeout, BridgeUnavailable, open_bridge
from src.persona_backends.read_input import READ_CAP_BYTES
from tests.persona_backends.fake_mcp_server import MARKER

ROOT = Path(__file__).resolve().parents[2]


def server(mode: str = "ok", command: str | None = None, env: dict[str, str] | None = None) -> StdioServerParameters:
    return StdioServerParameters(
        command=command or sys.executable,
        args=["-m", "tests.persona_backends.fake_mcp_server", mode],
        cwd=ROOT,
        env=env,
    )


def run(mode: str, body, verbs=("fake_rows",), timeout: float = 60.0, tmp_path: Path | None = None, **kw):
    async def go():
        with (tmp_path / "err.txt" if tmp_path else Path(os.devnull)).open("w", encoding="utf-8") as errlog:
            async with open_bridge(server(mode, **kw), verbs, errlog=errlog, startup_timeout=timeout) as bridge:
                return await body(bridge)

    return asyncio.run(go())


async def _specs(bridge):
    return bridge.tool_specs()


def test_offers_only_named_verbs():
    specs = run("ok", _specs, verbs=("fake_by_ids", "fake_rows"))
    assert [s["function"]["name"] for s in specs] == ["fake_by_ids", "fake_rows"]
    assert specs[0]["type"] == "function"
    assert specs[0]["function"]["parameters"]["required"] == ["ids"]
    assert "fake_hidden" not in json.dumps(specs)


def test_call_returns_rows_text():
    async def body(bridge):
        return await bridge.call("fake_rows", json.dumps({"limit": 2}))

    text = run("ok", body)
    assert "synthetic row" in text and not text.startswith("error:")


def test_server_input_error_is_error_text():
    async def body(bridge):
        return await bridge.call("fake_rows", json.dumps({"limit": "x"}))

    assert run("ok", body).startswith("error: Input validation error")


def test_bad_json_arguments_make_no_call():
    async def body(bridge):
        return [await bridge.call("fake_rows", "{"), await bridge.call("fake_rows", "[]"), await bridge.call("fake_rows", "")]

    first, second, third = run("ok", body)
    assert first.startswith("error: arguments are not valid JSON")
    assert second == "error: arguments must be a JSON object"
    assert "synthetic row" in third


def test_reply_past_cap_is_truncated():
    async def body(bridge):
        return await bridge.call("fake_rows", "{}")

    text = run("big", body)
    head, _, tail = text.rpartition("\n")
    assert len(head.encode("utf-8")) <= READ_CAP_BYTES and tail.startswith("[truncated: ") and tail.endswith(" bytes]")


def test_missing_verb_is_unavailable():
    with pytest.raises(BridgeUnavailable, match="fake_missing"):
        run("ok", _specs, verbs=("fake_missing",))


def test_server_crash_at_start_is_unavailable():
    with pytest.raises(BridgeUnavailable):
        run("crash", _specs)


def test_spawn_failure_is_unavailable():
    with pytest.raises(BridgeUnavailable, match="FileNotFoundError"):
        run("ok", _specs, command="/nonexistent/command")


def test_startup_hang_times_out():
    with pytest.raises(BridgeTimeout):
        run("hang", _specs, timeout=1.5)


def test_unnamed_verb_call_makes_no_server_call():
    async def body(bridge):
        return await bridge.call("fake_by_ids", json.dumps({"ids": []}))

    assert run("die", body) == "error: unknown tool"


def test_server_stderr_goes_only_to_errlog(tmp_path, capfd, monkeypatch):
    ran = tmp_path / "ran"
    with pytest.raises(BridgeUnavailable):
        run("crash", _specs, tmp_path=tmp_path, env={**os.environ, "FAKE_MCP_RAN_FILE": str(ran)})
    captured = capfd.readouterr()
    assert ran.exists()
    assert MARKER in (tmp_path / "err.txt").read_text(encoding="utf-8")
    assert MARKER not in captured.out and MARKER not in captured.err


def test_server_dying_mid_run_is_bridge_failed():
    async def body(bridge):
        return await bridge.call("fake_rows", "{}")

    with pytest.raises(BridgeFailed, match="MCPError"):
        run("die", body)


def test_body_exception_leaves_unwrapped():
    sentinel = KeyError("body")

    async def body(bridge):
        raise sentinel

    with pytest.raises(KeyError) as info:
        run("ok", body)
    assert info.value is sentinel


def test_body_oserror_is_not_reported_as_spawn_failure():
    sentinel = OSError("body")

    async def body(bridge):
        raise sentinel

    with pytest.raises(OSError) as info:
        run("ok", body)
    assert info.value is sentinel


def test_startup_errors_leave_unwrapped():
    for case, expected in (
        (lambda: run("hang", _specs, timeout=1.5), BridgeTimeout),
        (lambda: run("crash", _specs), BridgeUnavailable),
    ):
        try:
            case()
        except BaseException as exc:  # noqa: BLE001 -- the exact type is the assertion
            assert type(exc) is expected
        else:
            raise AssertionError("no error raised")
