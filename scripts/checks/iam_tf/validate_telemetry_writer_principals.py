"""Every identity that may invoke the production telemetry writer is declared (rec-4420, rec-5177; T3.22 c4).

STATIC-TEXT STANDING CHECK and the evaluator of docs/contracts/telemetry-writer-principals.yaml (named literally here).
It reads Terraform text only (never state or live IAM) in terraform/personal and terraform/bootstrap and fails closed: an
unreadable policy source or statement is a finding, never a pass. Owned elsewhere: resource-based grants (an
aws_lambda_permission on the writer, a Function URL with authorization_type NONE) by Decisions 213 cl.1 and 255 cl.2.
"""

from __future__ import annotations

import fnmatch
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scripts.checks import _common, registry
from scripts.checks.iam_tf import validate_invoke_implies_resolve as _vir
from scripts.checks.iam_tf import validate_secret_parameter_grants as _spg
from scripts.contracts import load_contract

REGISTRY_REL = "docs/contracts/telemetry-writer-principals.yaml"
CONTRACT_REL = "docs/contracts/ducklake_telemetry_writer.yaml"
WRITER = "agent-platform-ducklake-telemetry-writer"
_W_LAMBDA = f"arn:aws:lambda:r:a:function:{WRITER}"
_W_API = "arn:aws:execute-api:r:a:id/stage/POST/telemetry/writer"
_ROOTS = ("terraform/personal", "terraform/bootstrap")
_INVOKE = ("lambda:invokefunctionurl", "lambda:invokefunction", "lambda:invokeasync")
_ALLOWED_URL_ACTIONS = {"lambda:invokefunctionurl", "lambda:getfunctionurlconfig", "lambda:invokefunction"}
_BOUNDARY = ("aws_iam_policy.github_ci_apply_boundary", "agent-platform-github-ci-apply-boundary")
_EXCLUSION_ATTACH = {"PlatformAdmin": {"aws_iam_role.platform_admin"}, "ci_apply_boundary": set()}
_POLICY_ATTRS = tuple(
    "policy inline_policy source_json override_json source_policy_documents override_policy_documents".split()
)
_POLICY_TYPES = ("aws_iam_policy", "aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy", "aws_iam_role")
_ATTACH_POLICY_TYPES = ("aws_iam_role_policy", "aws_iam_user_policy", "aws_iam_group_policy")
_ATTACH_ARN_TYPES = tuple(f"aws_iam_{k}_policy_attachment" for k in ("role", "user", "group")) + ("aws_iam_policy_attachment",)
_PREFIX = {"local": "local", "doc": "data.aws_iam_policy_document", "pol": "aws_iam_policy"}
_IDENTITY_TYPES = ("aws_iam_role", "aws_iam_user", "aws_iam_group")
_IDENT_RE = re.compile(r"aws_iam_(?:role|user|group)\.\w+")
_REF_RE = re.compile(r"\blocal\.(\w+)|\bdata\.aws_iam_policy_document\.(\w+)|\baws_iam_policy\.(\w+)")
_DECL = r"(?:Decision \d+|rec-\d+)"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _Ident(_Strict):
    file: str
    resource: str = Field(pattern=r"^aws_iam_(?:role|user|group)\.\w+$")
    name: str


class _Grant(_Strict):
    file: str
    sids: list[str] = Field(min_length=1)


class _Class(_Strict):
    identity: _Ident
    grant: _Grant
    paths: list[Literal["function_url", "rest_route"]] = Field(min_length=1)
    project_scope: list[str] = Field(min_length=1)
    project_scope_enforcement: str
    admitted_by: str = Field(pattern=rf"^{_DECL}$")
    pinned_by: str
    retire_when: str | None = Field(default=None, pattern=rf"^{_DECL}$")


class _Exclusion(_Grant):
    reason: str


class _Registry(_Strict):
    contract: dict
    amendment_log: list
    procedure: str
    tenant_scope: str
    scope_enforcement_owner: str
    paths: dict[Literal["function_url", "rest_route"], str]
    exclusions: dict[Literal["PlatformAdmin", "ci_apply_boundary"], _Exclusion]
    classes: dict[str, _Class]


class _UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):  # type: ignore[no-untyped-def]
        keys = [self.construct_object(k, deep=True) for k, _ in node.value]
        if len(keys) != len(set(map(str, keys))):
            raise yaml.YAMLError(f"duplicated key in mapping at line {node.start_mark.line + 1}")
        return super().construct_mapping(node, deep)


def load_registry(text: str | None, writer_classes: list[str]) -> tuple[_Registry | None, list[str]]:
    if text is None:
        return None, [f"{REGISTRY_REL} is missing"]
    try:
        reg = _Registry.model_validate(yaml.load(text, Loader=_UniqueLoader))  # noqa: S506 - SafeLoader subclass
    except (yaml.YAMLError, ValidationError, TypeError) as exc:
        return None, [f"{REGISTRY_REL} is malformed: {str(exc).splitlines()[0]}"]
    problems = []
    if list(reg.classes) != writer_classes:
        problems.append(f"{REGISTRY_REL} class keys {list(reg.classes)} differ from principal_classes {writer_classes}")
    problems += [f"exclusion {token} is also a class" for token in set(reg.exclusions) & set(reg.classes)]
    for cls, row in reg.classes.items():
        problems += [f"class {cls} uses path {p} with no definition" for p in row.paths if p not in reg.paths]
    return reg, problems


_HEADER_RE = re.compile(r'^(\w+)((?:[ \t]+"[^"\n]*")*)[ \t]*\{', re.M)
_KEY_RE = re.compile(r'"?([A-Za-z_][\w-]*)"?[ \t]*=(?![=>])')


_CHUNK_RE = re.compile(r'[^"\\$]+')
_OPEN_RE = re.compile(r'"|<<-?\w+\n')


def _str_end(t: str, i: int) -> int:
    j = i + 1
    while j < len(t):
        if chunk := _CHUNK_RE.match(t, j):
            j = chunk.end()
        elif t[j] == "\\":
            j += 2
        elif t.startswith("${", j):
            j = _interp_end(t, j + 2)
        elif t[j] == '"':
            return j + 1
        else:
            j += 1
    return len(t)


def _interp_end(t: str, j: int) -> int:
    depth = 1
    while j < len(t) and depth:
        if t[j] == '"':
            j = _str_end(t, j)
            continue
        depth += (t[j] == "{") - (t[j] == "}")
        j += 1
    return j


@lru_cache(maxsize=512)
def _mask(t: str) -> str:
    out, i = list(t), 0
    while opener := _OPEN_RE.search(t, i):
        i = opener.start()
        if t[i] == '"':
            end = _str_end(t, i)
            lo, hi = i + 1, end - 1 if end - 1 > i and t[end - 1] == '"' else end
        else:
            tail = re.search(rf"\n[ \t]*{opener.group(0)[2:].strip('-').strip()}\b", t[opener.end() - 1 :])
            end = len(t) if tail is None else opener.end() - 1 + tail.end()
            lo, hi = i, end
        for k in range(lo, hi):
            if out[k] != "\n":
                out[k] = "_"
        i = end
    return "".join(out)


@lru_cache(maxsize=4096)
def _match(masked: str, open_idx: int) -> int:
    depth = 0
    for i in range(open_idx, len(masked)):
        depth += masked[i] in "([{"
        depth -= masked[i] in ")]}"
        if depth == 0:
            return i
    raise ValueError(f"unbalanced bracket at index {open_idx}")


@lru_cache(maxsize=2048)
def _top_keys(masked: str) -> tuple[tuple[int, int], ...]:
    """(key_start, value_start) of every `key =` at the object's own level."""
    out, depth, i = [], 0, 0
    while i < len(masked):
        c = masked[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and (c.isalpha() or c in '_"') and not (i and (masked[i - 1].isalnum() or masked[i - 1] in '_."-')):
            if m := _KEY_RE.match(masked, i):
                out.append((i, m.end()))
                i = m.end()
                continue
        i += 1
    return tuple(out)


_NESTED_RE = re.compile(r'\n[ \t]*[A-Za-z_]\w*(?:[ \t]+"[^"\n]*")*[ \t]*\{')


def _attrs(body: str) -> dict[str, str]:
    masked = _mask(body)
    keys = _top_keys(masked)
    out = {}
    for n, (k, v) in enumerate(keys):
        name = _KEY_RE.match(body, k)
        stop = keys[n + 1][0] if n + 1 < len(keys) else len(body)
        for nest in _NESTED_RE.finditer(masked, v, stop):
            seg = masked[v : nest.start()]
            if sum(seg.count(c) for c in "([{") == sum(seg.count(c) for c in ")]}"):
                stop = nest.start()
                break
        out[name.group(1) if name else ""] = body[v:stop].strip().rstrip(",").strip()
    return out


def _blocks(text: str) -> list[tuple[str, tuple[str, ...], str]]:
    masked, out = _mask(text), []
    for m in _HEADER_RE.finditer(text):
        end = _match(masked, m.end() - 1)
        out.append((m.group(1), tuple(re.findall(r'"([^"\n]*)"', m.group(2))), text[m.end() : end]))
    return out


def _prune(text: str) -> str:
    """Drop lifecycle/precondition/postcondition blocks and depends_on: references there are not uses."""
    masked, out, pos = _mask(text), [], 0
    for m in sorted(
        re.finditer(r"\b(?:(?:lifecycle|precondition|postcondition)\s*\{|depends_on\s*=\s*\[)", masked),
        key=lambda m: m.start(),
    ):
        if m.start() >= pos:
            out.append(text[pos : m.start()])
            pos = _match(masked, m.end() - 1) + 1
    return "".join(out) + text[pos:]


def _prep(files: dict[str, str]) -> dict[str, str]:
    stripped = {rel: _spg._strip_comments(t) for rel, t in files.items()}
    seen: dict[str, list[str]] = {}
    for t in stripped.values():
        for kind, _, body in _blocks(t):
            for name, expr in _attrs(body).items() if kind == "locals" else ():
                seen.setdefault(name, []).append(expr)
    lits = {n: v[0][1:-1] for n, v in seen.items() if len(v) == 1 and re.fullmatch(r'"[^"$\\]*"', v[0])}
    sub = re.compile(r"\$\{local\.(\w+)\}")
    return {rel: sub.sub(lambda m: lits.get(m.group(1), m.group(0)), t) for rel, t in stripped.items()}


def _holder_kind(stmt: dict) -> str | None:
    if stmt["effect"] != "Allow":
        return None
    kind, res = stmt["resource"]
    acts = stmt["actions"]
    wild = stmt["actions_nonliteral"] or stmt["not_action"]
    if wild or any(fnmatch.fnmatchcase(v, a.lower()) for a in acts for v in _INVOKE):
        if kind != "literal" or res is None or stmt["not_resource"] or any(_lambda_covers(r) for r in res):
            return "lambda"
    if wild or any(fnmatch.fnmatchcase("execute-api:invoke", a.lower()) for a in acts):
        if (
            kind != "literal"
            or res is None
            or stmt["not_resource"]
            or any(fnmatch.fnmatchcase(_W_API, _spg.normalise(r)) for r in res)
        ):
            return "api"
    return None


def _lambda_covers(resource: str) -> bool:
    n = _spg.normalise(resource)
    if "function:" not in n:
        return fnmatch.fnmatchcase(_W_LAMBDA, n)
    head, tail = n.split("function:", 1)
    name, sep, _ = tail.partition(":")
    pat = f"{head}function:{name}"
    return fnmatch.fnmatchcase(_W_LAMBDA, pat) or (not sep and fnmatch.fnmatchcase(_W_LAMBDA + ":x", pat))


def _stmt_bodies(text: str, sid: str) -> list[str]:
    starts = [text.rfind("{", 0, m.start()) for m in re.finditer(rf'\b[Ss]id\s*=\s*"{re.escape(sid)}"', text)]
    return [_vir._extract_block(text, s) if s >= 0 else "" for s in starts]


def _resource_name(text: str, resource: str) -> str | None:
    rtype, rname = resource.split(".", 1)
    m = re.search(rf'resource\s+"{rtype}"\s+"{rname}"\s*\{{', text)
    if m is None:
        return ""
    lit = re.search(r"^\s*name\s*=\s*(.+?)\s*$", _vir._extract_block(text, m.end() - 1), re.M)
    q = re.fullmatch(r'"([^"$]+)"', lit.group(1)) if lit else None
    return q.group(1) if q else None


class _Root:
    def __init__(self, files: dict[str, str]) -> None:
        self.files = files
        self.nodes: dict[tuple[str, str], tuple[str, str]] = {}
        self.attach: list[tuple[str, set[str], set[tuple[str, str]]]] = []
        self.others: list[tuple[str, str]] = []
        self.refs: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for rel, text in files.items():
            for kind, labels, body in _blocks(text):
                self._block(rel, kind, labels, body)
        for key, (rel, expr) in self.nodes.items():
            for ref in self._refs(expr, key[0] == "pol"):
                self.refs.setdefault(ref, set()).add(key)

    @staticmethod
    def _refs(text: str, policy: bool = False) -> set[tuple[str, str]]:
        return {
            ("local", m.group(1)) if m.group(1) else ("doc", m.group(2)) if m.group(2) else ("pol", m.group(3))
            for m in _REF_RE.finditer(_prune(text))
            if not (policy and m.group(3))
        }

    def _block(self, rel: str, kind: str, labels: tuple[str, ...], body: str) -> None:
        addr = ".".join(labels)
        attrs = _attrs(body)
        if kind == "locals":
            for name, expr in attrs.items():
                self.nodes[("local", name)] = (rel, expr)
        elif kind == "data" and labels[:1] == ("aws_iam_policy_document",):
            self.nodes[("doc", labels[1])] = (rel, body)
        elif kind == "resource" and labels[:1] == ("aws_iam_policy",):
            self.nodes[("pol", labels[1])] = (rel, attrs.get("policy", ""))
        elif kind == "resource" and labels[0] in _ATTACH_POLICY_TYPES:
            ids = " ".join(attrs.get(k, "") for k in ("role", "user", "group"))
            self.attach.append(("attach", set(_IDENT_RE.findall(ids)), self._refs(attrs.get("policy", ""))))
        elif kind == "resource" and labels[0] in _ATTACH_ARN_TYPES:
            ids = " ".join(attrs.get(k, "") for k in ("role", "user", "group", "roles", "users", "groups"))
            self.attach.append(("attach", set(_IDENT_RE.findall(ids)), self._refs(attrs.get("policy_arn", ""))))
        elif kind == "resource" and labels[0] in _IDENTITY_TYPES:
            me = {addr}
            self.attach.append(("boundary", me, self._refs(attrs.get("permissions_boundary", ""))))
            managed = self._refs(
                attrs.get("managed_policy_arns", "") + " ".join(re.findall(r"inline_policy\s*\{(.*?)\n\s*\}", body, re.S))
            )
            self.attach.append(("attach", me, managed))
        elif kind in ("resource", "data"):
            self.others.append((f"{kind}.{addr}", _prune(body)))

    def closure(self, start: tuple[str, str]) -> tuple[set[str], list[str]]:
        """Identities the node's statements are attached to, and any use outside an attachment."""
        todo, seen, attached, stray = [start], {start}, set(), []
        while todo:
            node = todo.pop()
            for kind, idents, refs in self.attach:
                if node in refs and kind == "attach":
                    attached |= idents
            for use in self.refs.get(node, ()):
                if use not in seen:
                    seen.add(use)
                    todo.append(use)
            name = node[1]
            pat = rf"\b{re.escape(_PREFIX[node[0]])}\.{name}\b"
            stray += [addr for addr, body in self.others if re.search(pat, body)]
        return attached, stray

    def holding_node(self, sid: str) -> tuple[str, str] | None:
        hits = [
            (len(expr), key) for key, (_, expr) in self.nodes.items() if re.search(rf'\b[Ss]id\s*=\s*"{re.escape(sid)}"', expr)
        ]
        return min(hits)[1] if hits else None


def _policy_source_findings(root: _Root, rel: str, text: str) -> list[str]:
    out = []
    for kind, labels, body in _blocks(text):
        if (kind == "resource" and labels[0] in _POLICY_TYPES) or (
            kind == "data" and labels[:1] == ("aws_iam_policy_document",)
        ):
            attrs = _attrs(body)
            for m in re.finditer(r"inline_policy\s*\{", body):
                attrs.update({k: v for k, v in _attrs(_vir._extract_block(body, m.end() - 1)).items() if k == "policy"})
            for key in _POLICY_ATTRS:
                if key in attrs and not _readable(root, attrs[key]):
                    out.append(f"{rel}: {'.'.join(labels)} {key} is not statically readable (fail closed)")
            if kind == "data" and re.search(r'\bdynamic\s+"statement"', body):
                out.append(f"{rel}: data.{'.'.join(labels)} builds statements dynamically (fail closed)")
    return out


def _readable(root: _Root, expr: str) -> bool:
    expr = expr.strip()
    if expr.startswith("jsonencode("):
        return True
    items = re.findall(r"[^\s,\[\]]+", expr) if expr.startswith("[") else [expr]
    for item in items:
        doc = re.fullmatch(r"data\.aws_iam_policy_document\.(\w+)\.json", item)
        loc = re.fullmatch(r"local\.(\w+)", item)
        if doc and ("doc", doc.group(1)) in root.nodes:
            continue
        if loc and root.nodes.get(("local", loc.group(1)), ("", ""))[1].lstrip().startswith("jsonencode("):
            continue
        return False
    return True


def _attachment_findings(root: _Root, rel: str, text: str) -> list[str]:
    out = []
    for kind, labels, body in _blocks(text):
        if kind != "resource":
            continue
        attrs = _attrs(body)
        values = [attrs.get("policy_arn", ""), attrs.get("managed_policy_arns", "")]
        for value in filter(None, values):
            for item in re.findall(r"[^\s,\[\]]+", value):
                m = re.fullmatch(r"aws_iam_policy\.(\w+)\.arn", item)
                if not (m and ("pol", m.group(1)) in root.nodes):
                    out.append(f"{rel}: {'.'.join(labels)} attaches {item}, not an aws_iam_policy declared in this root")
        direct = re.match(
            r"\s*(?:aws_iam_policy\.github_ci_apply_boundary\b|local\.github_ci_apply_boundary_policy_json\b)",
            attrs.get("policy", ""),
        )
        in_arn = any(b in v for v in values for b in _BOUNDARY)
        if (in_arn or (direct and labels[0] in _ATTACH_POLICY_TYPES)) and labels[
            0
        ] in _ATTACH_ARN_TYPES + _ATTACH_POLICY_TYPES + _IDENTITY_TYPES:
            out.append(f"{rel}: {'.'.join(labels)} attaches the CI apply permissions boundary (a ceiling, never a grant)")
    return out


def _bare_statement_findings(rel: str, text: str) -> list[str]:
    masked, out = _mask(text), []
    for m in re.finditer(r"\{", masked):
        base = m.start() + 1
        keys = {
            mm.group(1) for k, _ in _top_keys(masked[base : _match(masked, base - 1)]) if (mm := _KEY_RE.match(text, base + k))
        }
        if {"Action", "NotAction"} & keys and "Effect" not in keys:
            out.append(f"{rel}: an object with Action but no literal Effect is invisible to the scan (fail closed)")
    return out


def _file_findings(reg: _Registry, rel: str, stmts: list[dict]) -> list[str]:
    out = []
    declared = {(c.grant.file, s): n for n, c in reg.classes.items() for s in c.grant.sids}
    declared |= {(e.file, s): n for n, e in reg.exclusions.items() for s in e.sids}
    for stmt in stmts:
        if _holder_kind(stmt) is None:
            continue
        if (rel, stmt["sid"]) not in declared:
            out.append(f"{rel}: Sid {stmt['sid'] or '<none>'} can invoke the production telemetry writer and is not declared")
        elif stmt["not_action"] or stmt["not_resource"]:
            out.append(f"{rel}: Sid {stmt['sid']} uses NotAction or NotResource on a writer holder")
    return out


def _class_findings(reg: _Registry, tree: dict[str, str], stmts: dict[str, list[dict]], roots: dict[str, _Root]) -> list[str]:
    out = []
    for cls, row in reg.classes.items():
        ident, grant = row.identity, row.grant
        got = _resource_name(tree.get(ident.file, ""), ident.resource)
        if got != ident.name:
            why = "absent or renamed" if got == "" else f"not named {ident.name}"
            out.append(f"class {cls}: {ident.resource} in {ident.file} is {why}")
        text = tree.get(grant.file, "")
        for sid in grant.sids:
            bodies = _stmt_bodies(text, sid)
            if len(bodies) != 1:
                out.append(f"class {cls}: Sid {sid} appears {len(bodies)} times in {grant.file}, not once")
                continue
            stmt = next((s for s in stmts.get(grant.file, []) if s["sid"] == sid), None)
            out += _path_shape(cls, row, sid, bodies[0], stmt)
        out += _closure_findings(f"class {cls}", roots, grant.file, grant.sids, {ident.resource})
    for name, excl in reg.exclusions.items():
        body = "\n".join(b for sid in excl.sids for b in _stmt_bodies(tree.get(excl.file, ""), sid))
        if name == "PlatformAdmin" and not re.search(r'Resource\s*=\s*"\*"', body):
            out.append('exclusion PlatformAdmin: its statement no longer has Resource "*"')
        out += _closure_findings(f"exclusion {name}", roots, excl.file, excl.sids, _EXCLUSION_ATTACH[name])
    return out


def _path_shape(cls: str, row: _Class, sid: str, body: str, stmt: dict | None) -> list[str]:
    if stmt is None:
        return [f"class {cls}: Sid {sid} is not readable as a policy statement (fail closed)"]
    acts = {a.lower() for a in stmt["actions"]}
    if "function_url" in row.paths and "rest_route" not in row.paths:
        bad = sorted(a for a in acts if a not in _ALLOWED_URL_ACTIONS)
        out = (
            [f"class {cls}: Sid {sid} carries action(s) {bad} outside its function_url path"]
            if bad or stmt["actions_nonliteral"]
            else []
        )
        if "lambda:invokefunction" in acts and not (
            re.search(r"InvokedViaFunctionUrl", body) and re.search(r"Bool", body) and '"true"' in body
        ):
            out.append(f"class {cls}: Sid {sid} grants lambda:InvokeFunction without Bool lambda:InvokedViaFunctionUrl true")
        return out
    if "function_url" not in row.paths and _holder_kind(stmt) == "lambda":
        return [f"class {cls}: Sid {sid} is a rest_route class yet holds a Lambda invoke on the writer (Decision 256 cl.2)"]
    return []


def _closure_findings(label: str, roots: dict[str, _Root], file: str, sids: list[str], allowed: set[str]) -> list[str]:
    root = next((r for r in roots.values() if file in r.files), None)
    out: list[str] = []
    if root is None:
        return out
    for sid in sids:
        node = root.holding_node(sid)
        if node is None:
            out.append(f"{label}: Sid {sid} is held by no local, policy document or aws_iam_policy in its root")
            continue
        attached, stray = root.closure(node)
        extra = sorted(attached - allowed)
        if extra or (allowed and not allowed <= attached) or stray:
            reach = extra or sorted(stray) or "no attachment to its identity"
            out.append(f"{label}: Sid {sid} reaches {reach}, expected only {sorted(allowed)}")
    return out


def _root_files(tree: dict[str, str], root_rel: str) -> dict[str, str]:
    return {rel: t for rel, t in tree.items() if rel.startswith(root_rel + "/") and rel.endswith(".tf")}


def holder_sids(tree: dict[str, str]) -> set[tuple[str, str | None]]:
    """(file, Sid) of every statement that can invoke the production writer."""
    prepped = {rel: text for root_rel in _ROOTS for rel, text in _prep(_root_files(tree, root_rel)).items()}
    return {(rel, s["sid"]) for rel, text in prepped.items() for s in _spg.parse_statements(text) if _holder_kind(s)}


def analyse(tree: dict[str, str], reg: _Registry) -> tuple[list[str], int]:
    """Findings for a {relative path: text} mapping of both Terraform roots, and the statements examined."""
    try:
        return _analyse(tree, reg)
    except ValueError as exc:
        return [f"terraform text has unbalanced brackets and cannot be parsed ({exc}; fail closed)"], 0


def _analyse(tree: dict[str, str], reg: _Registry) -> tuple[list[str], int]:
    findings, stmts, prepped = [], {}, {}
    for root_rel in _ROOTS:
        prepped.update(_prep(_root_files(tree, root_rel)))
    roots = {r: _Root({rel: t for rel, t in prepped.items() if rel.startswith(r + "/")}) for r in _ROOTS}
    examined = 0
    for root in roots.values():
        for rel, text in root.files.items():
            stmts[rel] = _spg.parse_statements(text)
            findings += _file_findings(reg, rel, stmts[rel])
            findings += _policy_source_findings(root, rel, text) + _attachment_findings(root, rel, text)
            findings += _bare_statement_findings(rel, text)
            examined += len(stmts.get(rel, []))
    findings += _class_findings(reg, prepped, stmts, roots)
    return findings, examined


@registry.register("validate_telemetry_writer_principals", owner="platform")
def validate_telemetry_writer_principals(failed: list[str], root: Path | None = None) -> None:
    """Every identity able to invoke the production telemetry writer is a declared class or exclusion (rec-4420)."""
    print("\n=== Telemetry writer principals (every writer holder is declared) ===")
    root = root if root is not None else _common.ROOT
    key = "telemetry-writer-principals:"
    before = len(failed)
    tree = {
        p.relative_to(root).as_posix(): p.read_text(encoding="utf-8")
        for rel in _ROOTS
        if (root / rel).is_dir()
        for p in sorted((root / rel).glob("*.tf"))
    }
    if not tree:
        registry.skipped("neither terraform/personal nor terraform/bootstrap exists")
        print("  SKIP: no terraform roots")
        return
    try:
        classes = list(getattr(load_contract(root / CONTRACT_REL).contract.governance, "principal_classes", None) or [])
    except Exception as exc:  # noqa: BLE001 - a broken contract is a finding, never a pass
        classes = []
        failed.append(f"{key} {CONTRACT_REL} cannot be loaded: {exc}")
    path = root / REGISTRY_REL
    reg, problems = load_registry(path.read_text(encoding="utf-8") if path.exists() else None, classes)
    failed += [f"{key} {p}" for p in problems]
    examined = 0
    if reg is not None:
        findings, examined = analyse(tree, reg)
        failed += [f"{key} {f}" for f in findings]
    for finding in failed[before:]:
        print(f"  FAIL: {finding}")
    if len(failed) == before:
        print(f"  PASS: {examined} statement(s) examined; every writer holder is a declared class or exclusion.")
    registry.examined(examined, unit="iam_statements")


if __name__ == "__main__":  # pragma: no cover
    _failed: list[str] = []
    validate_telemetry_writer_principals(_failed)
    print("\n".join(f"  - {_f}" for _f in _failed))
    raise SystemExit(1 if _failed else 0)
