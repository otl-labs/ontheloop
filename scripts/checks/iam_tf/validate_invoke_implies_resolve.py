from __future__ import annotations

import re
from pathlib import Path

from scripts.checks import _common, registry

_PERSONAL_DIR_REL = Path("terraform") / "personal"

_DATA_BLOCK_RE = re.compile(r'data\s+"aws_iam_policy_document"\s+"(?P<name>\w+)"\s*\{')
_ROLE_POLICY_BLOCK_RE = re.compile(r'resource\s+"aws_iam_role_policy"\s+"(?P<role>\w+)"\s*\{')
_STATEMENT_BLOCK_RE = re.compile(r"statement\s*\{")
_SOURCE_DOCS_RE = re.compile(r"source_policy_documents\s*=\s*\[(?P<body>.*?)\]", re.DOTALL)
_SOURCE_DOC_REF_RE = re.compile(r"data\.aws_iam_policy_document\.(\w+)\.json")
_POLICY_REF_RE = re.compile(r"policy\s*=\s*data\.aws_iam_policy_document\.(\w+)\.json")
_SID_RE = re.compile(r'\bsid\s*=\s*"([^"]*)"')
_ACTIONS_RE = re.compile(r"\bactions\s*=\s*\[(?P<body>.*?)\]", re.DOTALL)
_RESOURCES_RE = re.compile(r"\bresources\s*=\s*\[(?P<body>.*?)\]", re.DOTALL)
_QUOTED_RE = re.compile(r'"([^"]*)"')

_DUCKLAKE_INVOKE_MARKERS = ("ducklake_writer", "ducklake_reader", "ducklake_telemetry_writer")
_INVOKE_ACTIONS = {"lambda:InvokeFunction", "lambda:*"}
_SSM_READ_ACTIONS = {"ssm:Get*", "ssm:*"}
_SSM_RESOURCE_MARKER = "parameter/agent-platform"


def _read_root_text(root_dir: Path) -> str:
    """Concatenate every *.tf file's text in a terraform root, sorted by filename.

    Deliberately NOT imported from _read_coverage: _read_coverage imports THIS module (for
    _extract_block / _QUOTED_RE / _parse_policy_documents / etc.), so importing back from it here
    would be a circular import. This is the same two-line concatenation as
    _read_coverage._read_root_text -- kept in sync by hand, not by import.
    """
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(root_dir.glob("*.tf")))


def _extract_block(text: str, open_brace_idx: int) -> str:
    """Return the body between the '{' at open_brace_idx and its matching '}'.

    A plain depth counter is safe here even though HCL interpolations (${...}) contain
    literal brace characters: every ${ is balanced by a } within the same expression, so
    the net depth at the true closing brace is unaffected.
    """
    depth = 0
    i = open_brace_idx
    n = len(text)
    start_body = open_brace_idx + 1
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start_body:i]
        i += 1
    raise ValueError(f"Unbalanced braces starting at index {open_brace_idx}")


def _find_statements(body: str) -> list[dict]:
    statements = []
    for m in _STATEMENT_BLOCK_RE.finditer(body):
        stmt_body = _extract_block(body, m.end() - 1)
        sid_m = _SID_RE.search(stmt_body)
        actions_m = _ACTIONS_RE.search(stmt_body)
        resources_m = _RESOURCES_RE.search(stmt_body)
        statements.append(
            {
                "sid": sid_m.group(1) if sid_m else None,
                "actions": _QUOTED_RE.findall(actions_m.group("body")) if actions_m else [],
                # Resources can mix quoted ARN strings with bare references
                # (aws_lambda_function.ducklake_writer.arn) -- keep the raw text for substring
                # checks rather than only the quoted subset.
                "resources_raw": resources_m.group("body") if resources_m else "",
            }
        )
    return statements


def _parse_policy_documents(text: str) -> dict[str, dict]:
    docs: dict[str, dict] = {}
    for m in _DATA_BLOCK_RE.finditer(text):
        body = _extract_block(text, m.end() - 1)
        source_m = _SOURCE_DOCS_RE.search(body)
        sources = _SOURCE_DOC_REF_RE.findall(source_m.group("body")) if source_m else []
        docs[m.group("name")] = {"sources": sources, "statements": _find_statements(body)}
    return docs


def _parse_role_policy_map(text: str) -> dict[str, str]:
    mapping = {}
    for m in _ROLE_POLICY_BLOCK_RE.finditer(text):
        body = _extract_block(text, m.end() - 1)
        policy_m = _POLICY_REF_RE.search(body)
        if policy_m:
            mapping[m.group("role")] = policy_m.group(1)
    return mapping


def _resolve_statements(
    doc_name: str,
    docs: dict[str, dict],
    _seen: set[str] | None = None,
    unresolved: list[str] | None = None,
) -> list[dict]:
    """Transitively resolve a policy document's own statements plus every statement
    contributed by its (possibly nested) source_policy_documents.

    `unresolved` (optional, backward-compatible default None): when a `doc_name` -- top-level or
    any nested source -- is not a key in `docs`, it is appended here rather than merely dropped.
    Decision 129 clause 4 (fail-loud on both indirections): a caller that ignores `unresolved` gets
    the pre-existing behaviour (a missing document silently contributes zero statements); a caller
    that checks it can distinguish "genuinely resolves to nothing invoking" from "could not be
    resolved at all" -- the vacuous-pass hazard root-wide parsing widens (more documents now live in
    sibling files, so more names can go missing from `docs` if a split repoint is incomplete).
    """
    if _seen is None:
        _seen = set()
    if doc_name in _seen:
        return []
    _seen.add(doc_name)
    doc = docs.get(doc_name)
    if doc is None:
        if unresolved is not None:
            unresolved.append(doc_name)
        return []
    resolved: list[dict] = []
    for source_name in doc["sources"]:
        resolved.extend(_resolve_statements(source_name, docs, _seen, unresolved))
    resolved.extend(doc["statements"])
    return resolved


def _invokes_ducklake(statements: list[dict]) -> bool:
    return any(
        action in _INVOKE_ACTIONS and any(marker in stmt["resources_raw"] for marker in _DUCKLAKE_INVOKE_MARKERS)
        for stmt in statements
        for action in stmt["actions"]
    )


def _resolves_ssm_read(statements: list[dict]) -> bool:
    return any(
        action in _SSM_READ_ACTIONS and _SSM_RESOURCE_MARKER in stmt["resources_raw"]
        for stmt in statements
        for action in stmt["actions"]
    )


@registry.register("validate_invoke_implies_resolve", owner="platform")
def validate_invoke_implies_resolve(failed: list[str]) -> None:
    """Credential-free CI-role invariant (T2.34:c2, Decision 104).

    Every CI role that grants lambda:InvokeFunction on the DuckLake reader/writer must also
    resolve SSM parameter reads (ssm:Get* on parameter/agent-platform/*) in its composed
    aws_iam_policy_document -- the fallback src/common/ducklake_reader_client.py and
    scripts/ops_data_portal.py use to resolve the DuckLake Function URL when DUCKLAKE_*_URL is
    unset. A role that invokes without SSM loses that fallback (rec-2363 and predecessors
    rec-2223/2251/2276). Parses every *.tf file under terraform/personal/ (root-wide, not a single
    file) and resolves source_policy_documents composition transitively, including across sibling
    files in the root -- no boto3, no AWS call, no terraform invocation (Decision 119 / Decision
    55), so this runs credential-free in --pre and full.

    Decision 129 clause 4 (fail-loud on both indirections): a role whose OWN policy document, or
    any document it transitively sources, cannot be resolved in the parsed root FAILS rather than
    taking the "PASS (vacuous)" branch -- an unresolvable document is not evidence the role doesn't
    invoke, it is evidence the parse couldn't tell. This is distinguished from the legitimately
    vacuous case (every referenced document resolves; none of them invokes the DuckLake reader/writer).
    """
    print("\n=== CI role invoke-implies-resolve invariant (T2.34:c2) ===")
    personal_dir = _common.ROOT / _PERSONAL_DIR_REL
    text = _read_root_text(personal_dir)
    if not text:
        registry.skipped(f"no *.tf file readable under {personal_dir}")
        failed.append(f"invoke-implies-resolve: cannot read any *.tf file under {personal_dir}")
        print(f"  FAIL: cannot read any *.tf file under {personal_dir}")
        return

    docs = _parse_policy_documents(text)
    role_policy = _parse_role_policy_map(text)

    if not docs or not role_policy:
        failed.append(
            f"invoke-implies-resolve: no aws_iam_policy_document / aws_iam_role_policy blocks found under {personal_dir}"
        )
        print("  FAIL: no policy documents or role policies parsed -- has the HCL shape changed?")
        return

    registry.examined(len(role_policy), unit="ci_roles")
    for role, doc_name in sorted(role_policy.items()):
        unresolved: list[str] = []
        statements = _resolve_statements(doc_name, docs, unresolved=unresolved)
        if unresolved:
            failed.append(
                f"invoke-implies-resolve: role {role!r} (policy document {doc_name!r}) references "
                f"unresolvable policy document(s) {sorted(set(unresolved))!r} -- cannot determine whether "
                "it invokes the DuckLake reader/writer (Decision 129 clause 4: fail loud, never PASS vacuous)"
            )
            print(f"  FAIL: {role} references unresolvable document(s) {sorted(set(unresolved))}.")
            continue
        if not _invokes_ducklake(statements):
            print(f"  PASS (vacuous): {role} does not invoke the DuckLake reader/writer.")
            continue
        if _resolves_ssm_read(statements):
            print(f"  PASS: {role} invokes the DuckLake reader/writer and resolves SSM parameter reads.")
        else:
            failed.append(
                f"invoke-implies-resolve: role {role!r} (policy document {doc_name!r}) grants "
                "lambda:InvokeFunction on the DuckLake reader/writer but its resolved composition "
                "lacks ssm:Get* on parameter/agent-platform/* -- source ci_ssm_refresh_read (directly "
                "or transitively) from its aws_iam_policy_document"
            )
            print(f"  FAIL: {role} invokes the DuckLake reader/writer but does not resolve SSM parameter reads.")
