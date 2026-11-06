"""The in-session warning and alarm for a tree whose turn capture keeps deferring (rec-4941, rules DW-1..DW-3).

Stdlib-only at import so the UserPromptSubmit shim can load it before any switch or credential work; every other import is
lazy inside the function that needs it. Alarm, never a gate (Decision 210 cl.2): nothing here changes a pass outcome, and
every public function swallows its own failure. Fixed tokens only (Decision 101): no content, ref or session id is printed
or filed.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATE_DIRNAME = "telemetry-capture"
DIAGNOSTICS = "diagnostics.jsonl"
WARNING_MARKER = "capture_warning.json"
ALARM_MARKER = "unpinned_alarm.json"
STOP_EVENTS = ("Stop", "StopFailure")
TRIGGER_PASSES = 2
REARM_EVERY = 25
ALARM_ATTEMPTS = 3
FILE = "scripts/telemetry_capture/capture_warning.py"
DEFERRED, HALTED = "deferred_no_pin", "halted"
DETAIL = "bin/venv-python -m scripts.telemetry_capture status --tree <this session's transcript>"
TEXTS = {
    DEFERRED: (
        "[telemetry_capture] WARNING: turn capture is not recording this session. The last {n} Stop passes ended "
        "deferred_no_pin (no capture pin found; Claude Code {version}), so nothing from this session reaches the telemetry "
        "warehouse. Tell the operator in your next status. Detail: " + DETAIL + "."
    ),
    HALTED: (
        "[telemetry_capture] WARNING: turn capture is halted for this session. The last {n} Stop passes ended halted, so "
        "nothing new from this session reaches the telemetry warehouse. Tell the operator in your next status. Detail: "
        + DETAIL
        + "; an operator clears it with clear-halt after fixing the cause."
    ),
}


def is_version(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value) is not None


def _dir(transcript_path: Path | str, session_id: str) -> Path:
    return Path(transcript_path).parent / session_id / STATE_DIRNAME


def _stop_passes(directory: Path) -> list[dict[str, Any]]:
    try:
        lines = (directory / DIAGNOSTICS).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            item = json.loads(line)
        except ValueError:
            continue
        if isinstance(item, dict) and item.get("event") in STOP_EVENTS and "outcome" in item:
            out.append(item)
    return out


def _reason_of(item: dict[str, Any]) -> str | None:
    diag = item.get("diag")
    if item.get("outcome") == "deferred" and isinstance(diag, dict) and diag.get("deferred_no_pin"):
        return DEFERRED
    return HALTED if item.get("outcome") == "halted" else None


def _persistent(passes: list[dict[str, Any]]) -> tuple[str | None, int]:
    if not passes:
        return None, 0
    reason = _reason_of(passes[-1])
    if reason is None:
        return None, 0
    streak = 0
    for item in reversed(passes):
        if _reason_of(item) != reason:
            break
        streak += 1
    return (reason, streak) if streak >= TRIGGER_PASSES else (None, streak)


def persistent(transcript_path: Path | str, session_id: str) -> tuple[str | None, int]:
    """Rule DW-1: (reason or None, streak) over the tree's trailing Stop and StopFailure passes."""
    try:
        return _persistent(_stop_passes(_dir(transcript_path, session_id)))
    except Exception:  # noqa: BLE001 -- read-only and advisory
        return None, 0


def _read_marker(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_marker(directory: Path, name: str, marker: dict[str, Any]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=f".{name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(marker, sort_keys=True) + "\n")
        os.replace(tmp, directory / name)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def alarm_marker(transcript_path: Path | str, session_id: str) -> dict[str, Any] | None:
    try:
        return _read_marker(_dir(transcript_path, session_id) / ALARM_MARKER)
    except Exception:  # noqa: BLE001
        return None


def _latest_version(passes: list[dict[str, Any]]) -> str:
    value = passes[-1].get("cli_version") if passes else None
    return value if is_version(value) else "unknown"


def prompt_warning(transcript_path: Path | str, session_id: str) -> str | None:
    """Rule DW-2: the JSON line to print on a UserPromptSubmit, once per episode, else None."""
    try:
        directory = _dir(transcript_path, session_id)
        passes = _stop_passes(directory)
        reason, streak = _persistent(passes)
        marker_path = directory / WARNING_MARKER
        if reason is None:
            marker_path.unlink(missing_ok=True)
            return None
        held = _read_marker(marker_path)
        held_streak = held.get("streak") if held else None
        fresh = (
            held is None
            or held.get("reason") != reason
            or not isinstance(held_streak, int)
            or streak - held_streak >= REARM_EVERY
        )
        if not fresh:
            return None
        try:
            _write_marker(directory, WARNING_MARKER, {"reason": reason, "streak": streak, "at": int(time.time())})
        except Exception:  # noqa: BLE001, S110 -- a failed write still warns: loud beats silent
            pass
        text = TEXTS[reason].format(n=streak, version=_latest_version(passes))
        return json.dumps(
            {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": text}, "systemMessage": text}
        )
    except Exception:  # noqa: BLE001
        return None


def cli_version(root: bytes) -> str | None:
    """The version field of the last root record that has one, if it is a d.d.d release; complete lines only."""
    complete = root[: root.rfind(b"\n") + 1]
    for raw in reversed(complete.splitlines()):
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict) and "version" in item:
            value = item["version"]
            return value if is_version(value) else None
    return None


def version_field(root: bytes) -> dict[str, str]:
    try:
        version = cli_version(root)
    except Exception:  # noqa: BLE001
        return {}
    return {"cli_version": version} if version is not None else {}


def file_unpinned_alarm(
    transcript_path: Path | str,
    session_id: str,
    *,
    portal: Any = None,
    reader: Any = None,
    now: datetime | None = None,
) -> str | None:
    """Rule DW-3: file or update the one unpinned_tree rec, once per tree, under the tree lock. Never raises."""
    lock = None
    try:
        directory = _dir(transcript_path, session_id)
        passes = _stop_passes(directory)
        reason, streak = _persistent(passes)
        if reason != DEFERRED:
            return None
        held = _read_marker(directory / ALARM_MARKER)
        attempts = 0
        if held is not None:
            action = held.get("action")
            attempts = held.get("attempts") if isinstance(held.get("attempts"), int) else 0
            if not (isinstance(action, str) and action.startswith("alarm_error:") and attempts < ALARM_ATTEMPTS):
                return None
        from scripts.telemetry_capture.state import CaptureState  # noqa: PLC0415

        lock = CaptureState(directory).acquire(0.0)
        if lock is None:
            return None
        from scripts.telemetry_capture import verify  # noqa: PLC0415
        from scripts.telemetry_verify import alarms  # noqa: PLC0415
        from src.turn_capture.record_turn import PARSER_VERSION  # noqa: PLC0415

        version = _latest_version(passes)
        stamp = now or datetime.now(timezone.utc)
        try:
            outcome = alarms.raise_alarms(
                {"unpinned_tree": streak},
                scope=f"claude_code={version}",
                alarm_at=None,
                now=stamp,
                portal=portal,
                file=FILE,
                acceptance_for=verify.acceptance_for,
                declared=(),
                parser_version=PARSER_VERSION,
                reader=reader,
            )
            token = outcome.actions.get("unpinned_tree", "unchanged")
            marker: dict[str, Any] = {"action": token, "version": version, "at": int(stamp.timestamp())}
        except Exception as exc:  # noqa: BLE001 -- counted in the marker, never silent
            token = f"alarm_error:{type(exc).__name__}"
            marker = {"action": token, "version": version, "at": int(stamp.timestamp()), "attempts": attempts + 1}
        _write_marker(directory, ALARM_MARKER, marker)
        return token
    except Exception:  # noqa: BLE001
        return None
    finally:
        if lock is not None:
            try:
                lock.release()
            except Exception:  # noqa: BLE001, S110
                pass


def unpinned(tree_path: Path | str, session_id: str) -> bool:
    """True when the tree's persistent reason is deferred_no_pin and no capture pin resolves."""
    try:
        if persistent(tree_path, session_id)[0] != DEFERRED:
            return False
        from src.turn_capture.transcript import FsTree, project_ref_pin  # noqa: PLC0415

        return project_ref_pin(FsTree(Path(tree_path))) is None
    except Exception:  # noqa: BLE001
        return False
