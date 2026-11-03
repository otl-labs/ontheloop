"""Mirror test for scripts/checks/contracts/_manifest.py (Decision 169, amends Decision 104)."""

from __future__ import annotations

import importlib
from fnmatch import fnmatch

import pytest

from scripts.checks import registry
from scripts.checks.contracts import _manifest


class TestContractsManifest:
    """Every contracts Entry names a bare-literal module/attr pair resolving to the same-named
    registered check inside this package."""

    def test_entries_is_non_empty(self) -> None:
        assert _manifest.ENTRIES

    def test_no_duplicate_names_within_this_manifest(self) -> None:
        names = [entry.name for entry in _manifest.ENTRIES]
        assert len(names) == len(set(names))

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_module_is_inside_this_domain_package(self, entry) -> None:
        assert entry.module.startswith("scripts.checks.contracts.")

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_entry_resolves_to_a_callable_named_its_own_attr(self, entry) -> None:
        module = importlib.import_module(entry.module)
        fn = getattr(module, entry.attr)
        assert callable(fn)
        assert fn.__name__ == entry.attr

    @pytest.mark.parametrize("entry", _manifest.ENTRIES, ids=[entry.name for entry in _manifest.ENTRIES])
    def test_registry_resolve_matches_the_manifest_entry(self, entry) -> None:
        module = importlib.import_module(entry.module)
        assert registry.resolve(entry.name) is getattr(module, entry.attr)


# One row per promoted gated Entry: repo-relative paths in that check's read set (transitive
# first-party import closure plus every data/config path the body opens).
_CLOSURE_INPUTS: dict[str, tuple[str, ...]] = {
    # One Path.exists() on the Decision 38 ghost file: creating it is necessarily a .github/ diff.
    "validate_no_underscore_instructions": (
        ".github/copilot_instructions.md",
        "scripts/checks/contracts/validate_no_underscore_instructions.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    # Reads root CLAUDE.md only -- AGENTS.md is the pointer TARGET, never opened by this check.
    "validate_claude_md_pointer_invariant": (
        "CLAUDE.md",
        "scripts/checks/contracts/validate_claude_md_pointer_invariant.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    # The invariant-source globs come from the contract's behavioural_invariant_sources key, so
    # the contract is an input alongside the SKILL.md files it currently resolves to.
    "validate_prompt_compliance": (
        ".claude/skills/planning/SKILL.md",
        "docs/contracts/instruction-architecture.yaml",
        "scripts/prompt_compliance.py",
        "logs/.retro-lite-log.jsonl",
        "logs/.execution-state.json",
        "scripts/checks/contracts/_shared.py",
        "scripts/checks/contracts/validate_prompt_compliance.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    # Every layer's content_locations must resolve, so the union of today's declared locations is
    # part of the read set -- a DELETION inside one of them is exactly what this check catches,
    # and such a diff names no other closure member.
    "validate_instruction_architecture_layers": (
        "docs/contracts/instruction-architecture.yaml",
        "scripts/prompt_compliance.py",
        "CLAUDE.md",
        "scripts/CLAUDE.md",
        "AGENTS.md",
        "docs/PROJECT_CONTEXT.md",
        ".claude/commands/plan.md",
        ".claude/skills/planning/SKILL.md",
        "config/agent/executor/prompts/implement.prompt.md",
        "config/agent/executor/instructions/python.instructions.md",
        "scripts/checks/contracts/_shared.py",
        "scripts/checks/contracts/validate_instruction_architecture_layers.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
    # rec-4068 (R2): the module joins this check's closure via ducklake_scd2_schema.py's
    # module-scope import of ducklake_partition_spec.
    "validate_maintenance_policy_matrix": (
        "config/lambda/ducklake/field_semantics.static.yaml",
        "src/common/ducklake_maintenance_scope.py",
        "src/common/ducklake_scd2_schema.py",
        "src/common/ducklake_named_reads.py",
        "src/common/ducklake_write_verbs.py",
        "src/common/ducklake_partition_spec.py",
        "scripts/checks/contracts/validate_maintenance_policy_matrix.py",
        "scripts/checks/_common.py",
        "scripts/checks/registry.py",
    ),
}


class TestPromotedGateClosures:
    """Each promoted check's read-set member is asserted MATCHED by its patterns, not present in
    them as a literal.

    Bare fnmatch, not scripts.validate._pre_glob_match, for the reason
    tests/checks/decisions/test__manifest.py::TestClosureMembersAreCovered states: the production
    matcher is fnmatch PLUS a leading-'**/' retry that can only ADD matches. That retry is why
    "scripts/CLAUDE.md" is asserted here but repo-root "CLAUDE.md" is carried as its own literal
    glob -- bare "**/CLAUDE.md" never matches a root-level file.
    """

    @staticmethod
    def _covered(name: str, path: str) -> bool:
        globs = next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ()
        return any(fnmatch(path, glob) for glob in globs)

    @pytest.mark.parametrize(
        ("name", "path"),
        [(name, path) for name, paths in _CLOSURE_INPUTS.items() for path in paths],
        ids=[f"{name}-{path}" for name, paths in _CLOSURE_INPUTS.items() for path in paths],
    )
    def test_a_diff_touching_only_this_closure_member_still_matches_the_gate(self, name: str, path: str) -> None:
        assert self._covered(name, path)

    @pytest.mark.parametrize("name", sorted(_CLOSURE_INPUTS))
    def test_an_unrelated_path_is_not_matched(self, name: str) -> None:
        """Anti-vacuity: the rows above would also pass against a catch-all pattern."""
        assert not self._covered(name, "terraform/personal/main.tf")


class TestRoadmapArchiveGlobs:
    """Decision 227: both roadmap-reading contracts checks rerun on a diff touching only the archive or
    only the hydration module their load() import closure now reaches."""

    _NAMES = ("validate_ports_contract", "validate_project_configuration")

    @staticmethod
    def _globs(name: str) -> tuple[str, ...]:
        return next(e for e in _manifest.ENTRIES if e.name == name).pre_globs or ()

    @pytest.mark.parametrize("name", _NAMES)
    @pytest.mark.parametrize("path", ["docs/ROADMAP-PLATFORM-ARCHIVE.yaml", "scripts/roadmap/roadmap_archive.py"])
    def test_an_archive_only_diff_matches_the_gate(self, name: str, path: str) -> None:
        assert any(fnmatch(path, glob) for glob in self._globs(name))

    @pytest.mark.parametrize("name", _NAMES)
    def test_an_unrelated_roadmap_package_path_is_not_matched(self, name: str) -> None:
        assert not any(fnmatch("scripts/roadmap/plan_audit.py", glob) for glob in self._globs(name))


class TestGithubOrgTransferPromotedUngated:
    """The org-transfer evaluator reads the contract plus both trust files (and, once the literal sweep
    extends it, every tracked file), so it is ungated like validate_portal_drift."""

    def test_is_dispatched_in_pre_and_full(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_github_org_transfer" in pre_names
        assert "validate_github_org_transfer" in full_names

    def test_declares_no_pre_globs(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_github_org_transfer")
        assert entry.pre_globs is None


class TestAccountCredentialInventoryPromoted:
    """The inventory's refs may name any repository YAML file, so the check is ungated like the org-transfer one."""

    def test_is_dispatched_in_pre_and_full(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_account_credential_inventory" in pre_names
        assert "validate_account_credential_inventory" in full_names

    def test_declares_no_pre_globs(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_account_credential_inventory")
        assert entry.pre_globs is None


class TestPortalDriftPromotedUngated:
    """Promoted into --pre WITHOUT globs, deliberately. Every answer_loci path it resolves comes
    OUT of EVALUATION-PROMPTS.yaml at run time and may name any path in the repo, so no static
    glob encloses the read set: gating on the three portal files would silently skip the
    deleted-locus case (Decision 101 public-content boundary) this gate exists to catch."""

    def test_is_dispatched_in_pre(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        assert "validate_portal_drift" in pre_names

    def test_declares_no_pre_globs(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_portal_drift")
        assert entry.pre_globs is None


class TestTelemetryFieldRealizationRegistered:
    """The realization check runs in both tiers and its globs cover every input it reads."""

    def test_is_dispatched_in_pre_and_full(self) -> None:
        pre_names = {step.name for step in registry.pre_sequence() if step.kind == "check"}
        full_names = {step.name for step in registry.full_sequence() if step.kind == "check"}
        assert "validate_telemetry_field_realization" in pre_names
        assert "validate_telemetry_field_realization" in full_names

    def test_pre_globs_cover_the_inputs(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_telemetry_field_realization")
        globs = set(entry.pre_globs or ())
        for needed in (
            "docs/contracts/telemetry_*.yaml",
            "config/telemetry/parser_versions.yaml",
            "tests/fixtures/turn_capture/golden/**",
            "config/lambda/ducklake/field_semantics.yaml",
            "docs/ROADMAP-PLATFORM.yaml",
            "scripts/contracts_schema.py",
        ):
            assert needed in globs
