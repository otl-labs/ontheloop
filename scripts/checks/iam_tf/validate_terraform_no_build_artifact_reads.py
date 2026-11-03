"""Terraform never reads a build artifact (rec-4243; docs/contracts/environment-taxonomy.yaml conformance).

Terraform re-evaluates file functions when it applies a saved plan, and nothing masks a create, so a
configuration file read of a path a job builds or downloads (lambda-packages/*.zip) is a plan input
that may be absent at apply time. Function and layer code is addressed only by s3_bucket/s3_key and
ships through the code-deploy channel. Three rules, one finding per (file, line):

- (a) a filesystem path with `lambda-packages` as a path segment;
- (b) a filesystem path that git reports as ignored once resolved against the file's own directory
  (every terraform/<root>/ is its own root module with no child modules, so ${path.module},
  ${path.root}, ${path.cwd} and an unprefixed file-function argument all resolve there);
- (c) any `source_code_hash` argument, whatever its value.

A string literal is a filesystem path when it starts with ${path.module}, ${path.root}, ${path.cwd},
./ or ../, or when it is the plain first argument of a file-family function. An s3_key literal such as
"lambda-packages/x.zip" is neither and is legitimate. An unanswerable ignore probe fails closed.

Declared residual: a path assembled from non-literal parts (format(), a variable value supplied at plan
time) cannot be resolved statically; none exists today (rec-4246 carries the review checklist item).
"""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Callable
from pathlib import Path

from scripts.checks import _common, registry
from scripts.checks.iam_tf.validate_terraform_try import _blank_comment_lines

_STRING_RE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_FILE_FUNCS = (
    "filebase64sha256|filebase64sha512|filebase64|filemd5|filesha1|filesha256|filesha512|templatefile|fileexists|fileset|file"
)
_FILE_ARG_RE = re.compile(rf"(?<![\w.])(?:{_FILE_FUNCS})\s*\(\s*$")
_PREFIX_RE = re.compile(r"^(?:\$\{path\.(?:module|root|cwd)\}/?|\./|\.\./)")
_ANCHORED_RE = re.compile(r"^(?:\$\{path\.(?:module|root|cwd)\}|\./|\.\./)")
_LAMBDA_PKG_RE = re.compile(r"(?:^|/)lambda-packages(?:/|$)")
_HASH_ARG_RE = re.compile(r"^[ \t]*source_code_hash[ \t]*=", re.MULTILINE)

Finding = tuple[str, int, list[str]]


def _is_path_literal(content: str, start: int, literal: str) -> bool:
    if _ANCHORED_RE.match(literal):
        return True
    line_start = content.rfind("\n", 0, start) + 1
    return bool(_FILE_ARG_RE.search(content[line_start:start]))


def _resolve(tf_file: Path, literal: str) -> str | None:
    rel = _PREFIX_RE.sub(lambda m: m.group(0) if m.group(0) in ("./", "../") else "", literal)
    if "${" in rel:
        return None
    return Path(os.path.normpath(tf_file.parent / rel)).as_posix()


def find_build_artifact_reads(tf_root: Path, is_ignored: Callable[[str], bool]) -> list[Finding]:
    """Sorted findings (path relative to tf_root, line, reasons) over every *.tf under tf_root."""
    found: dict[tuple[str, int], list[str]] = {}

    def add(rel: str, line: int, reason: str) -> None:
        reasons = found.setdefault((rel, line), [])
        if reason not in reasons:
            reasons.append(reason)

    for tf_file in sorted(tf_root.rglob("*.tf")):
        if ".terraform" in tf_file.relative_to(tf_root).parts:
            continue
        rel = tf_file.relative_to(tf_root).as_posix()
        content = _blank_comment_lines(tf_file.read_text(encoding="utf-8"))
        for m in _HASH_ARG_RE.finditer(content):
            add(rel, content.count("\n", 0, m.start()) + 1, "(c) source_code_hash argument")
        for m in _STRING_RE.finditer(content):
            literal = m.group(1)
            if not _is_path_literal(content, m.start(), literal):
                continue
            line = content.count("\n", 0, m.start()) + 1
            if _LAMBDA_PKG_RE.search(literal):
                add(rel, line, "(a) reads a lambda-packages path")
            resolved = _resolve(tf_file, literal)
            if resolved is None:
                continue
            try:
                ignored = is_ignored(resolved) or is_ignored(resolved + "/")
            except Exception as exc:
                add(rel, line, f"(b) ignore probe failed closed: {exc}")
                continue
            if ignored:
                add(rel, line, "(b) reads a git-ignored path")
    return [(rel, line, reasons) for (rel, line), reasons in sorted(found.items())]


def _git_check_ignore(path: str) -> bool:
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--", path],
        cwd=_common.ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore exited {proc.returncode}")
    return proc.returncode == 0


@registry.register("validate_terraform_no_build_artifact_reads", owner="platform")
def validate_terraform_no_build_artifact_reads(failed: list[str]) -> None:
    """No terraform configuration reads a build artifact or sets source_code_hash."""
    print("\n=== Terraform build-artifact reads ===")
    tf_root = _common.ROOT / "terraform"
    findings = find_build_artifact_reads(tf_root, _git_check_ignore)
    registry.examined(
        sum(1 for p in tf_root.rglob("*.tf") if ".terraform" not in p.relative_to(tf_root).parts), unit="tf_files"
    )
    for rel, line, reasons in findings:
        print(
            f"  terraform/{rel}:{line}: {'; '.join(reasons)} -- address function and layer code by "
            "s3_bucket/s3_key and ship it through the code-deploy channel "
            "(docs/contracts/environment-taxonomy.yaml conformance; rec-4243)"
        )
    if findings:
        failed.append("Terraform build-artifact reads")
    else:
        print("No terraform file read names a build artifact and no resource sets source_code_hash.")


if __name__ == "__main__":  # pragma: no cover
    _failed: list[str] = []
    validate_terraform_no_build_artifact_reads(_failed)
    raise SystemExit(1 if _failed else 0)
