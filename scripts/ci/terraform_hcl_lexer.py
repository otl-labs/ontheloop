#!/usr/bin/env python3
"""Purpose-built HCL2 lexer for the trusted plan's static screen (Decision 238 cl.3; never evaluates anything).

Tokenises only what the screen needs: identifiers, numbers, punctuation, double-quoted templates (with one level of
${ } interpolation whose content is lexed as expression tokens) and newlines. Strings are consumed before comments,
so a "/*" or "//" inside a string stays a string. Constructs the screen refuses outright (heredoc, block comment,
template directive, nested interpolation, non-ASCII bytes, unterminated string, unbalanced braces) raise LexRefusal
with a rule id and a line number. Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class LexRefusal(Exception):
    def __init__(self, rule: str, line: int) -> None:
        super().__init__(rule)
        self.rule = rule
        self.line = line


@dataclass
class Token:
    kind: str  # ident, number, str, punct, nl
    text: str
    line: int
    subs: list[list[Token]] = field(default_factory=list)


_PUNCT3 = ("...",)
_PUNCT2 = ("==", "!=", "<=", ">=", "&&", "||", "=>", "::")
_PUNCT1 = set("{}[]()=,.:?!<>+-*/%&|")
_IDENT_START = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_")
_IDENT_CHARS = _IDENT_START | set("0123456789-")


def _check_bytes(text: str) -> None:
    line = 1
    for ch in text:
        if ch == "\n":
            line += 1
        elif ch == "\t" or " " <= ch <= "~":
            continue
        else:
            raise LexRefusal("non_ascii_or_control", line)


class _Lexer:
    def __init__(self, text: str) -> None:
        self.t = text
        self.i = 0
        self.line = 1

    def tokens(self, in_interp: bool = False, nested: bool = False) -> list[Token]:
        out: list[Token] = []
        depth = 0
        t = self.t
        while self.i < len(t):
            ch = t[self.i]
            if ch == "\n":
                out.append(Token("nl", "\n", self.line))
                self.line += 1
                self.i += 1
            elif ch in " \t":
                self.i += 1
            elif ch == "#" or t.startswith("//", self.i):
                while self.i < len(t) and t[self.i] != "\n":
                    self.i += 1
            elif t.startswith("/*", self.i):
                raise LexRefusal("block_comment", self.line)
            elif t.startswith("<<", self.i):
                raise LexRefusal("heredoc", self.line)
            elif ch == '"':
                out.append(self._string(nested=nested or in_interp))
            elif ch in _IDENT_START:
                out.append(self._word("ident", lambda c: c in _IDENT_CHARS))
            elif ch.isdigit():
                out.append(self._word("number", lambda c: c.isdigit() or c == "."))
            else:
                if in_interp and ch == "}" and depth == 0:
                    return out
                tok = self._punct()
                depth += (tok.text == "{") - (tok.text == "}")
                out.append(tok)
        if in_interp:
            raise LexRefusal("unterminated_interpolation", self.line)
        return out

    def _word(self, kind: str, belongs) -> Token:
        j = self.i
        while j < len(self.t) and belongs(self.t[j]):
            j += 1
        tok = Token(kind, self.t[self.i : j], self.line)
        self.i = j
        return tok

    def _punct(self) -> Token:
        ch = self.t[self.i]
        text = next((p for p in _PUNCT3 + _PUNCT2 if self.t.startswith(p, self.i)), ch)
        if text == ch and ch not in _PUNCT1:
            raise LexRefusal("unclassified_character", self.line)
        self.i += len(text)
        return Token("punct", text, self.line)

    def _string(self, nested: bool) -> Token:
        t = self.t
        start_line = self.line
        self.i += 1
        begin = self.i
        subs: list[list[Token]] = []
        while True:
            if self.i >= len(t):
                raise LexRefusal("unterminated_string", start_line)
            ch = t[self.i]
            if ch == "\n":
                raise LexRefusal("unterminated_string", start_line)
            if ch == "\\":
                self.i += 2
            elif ch == '"':
                break
            elif t.startswith("$${", self.i):
                self.i += 3
            elif t.startswith("%{", self.i):
                raise LexRefusal("template_directive", self.line)
            elif t.startswith("${", self.i):
                if nested:
                    raise LexRefusal("nested_interpolation", self.line)
                self.i += 2
                subs.append(self.tokens(in_interp=True))
                self.i += 1
            else:
                self.i += 1
        raw = t[begin : self.i]
        self.i += 1
        return Token("str", raw, start_line, subs)


def lex(text: str) -> list[Token]:
    """Tokenise one Terraform file; raises LexRefusal on any construct the screen refuses at lexing time."""
    _check_bytes(text)
    lexer = _Lexer(text)
    tokens = lexer.tokens()
    depth = 0
    for tok in tokens:
        if tok.kind == "punct" and tok.text in "{}":
            depth += 1 if tok.text == "{" else -1
            if depth < 0:
                raise LexRefusal("unbalanced_braces", tok.line)
    if depth != 0:
        raise LexRefusal("unbalanced_braces", lexer.line)
    return tokens
