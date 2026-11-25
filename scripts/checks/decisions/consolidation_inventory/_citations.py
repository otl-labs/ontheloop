"""kinds anchored_citation and citation: prose and string-constant citations of a victim."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from scripts import decisions_md
from scripts.checks.decisions.consolidation_inventory._model import Evidence, Gate, Hit, make_hit, read_text

PROSE_GLOBS = ("docs/contracts/*.yaml", "AGENTS.md", "**/CLAUDE.md", ".claude/skills/**/*.md")
CODE_DIRS = ("scripts", "src")
DECISIONS_REL = "docs/DECISIONS.md"
_SKIP = frozenset({"__pycache__", ".venv", "node_modules", ".git"})
_LIST = re.compile(r"Decisions?[\s-]+(\d+(?:\s*(?:,|and|/|&|\+)\s*\d+)*)")
_DEC_ID = re.compile(r"dec-0*(\d+)")


def _anchored(victim: int) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w.#-]){victim}\s+(?:points?|pt\.?|cl\.|clauses?)\s*\d")


def _bare_positions(text: str, victim: int) -> list[int]:
    found: list[int] = []
    for match in _LIST.finditer(text):
        for number in re.finditer(r"\d+", match.group(1)):
            if int(number.group(0)) == victim:
                found.append(match.start(1) + number.start())
    for match in _DEC_ID.finditer(text):
        if int(match.group(1)) == victim:
            found.append(match.start(1))
    return found


def _line_of(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _prose_files(root: Path) -> list[Path]:
    seen: dict[str, Path] = {}
    for pattern in PROSE_GLOBS:
        for path in root.glob(pattern):
            if path.is_file() and not (set(path.relative_to(root).parts) & _SKIP):
                seen[path.relative_to(root).as_posix()] = path
    return [seen[k] for k in sorted(seen)]


def _decisions_outside(root: Path, victims: tuple[int, ...]) -> str | None:
    text = read_text(root, DECISIONS_REL)
    if text is None:
        return None
    # Blank the victims' own sections (keeping newlines so line numbers hold), so they never cite themselves.
    out = text
    for match, block in decisions_md.iter_decision_sections(text):
        if int(match.group(1)) in victims:
            out = out.replace(block, re.sub(r"[^\n]", " ", block), 1)
    return out


def _code_texts(root: Path) -> list[tuple[str, str]]:
    """(rel, text) with only string-constant text kept (comments dropped), line numbers preserved."""
    out: list[tuple[str, str]] = []
    for directory in CODE_DIRS:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            if set(path.relative_to(root).parts) & _SKIP:
                continue
            try:
                source = path.read_text(encoding="utf-8", errors="replace")
                if "ecision" not in source and "dec-" not in source:
                    continue
                tree = ast.parse(source)
            except (SyntaxError, ValueError, OSError):
                continue
            lines: dict[int, list[str]] = {}
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    for offset, line in enumerate(node.value.split("\n")):
                        lines.setdefault(node.lineno + offset, []).append(line)
            if lines:
                size = max(lines)
                out.append(
                    (path.relative_to(root).as_posix(), "\n".join(" ".join(lines.get(i, [])) for i in range(1, size + 1)))
                )
    return out


def scan(root: Path, victims: tuple[int, ...], gate: Gate, include_citations: bool) -> list[Hit]:
    sources: list[tuple[str, str]] = []
    for path in _prose_files(root):
        text = read_text(root, path.relative_to(root).as_posix())
        if text is not None:
            sources.append((path.relative_to(root).as_posix(), text))
    outside = _decisions_outside(root, victims)
    if outside is not None:
        sources.append((DECISIONS_REL, outside))
    sources.extend(_code_texts(root))
    hits: list[Hit] = []
    for rel, text in sources:
        for victim in victims:
            anchored_at: set[int] = set()
            for match in _anchored(victim).finditer(text):
                anchored_at.add(match.start())
                line = _line_of(text, match.start())
                hits.append(
                    make_hit(
                        "anchored_citation",
                        victim,
                        f"{rel}:{line}",
                        [Evidence("structured_key", match.group(0).strip())],
                        gate.is_gated(rel, None, line),
                    )
                )
            bare = [p for p in _bare_positions(text, victim) if p not in anchored_at]
            if not bare:
                continue
            if include_citations:
                for pos in bare:
                    line = _line_of(text, pos)
                    hits.append(
                        make_hit(
                            "citation",
                            victim,
                            f"{rel}:{line}",
                            [Evidence("structured_key", f"Decision {victim}")],
                            gate.is_gated(rel, None, line),
                        )
                    )
            else:
                hits.append(
                    make_hit(
                        "citation",
                        victim,
                        rel,
                        [Evidence("structured_key", f"{len(bare)} citations of Decision {victim}")],
                        gate.is_gated(rel),
                    )
                )
    return hits
