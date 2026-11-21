"""Markdown renderings of a friction Result: the public job summary and the session-only review sheet."""

from __future__ import annotations

from typing import Any

from scripts.feedback_loop import friction
from scripts.feedback_loop.friction import Result

NOT_PUBLISHED = "(not published)"
LOCAL_COMMAND = "bin/venv-python -m scripts.feedback_loop friction --json PATH --sheet PATH"


def _share(result: Result) -> str:
    if result.coverage == "unmeasurable" or result.unmapped_share is None:
        return "unmeasurable"
    return f"{result.unmapped_share:.2f}"


def _cell(value: Any) -> str:
    return "-" if value is None else str(value).replace("|", "\\|").replace("\n", " ")


def _row(cells: list[Any]) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def summary_markdown(result: Result) -> str:
    lines = [
        "## Friction detector",
        "",
        f"- window: {result.first}..{result.last}",
        f"- detector_version: {friction.DETECTOR_VERSION}, classifier_version: {result.classifier_version}",
        f"- reader calls: {result.calls}",
        f"- friction trees at this version: {result.friction_trees}",
        f"- process-event facts: {result.process_event_facts}, unmapped share: {_share(result)}",
        "",
    ]
    if not result.findings:
        lines += ["No findings in this window.", ""]
    else:
        head = [
            "state",
            "detector",
            "rule_id",
            "label",
            "class",
            "signature",
            "fact_count",
            "session_count",
            "tree_count",
            "rec",
        ]
        lines += [_row(head), _row(["---"] * len(head))]
        for f in result.findings:
            g = f.group or {}
            signature = g.get("signature")
            shown = signature if friction.is_published(signature) else (NOT_PUBLISHED if signature is not None else None)
            lines.append(
                _row(
                    [
                        f.state,
                        f.detector_id,
                        g.get("rule_id"),
                        g.get("label"),
                        g.get("class"),
                        shown,
                        g.get("fact_count"),
                        g.get("session_count"),
                        g.get("tree_count"),
                        f.rec_id,
                    ]
                )
            )
        lines.append("")
    lines += [
        f"Drafts and the review sheet come only from a session run: `{LOCAL_COMMAND}`.",
        "",
    ]
    return "\n".join(lines)


def _tree_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row.get("root_session_id"), row.get("harness_session_key"), row.get("session_started_at"))


def _order(value: Any) -> tuple[bool, str]:
    return (value is None, "" if value is None else str(value))


def sheet_markdown(result: Result, rows: list[dict[str, Any]] | None = None) -> str:
    rows = result.fact_rows if rows is None else rows
    numbers = {tuple(g[k] for k in friction.GROUP_KEYS): i for i, g in enumerate(result.groups, 1)}
    flagged = {f.subject_key for f in result.findings if f.group is not None}
    per_group: dict[int, dict[tuple[Any, ...], dict[str, Any]]] = {}
    per_tree: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        number = numbers[tuple(row.get(k) for k in friction.GROUP_KEYS)]
        key = _tree_key(row)
        entry = per_group.setdefault(number, {}).setdefault(key, {"facts": 0, "branch": row.get("branch")})
        entry["facts"] += row["fact_count"]
        tree = per_tree.setdefault(key, {"workflow": row.get("workflow"), "groups": {}})
        tree["groups"][number] = tree["groups"].get(number, 0) + row["fact_count"]
    refs = {key: f"T{i}" for i, key in enumerate(sorted(per_tree, key=lambda k: tuple(_order(v) for v in k)), 1)}
    lines = [
        f"# Friction review sheet, classifier_version {result.classifier_version}",
        "",
        f"- window: {result.first}..{result.last}",
        f"- friction trees: {result.friction_trees}, process-event facts: {result.process_event_facts}",
        f"- unmapped share: {_share(result)}",
        "",
    ]
    if any(f.detector_id == friction.COVERAGE for f in result.findings):
        lines += ["Coverage finding: draft verdict (file / covered by rec / reject): ", ""]
    head = [
        "group", "observation_type", "signature", "disposition", "rule_id", "label", "class", "generic", "fact_count",
        "session_count", "tree_count", "example_trees", "label_verdict", "draft_verdict",
    ]  # fmt: skip
    lines += [_row(head), _row(["---"] * len(head))]
    for number, g in enumerate(result.groups, 1):
        trees = per_group.get(number, {})
        top = sorted(trees.items(), key=lambda kv: (-kv[1]["facts"], _order(kv[0][2]), _order(kv[0][0])))[:2]
        examples = "; ".join(f"{k[0]} {k[2]} {v['branch']}" for k, v in top)
        subject = friction.subject_key(str(g["rule_id"]), str(g["signature"]))
        draft = "file / covered by rec / reject" if subject in flagged else ""
        lines.append(
            _row(
                [number, g["observation_type"], g["signature"], g["disposition"], g["rule_id"], g["label"], g["class"],
                 g["generic"], g["fact_count"], g["session_count"], g["tree_count"], examples,
                 "right / wrong / should be specific", draft]
            )
        )  # fmt: skip
    lines += ["", "## Repeat runs, per tree", ""]
    repeats = [(refs[k], n, t["groups"][n]) for k, t in per_tree.items() for n in t["groups"]
               if result.groups[n - 1]["rule_id"] == friction.REPEAT_RULE_ID]  # fmt: skip
    for ref, number, facts in sorted(repeats):
        lines.append(f"- {ref} group {number}: {facts} facts")
    lines += ["", "## Tree index", "", _row(["tree", "workflow", "root_session_id", "groups"]), _row(["---"] * 4)]
    for key, ref in sorted(refs.items(), key=lambda kv: int(kv[1][1:])):
        tree = per_tree[key]
        parts = ", ".join(f"{n}:{c}" for n, c in sorted(tree["groups"].items()))
        lines.append(_row([ref, tree["workflow"], key[0], parts]))
    lines.append("")
    return "\n".join(lines)
