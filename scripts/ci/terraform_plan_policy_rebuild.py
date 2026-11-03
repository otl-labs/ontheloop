#!/usr/bin/env python3
"""Rebuild a deferred IAM policy document from the plan for the names-only digest (rec-4741, Decision 238).

When an aws_iam_policy_document is read at apply, the policy that uses it is unknown at plan time, yet the planned read's
own change.after carries every evaluated statement (configuration expressions drop dynamic blocks and literal list parts).
rebuild_policy re-implements the provider's merge from that block and returns the statements the policy will hold, or the
reason it cannot. A policy is linked to its document only when its .tf `policy` attribute is exactly the document's
`.json` (load_policy_links lexes the public source; an override file or a repeated address switches a link off), because
the plan's references cannot tell `data.x.json` from a transform of it.

Stdlib and terraform_hcl_lexer only; it must never import scripts.ci.terraform_plan_digest (review.sh runs the digest by
path as __main__, so a two-way import breaks). No function here prints, logs or raises with a plan value.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from scripts.ci.terraform_hcl_lexer import Token, lex
from scripts.terraform_apply_guard import iter_resource_changes

DOC_PREFIX = "data.aws_iam_policy_document."
MALFORMED = "malformed plan entry"
REASONS = (
    MALFORMED,
    "policy is not exactly the document's json",
    "several instances",
    "statement list unknown",
    "statement field unknown",
    "unmodelled argument",
    "sourced document not resolvable",
    "current policy unparsed",
)
_MAX_DEPTH = 3
_UNMODELLED = ("override_policy_documents", "source_json", "override_json")
_LIST_FIELDS = (
    ("actions", "Action"),
    ("not_actions", "NotAction"),
    ("resources", "Resource"),
    ("not_resources", "NotResource"),
)
_JSON_ATTRS = ("json", "minified_json")


def strip_instance_keys(address: str) -> str:
    """Replace every bracketed instance key (index, string, quoted-with-bracket) with [*]."""
    out: list[str] = []
    i = 0
    while i < len(address):
        if address[i] != "[":
            out.append(address[i])
            i += 1
            continue
        j = i + 1
        if j < len(address) and address[j] == '"':
            j += 1
            while j < len(address) and address[j] != '"':
                j += 2 if address[j] == "\\" else 1
            j += 1
            while j < len(address) and address[j] != "]":
                j += 1
        else:
            while j < len(address) and address[j] != "]":
                j += 1
        out.append("[*]")
        i = j + 1
    return "".join(out)


@dataclass(frozen=True)
class PolicyLinks:
    policies: dict[str, str]
    documents: dict[str, list[str]]


@dataclass(frozen=True)
class Rebuilt:
    document: str
    statements: list[dict]
    unknown_elements: int
    before: list[dict]


@dataclass(frozen=True)
class Incomplete:
    reason: str


class _Stop(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _is_override(name: str) -> bool:
    return name in ("override.tf", "override.tf.json") or name.endswith(("_override.tf", "_override.tf.json"))


_REFERENCE_KINDS = ["ident", "punct", "ident", "punct", "ident", "punct", "ident"]


def _doc_reference(tokens: list[Token]) -> Optional[str]:
    if [t.kind for t in tokens] != _REFERENCE_KINDS:
        return None
    texts = [t.text for t in tokens]
    if texts[:4] == ["data", ".", "aws_iam_policy_document", "."] and texts[5] == "." and texts[6] in _JSON_ATTRS:
        return texts[4]
    return None


def _source_list(tokens: list[Token], start: int) -> Optional[list[str]]:
    if tokens[start].text != "[":
        return None
    names: list[str] = []
    element: list[Token] = []
    for tok in tokens[start + 1 :]:
        if tok.kind == "nl":
            continue
        if tok.text in (",", "]"):
            if element:
                name = _doc_reference(element)
                if name is None:
                    return None
                names.append(name)
                element = []
            if tok.text == "]":
                return names
        else:
            element.append(tok)
    return None


def _blocks(tokens: list[Token]):
    """Yield (kind, labels, attributes) per top-level resource/data block; attributes map a depth-1 name to its value index."""
    i, outer = 0, 0
    while i < len(tokens):
        head = tokens[i]
        labels = tokens[i + 1 : i + 3]
        if outer == 0 and head.kind == "ident" and head.text in ("resource", "data") and len(labels) == 2:
            if all(t.kind == "str" for t in labels):
                j, depth, attrs, fresh = i + 3, 0, {}, True
                while j < len(tokens):
                    tok = tokens[j]
                    if tok.kind == "nl":
                        fresh = True
                    else:
                        if depth == 1 and fresh and tok.kind == "ident" and tokens[j + 1].text == "=":
                            attrs[tok.text] = j + 2
                        fresh = tok.text == "{" and tok.kind == "punct"
                        depth += (tok.text == "{" and tok.kind == "punct") - (tok.text == "}" and tok.kind == "punct")
                        if depth == 0:
                            break
                    j += 1
                yield head.text, [t.text for t in labels], attrs
                i = j
        elif head.kind == "punct" and head.text in "{}":
            outer += 1 if head.text == "{" else -1
        i += 1


def _file_links(text: str) -> tuple[dict[str, Optional[str]], dict[str, Optional[list[str]]]]:
    tokens = lex(text)
    policies: dict[str, Optional[str]] = {}
    documents: dict[str, Optional[list[str]]] = {}
    for kind, labels, attrs in _blocks(tokens):
        if kind == "resource" and "policy" in attrs:
            key = ".".join(labels)
            end = attrs["policy"]
            while end < len(tokens) and tokens[end].kind != "nl":
                end += 1
            policies[key] = None if key in policies else _doc_reference(tokens[attrs["policy"] : end])
        elif kind == "data" and labels[0] == "aws_iam_policy_document":
            listed = "source_policy_documents" in attrs
            sources = _source_list(tokens, attrs["source_policy_documents"]) if listed else []
            documents[labels[1]] = None if labels[1] in documents else sources
    return policies, documents


def load_policy_links(paths: list[str]) -> PolicyLinks:
    """Exact-link rule over the public .tf files; never raises, and returns no links when any override file is present."""
    files: list[Path] = []
    try:
        for entry in paths:
            path = Path(entry)
            members = sorted(path.glob("*")) if path.is_dir() else [path]
            if any(_is_override(m.name) for m in members):
                return PolicyLinks({}, {})
            files.extend(m for m in members if path.is_file() or m.suffix == ".tf")
    except OSError:
        return PolicyLinks({}, {})
    policies: dict[str, Optional[str]] = {}
    documents: dict[str, Optional[list[str]]] = {}
    for tf in files:
        try:
            found_policies, found_documents = _file_links(tf.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        for key, value in found_policies.items():
            policies[key] = None if key in policies else value
        for key, sources in found_documents.items():
            documents[key] = None if key in documents else sources
    return PolicyLinks({k: v for k, v in policies.items() if v}, {k: v for k, v in documents.items() if v is not None})


def _any_unknown(flag: Any) -> bool:
    if isinstance(flag, list):
        return any(_any_unknown(f) for f in flag)
    if isinstance(flag, dict):
        return any(_any_unknown(f) for f in flag.values())
    return flag is True


def _shaped(flags: Any, want: type) -> Any:
    """The unknown-flag container as the type the plan schema gives it; any other shape is not a plan we model."""
    if flags is None or flags is False:
        return want()
    if isinstance(flags, want):
        return flags
    raise _Stop(MALFORMED)


def _unseen_unknown(items: Any, flags: Any) -> bool:
    """True when an unknown flag sits past the end of the planned list (Terraform leaves such elements out of `after`)."""
    return isinstance(flags, list) and _any_unknown(flags[len(items) if isinstance(items, list) else 0 :])


def _plain_statements(raw: Any) -> list[dict]:
    doc = json.loads(raw) if isinstance(raw, str) else raw
    statements = doc["Statement"] if isinstance(doc, dict) else None
    if isinstance(statements, dict):
        statements = [statements]
    if not isinstance(statements, list) or not all(isinstance(s, dict) for s in statements):
        raise ValueError
    return statements


class _Doc:
    def __init__(self, plan: dict, links: PolicyLinks) -> None:
        self.links = links
        self.reads: dict[str, list[dict]] = {}
        for entry in iter_resource_changes(plan):
            address = strip_instance_keys(str(entry.get("address", "")))
            self.reads.setdefault(address, []).append(entry)
        self.unknown = 0
        self.counts: dict[str, int] = {}

    def read(self, name: str) -> dict:
        found = [
            e
            for e in self.reads.get(DOC_PREFIX + name, [])
            if e.get("type") == "aws_iam_policy_document"
            and (e.get("change") or {}).get("actions") == ["read"]
            and e.get("action_reason")
        ]
        if len(self.reads.get(DOC_PREFIX + name, [])) > 1:
            raise _Stop("several instances")
        if len(found) != 1:
            raise _Stop(MALFORMED)
        return found[0]["change"]

    def listing(self, name: str, index: int, field: str, values: Any, flags: Any) -> tuple[Optional[list], int]:
        if flags is True or _unseen_unknown(values, flags):
            raise _Stop("statement field unknown")
        flag_list = _shaped(flags, list)
        if values is None or values == []:
            return None, 0
        if not isinstance(values, list):
            raise _Stop(MALFORMED)
        out: list = []
        unknown = 0
        for pos, value in enumerate(values):
            flagged = pos < len(flag_list) and _any_unknown(flag_list[pos])
            if flagged:
                unknown += 1
                out.append(f"\x00unknown:{name}:{index}:{field}:{pos}")
            elif isinstance(value, str):
                out.append(value)
            else:
                raise _Stop(MALFORMED)
        return out, unknown

    def principals(self, name: str, index: int, field: str, blocks: Any, flags: Any) -> tuple[Any, int]:
        if flags is True or _unseen_unknown(blocks, flags):
            raise _Stop("statement field unknown")
        flags = _shaped(flags, list)
        if not blocks:
            return None, 0
        out: dict[str, list[str]] = {}
        unknown = 0
        for pos, block in enumerate(blocks):
            flag = (
                {"type": True}
                if pos < len(flags) and flags[pos] is True
                else _shaped(flags[pos] if pos < len(flags) else None, dict)
            )
            if flag.get("type") or not isinstance(block, dict) or not isinstance(block.get("type"), str):
                raise _Stop("statement field unknown" if flag.get("type") else MALFORMED)
            ids, n = self.listing(name, index, f"{field}{pos}", block.get("identifiers"), flag.get("identifiers"))
            unknown += n
            out.setdefault(block["type"], []).extend(ids or [])
        return ("*" if out == {"*": ["*"]} else out), unknown

    def condition(self, name: str, index: int, blocks: Any, flags: Any) -> tuple[Any, int]:
        if flags is True or _unseen_unknown(blocks, flags):
            raise _Stop("statement field unknown")
        flags = _shaped(flags, list)
        if not blocks:
            return None, 0
        out: dict[str, dict[str, list[str]]] = {}
        unknown = 0
        for pos, block in enumerate(blocks):
            flag = (
                {"test": True}
                if pos < len(flags) and flags[pos] is True
                else _shaped(flags[pos] if pos < len(flags) else None, dict)
            )
            if not isinstance(block, dict) or flag.get("test") or flag.get("variable"):
                raise _Stop("statement field unknown" if isinstance(block, dict) else MALFORMED)
            values, n = self.listing(name, index, f"condition{pos}", block.get("values"), flag.get("values"))
            unknown += n
            out.setdefault(str(block.get("test")), {}).setdefault(str(block.get("variable")), []).extend(values or [])
        return out, unknown

    def statement(self, name: str, index: int, block: Any, flags: Any) -> dict:
        if not isinstance(block, dict):
            raise _Stop(MALFORMED)
        if flags is True:
            raise _Stop("statement field unknown")
        flags = _shaped(flags, dict)
        modelled = {"sid", "effect", "principals", "not_principals", "condition", *(f for f, _ in _LIST_FIELDS)}
        if flags.get("sid") or flags.get("effect") or any(_any_unknown(v) for k, v in flags.items() if k not in modelled):
            raise _Stop("statement field unknown")
        out: dict[str, Any] = {}
        if any(block.get(key) is not None and not isinstance(block[key], str) for key in ("sid", "effect")):
            raise _Stop(MALFORMED)
        if block.get("sid"):
            out["Sid"] = block["sid"]
        out["Effect"] = block.get("effect") or "Allow"
        for field, label in _LIST_FIELDS:
            value, n = self.listing(name, index, field, block.get(field), flags.get(field))
            self.unknown += n
            if value is not None:
                out[label] = value
        for field, label in (("principals", "Principal"), ("not_principals", "NotPrincipal")):
            value, n = self.principals(name, index, field, block.get(field), flags.get(field))
            self.unknown += n
            if value is not None:
                out[label] = value
        value, n = self.condition(name, index, block.get("condition"), flags.get("condition"))
        self.unknown += n
        if value is not None:
            out["Condition"] = value
        return out

    def document(self, name: str, depth: int, seen: tuple[str, ...]) -> list[dict]:
        change = self.read(name)
        after, unknown = change.get("after"), change.get("after_unknown")
        if not isinstance(after, dict) or not isinstance(unknown, dict):
            raise _Stop(MALFORMED)
        if any(_any_unknown(unknown.get(k)) or after.get(k) not in (None, "", [], {}) for k in _UNMODELLED):
            raise _Stop("unmodelled argument")
        if unknown.get("statement") is True:
            raise _Stop("statement list unknown")
        known_keys = {"id", "json", "minified_json", "statement", "source_policy_documents", *_UNMODELLED}
        if any(_any_unknown(v) for k, v in unknown.items() if k not in known_keys):
            raise _Stop("unmodelled argument")
        blocks, flags = after.get("statement") or [], unknown.get("statement")
        if not isinstance(blocks, list):
            raise _Stop(MALFORMED)
        flags = _shaped(flags, list)
        if _unseen_unknown(blocks, flags):
            raise _Stop("statement field unknown")
        merged: list[dict] = []
        for source in self.sources(name, after, unknown, depth, seen):
            _merge(merged, source)
        outer, self.unknown = self.unknown, 0
        own = [self.statement(name, i, b, flags[i] if i < len(flags) else None) for i, b in enumerate(blocks)]
        self.counts[name], self.unknown = self.unknown, outer
        _merge(merged, own)
        return merged

    def sources(self, name: str, after: dict, unknown: dict, depth: int, seen: tuple[str, ...]) -> list[list[dict]]:
        elements, flags = after.get("source_policy_documents") or [], unknown.get("source_policy_documents")
        if flags is True:
            raise _Stop("unmodelled argument")
        if not isinstance(elements, list):
            raise _Stop(MALFORMED)
        if _unseen_unknown(elements, flags):
            raise _Stop("sourced document not resolvable")
        flags = _shaped(flags, list)
        out: list[list[dict]] = []
        for pos, element in enumerate(elements):
            if pos < len(flags) and _any_unknown(flags[pos]):
                names = self.links.documents.get(name) or []
                if len(names) != len(elements) or depth >= _MAX_DEPTH or names[pos] in seen + (name,):
                    raise _Stop("sourced document not resolvable")
                try:
                    out.append(self.document(names[pos], depth + 1, seen + (name,)))
                except _Stop:
                    raise _Stop("sourced document not resolvable") from None
            else:
                try:
                    out.append(_plain_statements(element))
                except (ValueError, KeyError, TypeError):
                    raise _Stop("sourced document not resolvable") from None
        return out


def _merge(base: list[dict], new: list[dict]) -> None:
    for stmt in new:
        sid = stmt.get("Sid")
        index = next((i for i, s in enumerate(base) if sid and s.get("Sid") == sid), None)
        if index is None:
            base.append(stmt)
        else:
            base[index] = stmt


def _refs_document(policy_refs: Any, links: PolicyLinks, address: str) -> str:
    wanted = links.policies.get(address)
    named = {
        ref[len(DOC_PREFIX) :].split(".")[0]
        for ref in policy_refs or []
        if isinstance(ref, str) and ref.startswith(DOC_PREFIX)
    }
    if wanted is None or named != {wanted}:
        raise _Stop("policy is not exactly the document's json")
    return wanted


def rebuild_policy(plan: dict, entry: dict, links: PolicyLinks, policy_refs: Any) -> Rebuilt | Incomplete:
    """The statements the policy will hold once its deferred document is read, or why they cannot be known now."""
    try:
        address = strip_instance_keys(str(entry.get("address", "")))
        name = _refs_document(policy_refs, links, address)
        if sum(1 for e in iter_resource_changes(plan) if strip_instance_keys(str(e.get("address", ""))) == address) > 1:
            raise _Stop("several instances")
        rebuilder = _Doc(plan, links)
        statements = rebuilder.document(name, 0, ())
        before = (entry.get("change") or {}).get("before")
        policy = before.get("policy") if isinstance(before, dict) else None
        try:
            current = [] if policy is None else _plain_statements(policy)
        except (ValueError, KeyError, TypeError):
            raise _Stop("current policy unparsed") from None
        return Rebuilt(name, canonical(statements), sum(rebuilder.counts.values()), canonical(current))
    except _Stop as stop:
        return Incomplete(stop.reason)
    except Exception:
        return Incomplete(MALFORMED)


def _sorted_unique(value: Any) -> Any:
    if isinstance(value, str):
        return [value]
    try:
        return sorted(set(value)) if isinstance(value, list) else value
    except TypeError:
        return value


def _principal(value: Any) -> Any:
    if value == "*":
        return {"*": ["*"]}
    return {k: _sorted_unique(v) for k, v in value.items()} if isinstance(value, dict) else value


def canonical(statements: list[dict]) -> list[dict]:
    """Statements with every list sorted and deduplicated and the provider's spellings made equal (rebuilt path only)."""
    out: list[dict] = []
    for stmt in statements:
        item = {k: v for k, v in stmt.items() if k not in ("Effect", "Principal", "NotPrincipal", "Condition")}
        item["Effect"] = stmt.get("Effect") or "Allow"
        for key in ("Action", "NotAction", "Resource", "NotResource"):
            if key in item:
                item[key] = _sorted_unique(item[key])
        for key in ("Principal", "NotPrincipal"):
            if stmt.get(key) is not None:
                item[key] = _principal(stmt[key])
        if stmt.get("Condition"):
            item["Condition"] = {t: {v: _sorted_unique(x) for v, x in m.items()} for t, m in stmt["Condition"].items()}
        out.append(item)
    return out
