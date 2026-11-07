#!/usr/bin/env python3
"""Static screen of a Terraform root, run before any credential exists (Decision 238 cl.3, cl.4).

Run by path with `python3 -I`; the repo root is appended (never inserted at 0) so the stdlib always wins. Pure
functions over {relative path: text} so tests run on synthetic trees, plus two CLI verbs:

  screen --root DIR [--policy ROW]       prints PASS, or one "REFUSED <rule> <file>:<line>" line per finding; exit 1 on a
                                         refusal. ROW defaults to terraform/personal.
  backstop --plan-json F [--policy ROW]  after plan, checks Terraform's own parsed configuration; prints only BACKSTOP_OK
                                         or "BACKSTOP_REFUSED <rule>" (no address, no expression).

Each root's constants are a code-owned row in terraform_hcl_screen_policy.py. The screen reads only public PR text, so
printing locations is safe.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
sys.path.append(str(_ROOT))

from scripts.ci import terraform_hcl_lexer as lexer  # noqa: E402
from scripts.ci import terraform_hcl_screen_policy as policies  # noqa: E402
from scripts.ci.terraform_hcl_lexer import LexRefusal, Token  # noqa: E402


def _package_resolves_under_root() -> bool:
    return Path(lexer.__file__).resolve().is_relative_to(_ROOT)


_PERSONAL = policies.default()
MAIN_ONLY_FILES = _PERSONAL.main_only_files
DOC_FILES = frozenset({"README.md", "CLAUDE.md"})
ALLOWED_DATA_TYPES = _PERSONAL.data_types
ALLOWED_RESOURCE_TYPES = _PERSONAL.resource_types
ALLOWED_FUNCTIONS = _PERSONAL.functions
VALIDATION_ONLY_FUNCTIONS = _PERSONAL.validation_only_functions
ALLOWED_FILE_PATHS = _PERSONAL.file_paths
TOP_LEVEL_BLOCKS = _PERSONAL.top_level_blocks
Policy = policies.Policy
_KEYWORDS = frozenset({"for", "in", "if", "else", "endif", "endfor"})
_SHAPE_OPERATORS = frozenset({"?", "==", "!=", "<", ">", "<=", ">=", "&&", "||", "!"})
_CONDITION_CONTEXTS = (("lifecycle", "precondition"), ("lifecycle", "postcondition"), ("variable", "validation"))
_FILE_NAME_RE = re.compile(r"[A-Za-z0-9._-]+")
_LITERAL_IDENTS = frozenset({"true", "false", "null"})

Finding = tuple[str, str, int]  # rule, file, line


def _significant(tokens: list[Token]) -> list[Token]:
    return [t for t in tokens if t.kind != "nl"]


def _block_name_at(tokens: list[Token], open_idx: int) -> str | None:
    """Name of the block opened by tokens[open_idx] ('{'), or None for an object constructor."""
    j = open_idx - 1
    header: list[Token] = []
    while j >= 0 and tokens[j].kind != "nl" and not (tokens[j].kind == "punct" and tokens[j].text in "{}"):
        header.append(tokens[j])
        j -= 1
    header.reverse()
    if header and header[0].kind == "ident" and all(t.kind in ("ident", "str") for t in header):
        return header[0].text
    return None


def _walk(tokens: list[Token], stack: tuple[str, ...]) -> Any:
    """Yield (token, block stack) for every token, descending into interpolations with the parent's stack."""
    stack_list = list(stack)
    for idx, tok in enumerate(tokens):
        if tok.kind == "punct" and tok.text == "{":
            name = _block_name_at(tokens, idx)
            stack_list.append(name or "")
        yield tok, tuple(stack_list)
        for sub in tok.subs:
            yield from _walk(sub, tuple(stack_list))
        if tok.kind == "punct" and tok.text == "}" and stack_list:
            stack_list.pop()


def _in_condition(stack: tuple[str, ...]) -> bool:
    return any(stack[-len(c) :] == c for c in _CONDITION_CONTEXTS if len(stack) >= len(c))


def _file_rules(name: str, text: str, policy: Policy) -> list[Finding]:
    try:
        tokens = lexer.lex(text)
    except LexRefusal as exc:
        return [(exc.rule, name, exc.line)]
    walked = list(_walk(tokens, ()))
    out = _top_level(name, tokens, policy) + _shape_and_function_rules(name, walked)
    out += _validation_function_rules(name, walked, policy)
    if policy.imports_allowed:
        out += _import_rules(name, tokens, policy)
    for group in [tokens] + [sub for tok in tokens for sub in tok.subs]:
        out += _call_rules(name, _significant(group), policy)
    return out


def _block_type_finding(name: str, sig: list[Token], i: int, policy: Policy) -> list[Finding]:
    tok = sig[i]
    labels: list[str] = []
    for nxt in sig[i + 1 :]:
        if nxt.kind != "str":
            break
        labels.append(nxt.text)
    if not labels:
        return []
    if tok.text == "resource" and labels[0] not in policy.resource_types:
        return [("resource_type", name, tok.line)]
    if tok.text == "data" and labels[0] not in policy.data_types:
        return [("data_type", name, tok.line)]
    return []


def _top_level_token(name: str, sig: list[Token], i: int, policy: Policy) -> list[Finding]:
    tok = sig[i]
    prev = sig[i - 1] if i else None
    if tok.kind == "str" and prev is not None and prev.kind in ("ident", "str"):
        return []
    starts_block = prev is None or (prev.kind == "punct" and prev.text == "}")
    if tok.kind != "ident" or not starts_block:
        return [("unclassified_token", name, tok.line)]
    if tok.text not in policy.top_level_blocks:
        return [("toplevel_block", name, tok.line)]
    return _block_type_finding(name, sig, i, policy)


def _top_level(name: str, tokens: list[Token], policy: Policy) -> list[Finding]:
    out: list[Finding] = []
    depth = 0
    sig = _significant(tokens)
    for i, tok in enumerate(sig):
        prev = sig[i - 1] if i else None
        if tok.kind == "punct" and tok.text in "{[(":
            if depth == 0 and not (tok.text == "{" and prev is not None and prev.kind in ("ident", "str")):
                out.append(("unclassified_token", name, tok.line))
            depth += 1
        elif tok.kind == "punct" and tok.text in "}])":
            depth -= 1
        elif depth == 0:
            out += _top_level_token(name, sig, i, policy)
    return out


def _opens_block(walked: list[tuple[Token, tuple[str, ...]]], idx: int) -> bool:
    prev = walked[idx - 1][0] if idx else None
    nxt = walked[idx + 1][0] if idx + 1 < len(walked) else None
    if nxt is None or (prev is not None and prev.kind == "punct" and prev.text == "."):
        return False
    return nxt.kind in ("ident", "str") or (nxt.kind == "punct" and nxt.text == "{")


def _shape_and_function_rules(name: str, walked: list[tuple[Token, tuple[str, ...]]]) -> list[Finding]:
    out: list[Finding] = []
    for idx, (tok, stack) in enumerate(walked):
        if tok.kind == "punct":
            if tok.text in _SHAPE_OPERATORS and not _in_condition(stack):
                rule = "conditional_operator" if tok.text == "?" else "comparison_or_logical_operator"
                out.append((rule, name, tok.line))
            elif tok.text == "::":
                out.append(("provider_function", name, tok.line))
        elif tok.kind == "ident":
            nxt = walked[idx + 1][0] if idx + 1 < len(walked) else None
            if tok.text == "if":
                out.append(("for_if_clause", name, tok.line))
            elif tok.text in ("dynamic", "provisioner", "connection") and _opens_block(walked, idx):
                rule = "dynamic_block" if tok.text == "dynamic" else "provisioner_block"
                out.append((rule, name, tok.line))
            elif (
                tok.text in ("count", "for_each")
                and nxt is not None
                and nxt.kind == "punct"
                and nxt.text == "="
                and stack in (("resource",), ("data",))
            ):
                out.append(("meta_argument", name, tok.line))
    return out


def _call_rules(name: str, sig: list[Token], policy: Policy) -> list[Finding]:
    out: list[Finding] = []
    for i, tok in enumerate(sig):
        if tok.kind != "ident" or tok.text in _KEYWORDS or i + 1 >= len(sig):
            continue
        if not (sig[i + 1].kind == "punct" and sig[i + 1].text == "("):
            continue
        if tok.text not in policy.functions:
            out.append(("function_not_allowlisted", name, tok.line))
        elif tok.text == "file" and not _file_call_ok(sig, i, policy):
            out.append(("file_path_not_allowlisted", name, tok.line))
        elif tok.text == "try" and not _try_call_ok(sig, i):
            out.append(("try_form_not_allowed", name, tok.line))
    return out


def _file_call_ok(sig: list[Token], i: int, policy: Policy) -> bool:
    return (
        i + 3 < len(sig)
        and sig[i + 2].kind == "str"
        and sig[i + 2].text in policy.file_paths
        and sig[i + 3].kind == "punct"
        and sig[i + 3].text == ")"
    )


def _try_call_ok(sig: list[Token], i: int) -> bool:
    """try(yamldecode(file(<STR>))<.ident | [STR]>, <literal>)"""
    texts = [(t.kind, t.text) for t in sig[i : i + 16]]
    head = [("ident", "try"), ("punct", "("), ("ident", "yamldecode"), ("punct", "("), ("ident", "file"), ("punct", "(")]
    if texts[:6] != head or len(texts) < 9 or texts[6][0] != "str" or texts[7:9] != [("punct", ")"), ("punct", ")")]:
        return False
    rest = texts[9:]
    if rest[:2] and rest[0] == ("punct", ".") and rest[1][0] == "ident":
        rest = rest[2:]
    elif len(rest) >= 3 and rest[0] == ("punct", "[") and rest[1][0] == "str" and rest[2] == ("punct", "]"):
        rest = rest[3:]
    else:
        return False
    if len(rest) < 3 or rest[0] != ("punct", ",") or rest[2] != ("punct", ")"):
        return False
    lit = rest[1]
    return lit[0] in ("str", "number") or (lit[0] == "ident" and lit[1] in _LITERAL_IDENTS)


def _validation_function_rules(name: str, walked: list[tuple[Token, tuple[str, ...]]], policy: Policy) -> list[Finding]:
    return [
        ("validation_only_function", name, tok.line)
        for tok, stack in walked
        if tok.kind == "ident" and tok.text in policy.validation_only_functions and not _in_validation(stack)
    ]


def _in_validation(stack: tuple[str, ...]) -> bool:
    return stack[-2:] == ("variable", "validation")


def tree_rules(files: dict[str, str], policy: Policy = _PERSONAL) -> list[Finding]:
    out: list[Finding] = []
    for name in sorted(files):
        if name in policy.main_only_files or name in DOC_FILES:
            continue
        if name.endswith(".tf.json"):
            out.append(("tf_json", name, 0))
        elif name == "override.tf" or name.endswith("_override.tf"):
            out.append(("override_file", name, 0))
        elif name == "terraform.tfvars" or name.endswith((".auto.tfvars", ".auto.tfvars.json")):
            out.append(("tfvars_file", name, 0))
        elif not name.endswith(".tf"):
            out.append(("file_not_tf", name, 0))
    return out


def screen_tree(files: dict[str, str], policy: Policy = _PERSONAL) -> list[Finding]:
    """Rules over a {relative file name: text} map of one root. The row's main-only files come from main, not lexed."""
    out = tree_rules(files, policy)
    for name in sorted(files):
        if name.endswith(".tf") and name not in policy.main_only_files:
            out += _file_rules(name, files[name], policy)
    return sorted(set(out), key=lambda f: (f[1], f[2], f[0]))


def read_root(root: Path) -> tuple[dict[str, str], list[Finding]]:
    files: dict[str, str] = {}
    findings: list[Finding] = []
    for entry in sorted(root.iterdir()):
        if entry.is_dir():
            if entry.name != ".terraform":
                findings.append(("subdirectory", entry.name, 0))
            continue
        if not _FILE_NAME_RE.fullmatch(entry.name):
            findings.append(("file_name", "invalid-name", 0))
            continue
        try:
            files[entry.name] = entry.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            findings.append(("non_ascii_or_control", entry.name, 0))
    return files, findings


def screen_root(root: Path, policy: Policy = _PERSONAL) -> list[Finding]:
    files, findings = read_root(root)
    return sorted(set(findings + screen_tree(files, policy)), key=lambda f: (f[1], f[2], f[0]))


def _resource_refusal(res: dict[str, Any], policy: Policy) -> str | None:
    if res.get("provisioners") or res.get("count_expression") or res.get("for_each_expression"):
        return "provisioner_or_meta_argument"
    is_data = res.get("mode") == "data"
    if res.get("type") not in (policy.data_types if is_data else policy.resource_types):
        return "data_type" if is_data else "resource_type"
    return None


def _importing_refusal(changes: list[Any], policy: Policy) -> bool:
    return any(
        (rc.get("change") or {}).get("importing") and not (policy.imports_allowed and rc.get("type") in policy.resource_types)
        for rc in changes
    )


def backstop(plan: Any, policy: Policy = _PERSONAL) -> str | None:
    """Rule id of the first refusal found in Terraform's own parsed plan JSON, or None."""
    if not isinstance(plan, dict) or not isinstance(plan.get("configuration"), dict):
        return "plan_unreadable"
    cfg = plan["configuration"]
    providers = cfg.get("provider_config") or {}
    if set(providers) != policy.backstop_providers or any("alias" in (v or {}) for v in providers.values()):
        return "provider_config"
    root = cfg.get("root_module") or {}
    if root.get("module_calls"):
        return "module_calls"
    for res in root.get("resources") or []:
        refusal = _resource_refusal(res, policy)
        if refusal:
            return refusal
    if _importing_refusal(plan.get("resource_changes") or [], policy):
        return "importing"
    if any(((chk or {}).get("address") or {}).get("kind") == "check" for chk in plan.get("checks") or []):
        return "check_block"
    return None


def _import_id_ok(expr: list[Token], policy: Policy) -> bool:
    if len(expr) == 1 and expr[0].kind == "str":
        subs = expr[0].subs
        if not subs:
            return True
        names = [_var_ref(sub) for sub in subs]
        return all(n in policy.import_id_vars for n in names) and "repository_name" in names
    return _var_ref(expr) == "repository_name"


def _var_ref(toks: list[Token]) -> str | None:
    sig = _significant(toks)
    if len(sig) == 3 and [(t.kind, t.text) for t in sig[:2]] == [("ident", "var"), ("punct", ".")] and sig[2].kind == "ident":
        return sig[2].text
    return None


def _import_body_ok(body: list[Token], policy: Policy) -> bool:
    lines: list[list[Token]] = [[]]
    for tok in body:
        if tok.kind == "nl":
            lines.append([])
        else:
            lines[-1].append(tok)
    lines = [ln for ln in lines if ln]
    if len(lines) != 2:
        return False
    args = {ln[0].text: ln[2:] for ln in lines if len(ln) > 2 and ln[0].kind == "ident" and ln[1].text == "="}
    if set(args) != {"to", "id"}:
        return False
    to = args["to"]
    shape = [t.kind for t in to] == ["ident", "punct", "ident"] and to[1].text == "."
    return shape and to[0].text in policy.resource_types and _import_id_ok(args["id"], policy)


def _import_rules(name: str, tokens: list[Token], policy: Policy) -> list[Finding]:
    out: list[Finding] = []
    depth = 0
    for i, tok in enumerate(tokens):
        if tok.kind == "punct" and tok.text in "{[(":
            depth += 1
        elif tok.kind == "punct" and tok.text in "}])":
            depth -= 1
        elif depth == 0 and tok.kind == "ident" and tok.text == "import":
            nxt = tokens[i + 1] if i + 1 < len(tokens) else None
            if nxt is None or nxt.text != "{":
                out.append(("import_shape", name, tok.line))
                continue
            end, level = i + 2, 1
            while end < len(tokens) and level:
                if tokens[end].kind == "punct" and tokens[end].text in "{[(":
                    level += 1
                elif tokens[end].kind == "punct" and tokens[end].text in "}])":
                    level -= 1
                end += 1
            if level or not _import_body_ok(tokens[i + 2 : end - 1], policy):
                out.append(("import_shape", name, tok.line))
    return out


def _row_from_args(args: list[str]) -> tuple[list[str], str]:
    if len(args) >= 2 and args[-2] == "--policy":
        return args[:-2], args[-1]
    return args, policies.DEFAULT_ROOT


def _mismatched(root_dir: str, row: str) -> bool:
    parts = os.path.abspath(root_dir).split(os.sep)
    pair = "/".join(parts[-2:])
    return pair != row and pair in policies.POLICIES


def main(argv: list[str] | None = None) -> int:
    if not _package_resolves_under_root():
        print("SCREEN_ERROR scripts package did not resolve under the repo root")
        return 2
    args = list(sys.argv[1:] if argv is None else argv)
    args, row = _row_from_args(args)
    policy = policies.get(row)
    if len(args) == 3 and args[0] == "screen" and args[1] == "--root":
        if policy is None:
            print("REFUSED unknown_root")
            return 1
        if _mismatched(args[2], row):
            print("REFUSED root_policy_mismatch")
            return 1
        findings = screen_root(Path(args[2]), policy)
        if not findings:
            print("PASS")
            return 0
        for rule, name, line in findings:
            print(f"REFUSED {rule} {name}:{line}")
        return 1
    if len(args) == 3 and args[0] == "backstop" and args[1] == "--plan-json":
        if policy is None:
            print("BACKSTOP_REFUSED unknown_root")
            return 1
        try:
            plan = json.loads(Path(args[2]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            plan = None
        refused = backstop(plan, policy)
        print("BACKSTOP_OK" if refused is None else f"BACKSTOP_REFUSED {refused}")
        return 0 if refused is None else 1
    print(
        "usage: terraform_hcl_screen.py screen --root DIR [--policy ROW] | backstop --plan-json FILE [--policy ROW]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
