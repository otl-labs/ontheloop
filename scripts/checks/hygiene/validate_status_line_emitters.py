from __future__ import annotations

import ast
import re
from collections.abc import Mapping
from typing import Any, NamedTuple

import yaml

from scripts.checks import _common, registry

ROSTER_PATH = "config/telemetry/status_sources.yaml"
SCAN_ROOTS = ("scripts", "src", ".claude/hooks")
FORMATTER_HOMES = frozenset({"src/turn_capture/markers.py", "scripts/telemetry_capture/status_line.py"})
_EMITTER = "emit_status"
_FORMATTER = "format_" + "status_marker"
_RELEVANT_TOKENS = (_EMITTER, _FORMATTER, "STATUS" + " v")


class ParsedModule(NamedTuple):
    text: str
    tree: ast.Module


def _literal(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _is_none(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and node.value is None


def _call_args(call: ast.Call) -> tuple[ast.expr | None, ast.expr | None, ast.expr | None]:
    slots: list[ast.expr | None] = [None, None, None]
    for index, arg in enumerate(call.args[:3]):
        slots[index] = arg
    for keyword in call.keywords:
        if keyword.arg in ("source", "result", "reason"):
            slots[("source", "result", "reason").index(keyword.arg)] = keyword.value
    return slots[0], slots[1], slots[2]


def _callee(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _emit_calls(tree: ast.Module) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _callee(n) == _EMITTER]


def _row_errors(source: str, row: Any, modules: Mapping[str, ParsedModule]) -> list[str]:
    from src.turn_capture import gate_events, markers  # noqa: PLC0415

    signature = re.compile(markers.SIGNATURE_PATTERN)
    row = row if isinstance(row, Mapping) else {}
    module = row.get("module")
    errors: list[str] = []
    if not signature.fullmatch(source) or len(source) > markers.STATUS_SOURCE_MAX:
        errors.append(f"{ROSTER_PATH}: E1 source {source!r} does not fit the signature class and bound")
    results = row.get("results")
    if not isinstance(results, list) or not results or not set(results) <= set(markers.STATUS_RESULTS):
        errors.append(f"{ROSTER_PATH}: E2 source {source!r} results {results!r} are not a subset of {markers.STATUS_RESULTS}")
        results = []
    command = str(row.get("command", ""))
    if not any(token in command for token in gate_events.STATUS_COMMAND_TOKENS):
        errors.append(f"{ROSTER_PATH}: E5 source {source!r} command {command!r} is never scanned by the status parser")
    parsed = modules.get(str(module))
    if parsed is None:
        return [*errors, f"{ROSTER_PATH}: E3 source {source!r} module {module!r} does not exist"]
    seen: set[str] = set()
    for call in _emit_calls(parsed.tree):
        call_source, call_result, call_reason = (_literal(n) for n in _call_args(call))
        if call_source != source:
            continue
        if call_result is not None:
            seen.add(call_result)
        if call_result is not None and call_result not in results:
            errors.append(f"{module}:{call.lineno}: E3 source {source!r} emits undeclared result {call_result!r}")
        if call_reason is not None and not signature.fullmatch(call_reason):
            errors.append(
                f"{module}:{call.lineno}: E3 source {source!r} reason {call_reason!r} does not fit the signature class"
            )
    errors.extend(
        f"{module}: E3 source {source!r} declares result {result!r} but never emits it"
        for result in results
        if result not in seen
    )
    return errors


def _call_errors(path: str, parsed: ParsedModule, owner: Mapping[str, str]) -> list[str]:
    errors: list[str] = []
    for call in _emit_calls(parsed.tree):
        source_node, result_node, reason_node = _call_args(call)
        reason_ok = reason_node is None or _literal(reason_node) is not None or _is_none(reason_node)
        source = _literal(source_node)
        if source is None or _literal(result_node) is None or not reason_ok:
            errors.append(f"{path}:{call.lineno}: E4 {_EMITTER} needs literal source, result and reason arguments")
        elif source not in owner:
            errors.append(f"{path}:{call.lineno}: E4 source {source!r} is not in the roster")
        elif owner[source] != path:
            errors.append(f"{path}:{call.lineno}: E4 source {source!r} is assigned to {owner[source]} by the roster")
    return errors


def _formatter_errors(path: str, parsed: ParsedModule) -> list[str]:
    from src.turn_capture import markers  # noqa: PLC0415

    errors: list[str] = []
    if f"{markers.STATUS_MARKER_PREFIX} v{markers.STATUS_MARKER_VERSION}" in parsed.text:
        errors.append(f"{path}: E6 holds the status marker literal outside its two homes")
    for node in ast.walk(parsed.tree):
        if isinstance(node, ast.Call) and _callee(node) == _FORMATTER:
            errors.append(f"{path}:{node.lineno}: E6 calls the marker formatter outside its two homes")
    return errors


def emitter_errors(roster: Mapping[str, Any], modules: Mapping[str, ParsedModule]) -> list[str]:
    errors: list[str] = []
    owner = {str(source): str(row.get("module")) if isinstance(row, Mapping) else "" for source, row in roster.items()}
    for source, row in roster.items():
        errors.extend(_row_errors(str(source), row, modules))
    for path, parsed in modules.items():
        errors.extend(_call_errors(path, parsed, owner))
        if path not in FORMATTER_HOMES:
            errors.extend(_formatter_errors(path, parsed))
    return errors


def _load_modules(failed: list[str]) -> dict[str, ParsedModule]:
    modules: dict[str, ParsedModule] = {}
    for root in SCAN_ROOTS:
        for path in sorted((_common.ROOT / root).glob("**/*.py")):
            rel = path.relative_to(_common.ROOT).as_posix()
            text = path.read_text(encoding="utf-8", errors="replace")
            if not any(token in text for token in _RELEVANT_TOKENS):
                continue
            try:
                modules[rel] = ParsedModule(text, ast.parse(text))
            except SyntaxError as exc:
                print(f"  - {rel}: does not parse: {exc}")
                failed.append(f"Status line emitters: {rel} does not parse")
    return modules


@registry.register("validate_status_line_emitters", owner="platform")
def validate_status_line_emitters(failed: list[str]) -> None:
    """Check the status-line emitter roster against the code that emits (rules E1-E6)."""
    print("\n=== Status line emitters ===")
    roster_file = _common.ROOT / ROSTER_PATH
    try:
        loaded = yaml.safe_load(roster_file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"  - {ROSTER_PATH}: unreadable: {exc}")
        failed.append("Status line emitters: roster unreadable")
        return
    sources = loaded.get("sources") if isinstance(loaded, dict) else None
    if not isinstance(sources, dict) or not sources:
        print(f"  - {ROSTER_PATH}: not a source-keyed mapping")
        failed.append("Status line emitters: roster is not a source-keyed mapping")
        return
    modules = _load_modules(failed)
    errors = emitter_errors(sources, modules)
    registry.examined(len(sources), unit="status_sources")
    if errors:
        for error in errors:
            print(f"  - {error}")
        failed.append("Status line emitters")
    else:
        print("Every status line emitter matches the roster.")
