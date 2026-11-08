"""Pure shell walk for the protected-action gate -- no I/O, no git (rec-4518, docs/contracts/protected-actions.yaml).

Built on scripts/session/handoff_push_parse.py's lexer. walk() returns the simple commands of a Bash command text
(recursing into $(...), backticks, bash -c and eval strings), each with its command env, carried directory,
redirections and inline interpreter code, plus every piece of inline code found (a heredoc or here-string fed to an
interpreter, an interpreter's -c/-e/-E/-p/--eval string). A here-string or an arithmetic << is neutralised first so it
never hides a later line (rec-4772 owns the same defect in the handoff gate). Other heredoc bodies are data.

A lexer error raises ValueError; the caller decides (the gate denies a coarse command and allows the rest).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from scripts.session.handoff_push_parse import (
    _ASSIGNMENT_RE,
    _HEREDOC_OP_RE,
    _basename,
    _find_balanced_spans,
    _find_delimiter_line,
    _shell_c_and_eval_args,
    _split_simple_commands,
    _tokenize,
    strip_heredocs,
    strip_line_continuations,
)

WRAPPERS = frozenset(
    {"env", "command", "exec", "nohup", "nice", "time", "timeout", "sudo", "stdbuf", "ionice", "xargs", "watch"}
)
SHELL_NAMES = frozenset({"bash", "sh"})
WRITE_COMMANDS = frozenset({"tee", "sed", "cp", "mv", "rm", "truncate", "dd", "ln"})
TAIL_MARKERS = frozenset({"--", "-exec", "-execdir"})
_CODE_LETTERS = {"python": "c", "perl": "eE", "node": "ep"}
_INTERPRETER_LINE_RE = re.compile(r"(?:^|[\s/|&;(])(?:venv-python|perl|node|python[0-9.]*)(?=[\s]|$)")
_OP_CHARS = frozenset("<>&|")


@dataclass
class Simple:
    words: list[str]
    env: dict[str, str]
    cwd: str | None
    redirects: list[tuple[str, str]] = field(default_factory=list)
    code: list[str] = field(default_factory=list)
    carries_inline: bool = False

    @property
    def command(self) -> str:
        return _basename(self.words[0]) if self.words else ""


@dataclass
class Walk:
    commands: list[Simple] = field(default_factory=list)
    inline: list[str] = field(default_factory=list)
    heredoc: bool = False


def interpreter_kind(word: str) -> str | None:
    base = _basename(word)
    if base == "venv-python" or base.startswith("python"):
        return "python"
    return base if base in ("perl", "node") else None


def _skip_quoted(text: str, i: int, quote: str) -> int:
    j = i + 1
    while j < len(text) and text[j] != quote:
        j += 2 if quote == '"' and text[j] == "\\" else 1
    return j


def _next_word(text: str, i: int) -> tuple[str, int]:
    while i < len(text) and text[i] in " \t":
        i += 1
    if i < len(text) and text[i] in "'\"":
        end = _skip_quoted(text, i, text[i])
        return text[i + 1 : end], end + 1
    j = i
    while j < len(text) and not text[j].isspace():
        j += 1
    return text[i:j], j


def _arithmetic_end(text: str, i: int) -> int:
    depth = 0
    j = i
    while j < len(text):
        depth += text[j] == "("
        depth -= text[j] == ")"
        j += 1
        if depth == 0:
            return j
    return len(text)


def _neutralise(text: str) -> tuple[str, list[str], bool]:
    """Replace each unquoted <<< and each << inside $((...)) or ((...)); return (copy, here-string code, saw heredoc)."""
    out: list[str] = []
    code: list[str] = []
    saw = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "'":
            end = text.find("'", i + 1)
            end = n - 1 if end == -1 else end
            out.append(text[i : end + 1])
            i = end + 1
        elif text.startswith("$((", i) or (text.startswith("((", i) and (i == 0 or text[i - 1] in " \t\n;&|(")):
            start = i + 1 if c == "$" else i
            end = _arithmetic_end(text, start)
            out.append(text[i:start] + text[start:end].replace("<<", "<"))
            i = end
        elif text.startswith("<<<", i):
            word, after = _next_word(text, i + 3)
            line_start = text.rfind("\n", 0, i) + 1
            line_end = text.find("\n", i)
            line = text[line_start : n if line_end == -1 else line_end]
            if _INTERPRETER_LINE_RE.search(line):
                code.append(word)
            saw = True
            out.append("<")
            i += 3
        elif c == '"':
            end = _skip_quoted(text, i, '"')
            out.append(text[i : end + 1])
            i = end + 1
        else:
            saw = saw or text.startswith("<<", i)
            out.append(c)
            i += 1
    return "".join(out), code, saw


def _heredoc_inline(text: str) -> list[str]:
    """Bodies of heredocs whose receiving line has an interpreter word (outside quotes)."""
    bodies: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in "'\"":
            i = _skip_quoted(text, i, c) + 1
            continue
        m = _HEREDOC_OP_RE.match(text, i) if text.startswith("<<", i) else None
        if m is None:
            i += 1
            continue
        line_end = text.find("\n", m.end())
        line_end = n if line_end == -1 else line_end
        receiving = text[text.rfind("\n", 0, i) + 1 : i] + text[m.end() : line_end]
        body_start = min(line_end + 1, n)
        body_end, resume = _find_delimiter_line(text, body_start, m.group(3))
        if _INTERPRETER_LINE_RE.search(receiving):
            bodies.append(text[body_start:body_end])
        i = max(resume, m.end())
    return bodies


_BREAKS = frozenset("();\n")
_SEPARATOR_RUNS = frozenset({"&&", "||", "&", "|"})


def _normalise_tokens(tokens: list[str]) -> list[str]:
    """Split the lexer's merged punctuation tokens (such as "))" plus a newline) into separators and operators."""
    out: list[str] = []
    for tok in tokens:
        if not tok or not all(ch in _BREAKS or ch in _OP_CHARS for ch in tok):
            out.append(tok)
            continue
        run = ""
        for ch in tok:
            if ch in _BREAKS:
                out.extend([run] if run else [])
                out.append(ch)
                run = ""
            else:
                run += ch
        out.extend([run] if run else [])
    return out


def _split_redirects(tokens: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
    words: list[str] = []
    redirects: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok and all(ch in _OP_CHARS for ch in tok):
            target = tokens[i + 1] if i + 1 < len(tokens) else ""
            redirects.append((tok, target))
            i += 2
            continue
        if tok.isdigit() and i + 1 < len(tokens) and tokens[i + 1] and all(ch in _OP_CHARS for ch in tokens[i + 1]):
            i += 1
            continue
        words.append(tok)
        i += 1
    return words, redirects


def inline_code(words: list[str]) -> tuple[list[str], bool]:
    """Code strings carried by an interpreter's code flags anywhere in *words*, and whether any flag was seen."""
    found: list[str] = []
    flagged = False
    for idx, word in enumerate(words):
        kind = interpreter_kind(word)
        if kind is None:
            continue
        j = idx + 1
        while j < len(words) and words[j].startswith("-") and words[j] != "-":
            tok = words[j]
            if kind == "node" and (tok == "--eval" or tok.startswith("--eval=")):
                flagged = True
                value = tok.split("=", 1)[1] if "=" in tok else (words[j + 1] if j + 1 < len(words) else "")
                found.append(value)
            elif not tok.startswith("--"):
                for pos, letter in enumerate(tok[1:], start=1):
                    if letter in _CODE_LETTERS[kind]:
                        flagged = True
                        rest = tok[pos + 1 :]
                        found.append(rest or (words[j + 1] if j + 1 < len(words) else ""))
                        break
            j += 1
    return found, flagged


def is_entry(word: str, entry_paths: Iterable[str]) -> bool:
    return any(word.endswith(path) for path in entry_paths)


def _resolve(
    rest: list[str], env: dict[str, str], cwd: str | None, stops: tuple[str, ...]
) -> tuple[list[str], str | None] | None:
    if _basename(rest[0]) not in WRAPPERS:
        return rest, cwd
    in_env = _basename(rest[0]) == "env"
    i = 1
    while i < len(rest):
        word = rest[i]
        base = _basename(word)
        if _ASSIGNMENT_RE.match(word):
            key, _, value = word.partition("=")
            env[key] = value
        elif in_env and word == "-C" and i + 1 < len(rest):
            cwd = rest[i + 1]
            i += 1
        elif in_env and word.startswith("-C") and len(word) > 2:
            cwd = word[2:]
        elif in_env and word.startswith("--chdir="):
            cwd = word.split("=", 1)[1]
        elif base in SHELL_NAMES or base in ("terraform", "aws") or base in WRITE_COMMANDS or interpreter_kind(word):
            return rest[i:], cwd
        elif any(word.endswith(path) for path in stops):
            return rest[i:], cwd
        else:
            in_env = in_env or base == "env"
        i += 1
    return None


def _join(base: str | None, path: str) -> str:
    if base and not path.startswith(("/", "~", "$")):
        return posixpath.join(base, path)
    return path


def _simple(tokens: list[str], stops: tuple[str, ...], state: dict, out: Walk) -> None:
    words, redirects = _split_redirects(tokens)
    env = dict(state["env"])
    i = 0
    while i < len(words) and _ASSIGNMENT_RE.match(words[i]):
        key, _, value = words[i].partition("=")
        env[key] = value
        i += 1
    rest = words[i:]
    if not rest:
        state["env"].update(env)
        return
    if rest[0] == "export":
        for word in rest[1:]:
            if _ASSIGNMENT_RE.match(word):
                key, _, value = word.partition("=")
                state["env"][key] = value
        return
    if _basename(rest[0]) in ("cd", "pushd") and len(rest) > 1:
        state["cwd"] = _join(state["cwd"], rest[1])
        return
    resolved = _resolve(rest, env, state["cwd"], stops)
    if resolved is None:
        return
    command_words, cwd = resolved
    code, flagged = inline_code(command_words)
    simple = Simple(command_words, env, cwd, redirects, code, flagged)
    out.commands.append(simple)
    out.inline.extend(code)
    for idx, word in enumerate(command_words[1:], start=1):
        if word in TAIL_MARKERS:
            _simple(command_words[idx + 1 :], stops, {"env": dict(env), "cwd": cwd}, out)
            break


def _walk(text: str, stops: tuple[str, ...], out: Walk) -> None:
    text, here_code, saw = _neutralise(text)
    out.heredoc = out.heredoc or saw
    out.inline.extend(here_code)
    out.inline.extend(_heredoc_inline(text))
    stripped = strip_line_continuations(strip_heredocs(text))
    for _, _, inner in _find_balanced_spans(stripped):
        _walk(inner, stops, out)
    for arg in _shell_c_and_eval_args(stripped):
        _walk(arg, stops, out)
    elided = stripped
    for start, end, _ in reversed(_find_balanced_spans(stripped)):
        elided = elided[:start] + "SUBSTX" + elided[end:]
    state: dict = {"env": {}, "cwd": None}
    for tokens in _split_simple_commands(_normalise_tokens(_tokenize(elided))):
        _simple(tokens, stops, state, out)


def walk(text: str, entry_paths: Iterable[str] = ()) -> Walk:
    """Simple commands and inline code of *text*; raises ValueError on a lexer error."""
    out = Walk()
    _walk(text, tuple(entry_paths), out)
    return out
