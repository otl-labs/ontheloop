"""Tests for validate_licence_consistency() (Decision 171).

Two invariants, exercised in both directions. The negative cases are driven by FIXTURE trees
under tmp_path, never by mutating the real repository, so a test that proves the check goes red
on an old-slug survivor keeps proving it after the rename is complete. One test runs the check
against the real tree to assert the shipped artefacts actually satisfy it.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

from scripts.checks import registry
from scripts.checks.hygiene import validate_licence_consistency as mod

_VALID_LICENSE = """Business Source License 1.1

License text copyright (c) 2024 MariaDB plc, All Rights Reserved.

-----------------------------------------------------------------------------

Parameters

Licensor:             Benjamin Blake

Licensed Work:        On The Loop

Additional Use Grant: You may make use of the Licensed Work for any purpose
                      that is not a production use. This enumeration is
                      illustrative and is not exhaustive; it neither limits nor
                      qualifies the rights granted to you by the Terms below.

Change Date:          2030-08-15

Change License:       Apache License, Version 2.0

-----------------------------------------------------------------------------

Terms
"""

_VALID_APACHE = "                                 Apache License\n                           Version 2.0, January 2004\n"

_VALID_LICENSING = (
    "# Licensing boundary\n\n"
    "Every version up to and including commit `4de9df86e02b7eeccf58df83e74f6061fc1303e2` is\n"
    "Apache-2.0. The rule is self-verifying: read the LICENSE file as published by the Licensor\n"
    "at that commit.\n\n"
    "On The Loop is the same Licensed Work formerly named Theseus.\n"
)

_VALID_README = "# theseus\n\nLicensed under the Business Source License 1.1. See LICENSING.md.\n"


def _tree(tmp_path: Path, **overrides: str | None) -> Path:
    """Write a licence-artefact fixture tree. An override of None omits that file."""
    files = {
        "LICENSE": _VALID_LICENSE,
        "LICENSE-APACHE": _VALID_APACHE,
        "LICENSING.md": _VALID_LICENSING,
        "README.md": _VALID_README,
    }
    files.update(overrides)
    for name, text in files.items():
        if text is None:
            continue
        (tmp_path / name).write_text(text, encoding="utf-8")
    return tmp_path


def _run(tmp_path: Path, class_a: dict[str, re.Pattern[str]] | None = None) -> list[str]:
    failed: list[str] = []
    with (
        patch.object(mod, "_REPO_ROOT", tmp_path),
        patch.object(mod, "_CLASS_A_PATTERNS", {} if class_a is None else class_a),
    ):
        mod.validate_licence_consistency(failed)
    return failed


class TestLicenceArtefactConsistency:
    def test_valid_fixture_tree_passes(self, tmp_path: Path) -> None:
        assert _run(_tree(tmp_path)) == []

    def test_missing_licence_fails(self, tmp_path: Path) -> None:
        failed = _run(_tree(tmp_path, LICENSE=None))
        assert any("LICENSE is missing" in f for f in failed)

    def test_unpopulated_parameter_fails(self, tmp_path: Path) -> None:
        stripped = _VALID_LICENSE.replace("Licensor:             Benjamin Blake", "Licensor:")
        failed = _run(_tree(tmp_path, LICENSE=stripped))
        assert any("lacks a populated Licensor parameter" in f for f in failed)

    def test_absent_additional_use_grant_block_fails(self, tmp_path: Path) -> None:
        without_grant = re.sub(r"Additional Use Grant:.*?\n\nChange Date:", "Change Date:", _VALID_LICENSE, flags=re.S)
        failed = _run(_tree(tmp_path, LICENSE=without_grant))
        assert any("has no Additional Use Grant block" in f for f in failed)

    def test_grant_without_non_limitation_clause_fails(self, tmp_path: Path) -> None:
        # An enumerated grant whose non-limitation clause was dropped: every Parameter is still
        # populated, so only the clause assertion can catch the narrowing.
        narrowed = _VALID_LICENSE.replace(
            "This enumeration is\n"
            "                      illustrative and is not exhaustive; it neither limits nor\n"
            "                      qualifies the rights granted to you by the Terms below.\n",
            "\n",
        )
        failed = _run(_tree(tmp_path, LICENSE=narrowed))
        assert any("lacks its non-limitation clause" in f for f in failed)

    def test_bracket_placeholder_in_parameters_fails(self, tmp_path: Path) -> None:
        templated = _VALID_LICENSE.replace("Licensed Work:        On The Loop", "Licensed Work:        [Name]")
        failed = _run(_tree(tmp_path, LICENSE=templated))
        assert any("still contains a bracket placeholder" in f for f in failed)

    def test_missing_apache_text_fails(self, tmp_path: Path) -> None:
        assert any("LICENSE-APACHE is missing" in f for f in _run(_tree(tmp_path, **{"LICENSE-APACHE": None})))

    def test_apache_file_without_apache_text_fails(self, tmp_path: Path) -> None:
        failed = _run(_tree(tmp_path, **{"LICENSE-APACHE": "MIT License\n"}))
        assert any("does not contain the Apache-2.0 text" in f for f in failed)

    def test_missing_licensing_doc_fails(self, tmp_path: Path) -> None:
        assert any("LICENSING.md is missing" in f for f in _run(_tree(tmp_path, **{"LICENSING.md": None})))

    def test_licensing_without_boundary_commit_fails(self, tmp_path: Path) -> None:
        failed = _run(
            _tree(
                tmp_path, **{"LICENSING.md": _VALID_LICENSING.replace("4de9df86e02b7eeccf58df83e74f6061fc1303e2", "4de9df8")}
            )
        )
        assert any("names no 40-hex boundary commit" in f for f in failed)

    def test_licensing_without_provenance_scope_fails(self, tmp_path: Path) -> None:
        failed = _run(_tree(tmp_path, **{"LICENSING.md": _VALID_LICENSING.replace(" as published by the Licensor", "")}))
        assert any("not provenance-scoped" in f for f in failed)

    def test_missing_readme_fails(self, tmp_path: Path) -> None:
        assert any("README.md is missing" in f for f in _run(_tree(tmp_path, **{"README.md": None})))

    def test_readme_not_stating_busl_fails(self, tmp_path: Path) -> None:
        failed = _run(_tree(tmp_path, **{"README.md": "# theseus\n\nSee LICENSING.md.\n"}))
        assert any("does not state the BUSL-1.1 licence" in f for f in failed)

    def test_readme_not_linking_licensing_fails(self, tmp_path: Path) -> None:
        failed = _run(_tree(tmp_path, **{"README.md": "# theseus\n\nBusiness Source License 1.1.\n"}))
        assert any("does not link LICENSING.md" in f for f in failed)


def test_licensed_work_names_the_ratified_mark(tmp_path: Path) -> None:
    old_mark = _VALID_LICENSE.replace("Licensed Work:        On The Loop", "Licensed Work:        Theseus")
    failed = _run(_tree(tmp_path, LICENSE=old_mark))
    assert any("does not name the ratified mark" in f for f in failed)


def test_licensing_continuity_note_names_both_marks(tmp_path: Path) -> None:
    without_note = _VALID_LICENSING.replace("On The Loop is the same Licensed Work formerly named Theseus.\n", "")
    failed = _run(_tree(tmp_path, **{"LICENSING.md": without_note}))
    assert any("carries no continuity note" in f for f in failed)


class TestClassASlugHygiene:
    def test_old_slug_survivor_fails_with_line_number(self, tmp_path: Path) -> None:
        (tmp_path / "probe.py").write_text('x = 1\nREPO = "benjamin-blake/agent-platform"\n', encoding="utf-8")
        failed = _run(_tree(tmp_path), {"probe.py": re.compile(r"benjamin-blake/agent-platform")})
        assert any("probe.py:2 still references the old repository slug" in f for f in failed)

    def test_renamed_slug_passes(self, tmp_path: Path) -> None:
        (tmp_path / "probe.py").write_text('REPO = "benjamin-blake/theseus"\n', encoding="utf-8")
        assert _run(_tree(tmp_path), {"probe.py": re.compile(r"benjamin-blake/agent-platform")}) == []

    def test_class_b_aws_name_in_same_file_does_not_trip_the_check(self, tmp_path: Path) -> None:
        # The bucket, the IAM role and the SSM prefix are AWS resource names that survive the
        # rename. A bare-token pattern would flag them forever; the shipped full-slug pattern
        # must not.
        (tmp_path / "escalate.py").write_text(
            'REPO = "benjamin-blake/theseus"\n'
            'BUCKET = "s3://agent-platform-data-lake"\n'
            'ROLE = "agent-platform-github-ci-pr"\n'
            'SSM = "/agent-platform/convergence"\n',
            encoding="utf-8",
        )
        pattern = mod._CLASS_A_PATTERNS["scripts/convergence_health/escalate.py"]
        assert _run(_tree(tmp_path), {"escalate.py": pattern}) == []

    def test_dropped_path_is_reported_rather_than_passing_silently(self, tmp_path: Path) -> None:
        failed = _run(_tree(tmp_path), {"gone.py": re.compile(r"benjamin-blake/agent-platform")})
        assert any("class-(a) path gone.py does not exist" in f for f in failed)


def test_the_archive_inherits_the_roadmap_class_a_pattern(tmp_path: Path) -> None:
    """Decision 227: retired roadmap records moved to the archive keep the roadmap's class-(a) guard."""
    pattern = mod._CLASS_A_PATTERNS["docs/ROADMAP-PLATFORM-ARCHIVE.yaml"]
    assert pattern.pattern == mod._CLASS_A_PATTERNS["docs/ROADMAP-PLATFORM.yaml"].pattern
    (tmp_path / "archive.yaml").write_text(
        "tier_items:\n  - id: T0.1\n    intent: benjamin-blake/agent-platform\n", encoding="utf-8"
    )
    failed = _run(_tree(tmp_path), {"archive.yaml": pattern})
    assert any("archive.yaml:3 still references the old repository slug" in f for f in failed)


class TestAccountingDeclaration:
    def test_declares_every_file_it_examined(self, tmp_path: Path) -> None:
        (tmp_path / "probe.py").write_text("clean\n", encoding="utf-8")
        registry.pop_declaration()
        _run(_tree(tmp_path), {"probe.py": re.compile(r"benjamin-blake/agent-platform")})
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.kind == "examined"
        assert declaration.count == 5  # 4 licence artefacts + 1 class-(a) path
        assert declaration.unit == "licence_and_class_a_files"

    def test_declaration_is_not_vacuous_on_the_real_tree(self) -> None:
        registry.pop_declaration()
        mod.validate_licence_consistency([])
        declaration = registry.pop_declaration()
        assert declaration is not None
        assert declaration.count > 0


class TestRealTree:
    def test_shipped_artefacts_satisfy_the_check(self) -> None:
        failed: list[str] = []
        mod.validate_licence_consistency(failed)
        assert failed == []
