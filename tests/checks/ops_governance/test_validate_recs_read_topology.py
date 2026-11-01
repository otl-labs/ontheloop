"""Tests for validate_recs_read_topology() -- the recs read-topology guard (T2.19 c10)."""

from fnmatch import fnmatch
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from scripts.checks import _common, registry
from scripts.checks.ops_governance import _manifest
from scripts.checks.ops_governance import validate_recs_read_topology as topology

CLEAN_CLIENT = """from typing import Protocol


class Reader(Protocol):
    def current_state(self, table): ...

    def latest_snapshot(self, table): ...


class ReaderInvokeError(RuntimeError):
    pass


class DuckLakeReader:
    def current_state(self, table):
        return []

    def latest_snapshot(self, table):
        return None


def make_reader(profile=None, table=None):
    return DuckLakeReader(profile=profile)
"""

PINNED_GLOBS = (
    "src/common/ducklake_runtime.py",
    "src/common/ducklake_spike.py",
    "src/common/ducklake_connect_probe.py",
    "src/common/ducklake_maintenance.py",
    "src/lambdas/ducklake_reader/**",
    "src/lambdas/ducklake_writer/**",
    "src/lambdas/ducklake_maintenance/**",
    "src/lambdas/ducklake_maintenance_smoke/**",
    "src/lambdas/ducklake_telemetry_reader/**",
    "src/lambdas/ducklake_telemetry_writer/**",
    "src/lambdas/ducklake_catalog_dr/**",
    "src/common/ducklake_event_reads.py",
    "scripts/ducklake_smoke/**",
    "scripts/ducklake_neon_smoke_test.py",
    "src/telemetry/**",
)
DEFENSIVE_ROWS = {"scripts/ducklake_neon_smoke_test.py", "src/telemetry/**"}


def _tree(tmp_path: Path, files: dict[str, str | bytes], client: str | None = CLEAN_CLIENT) -> Path:
    if client is not None:
        files = {topology.READER_CLIENT_REL: client, **files}
    for rel, body in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            target.write_bytes(body)
        else:
            target.write_text(body, encoding="utf-8")
    return tmp_path


def _legs(tmp_path: Path, files: dict[str, str | bytes], client: str | None = CLEAN_CLIENT) -> set[str]:
    return {f.leg for f in topology.scan(_tree(tmp_path, files, client)).findings}


class TestCleanTree:
    def test_clean_tree_has_no_findings(self, tmp_path: Path) -> None:
        assert _legs(tmp_path, {"scripts/ok.py": "x = 1\n"}) == set()


class TestR0Reasons:
    @pytest.mark.parametrize("reason", ["", "   "])
    def test_allowlist_row_without_reason_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str) -> None:
        monkeypatch.setattr(topology, "ALLOWLIST", (*topology.ALLOWLIST, ("scripts/extra/**", reason)))
        assert "R0" in _legs(tmp_path, {})

    def test_declared_reader_without_reason_fails(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(topology, "DECLARED_READERS", (("DuckLakeReader", ""),))
        assert "R0" in _legs(tmp_path, {})


class TestR1Factory:
    def test_return_of_another_class_fails(self, tmp_path: Path) -> None:
        client = CLEAN_CLIENT.replace("return DuckLakeReader(profile=profile)", "return LegacyReader()")
        assert "R1" in _legs(tmp_path, {}, client=client)

    def test_conditional_return_of_undeclared_class_fails(self, tmp_path: Path) -> None:
        client = CLEAN_CLIENT.replace(
            "    return DuckLakeReader(profile=profile)",
            "    if table:\n        return LegacyReader()\n    return DuckLakeReader(profile=profile)",
        )
        assert "R1" in _legs(tmp_path, {}, client=client)

    def test_missing_make_reader_fails(self, tmp_path: Path) -> None:
        client = CLEAN_CLIENT.replace("def make_reader(", "def build_reader(")
        assert "R1" in _legs(tmp_path, {}, client=client)

    def test_make_reader_without_return_fails(self, tmp_path: Path) -> None:
        client = CLEAN_CLIENT.replace("    return DuckLakeReader(profile=profile)", "    pass")
        assert "R1" in _legs(tmp_path, {}, client=client)

    def test_unparseable_client_reports_io_only(self, tmp_path: Path) -> None:
        assert _legs(tmp_path, {}, client="def make_reader(:\n") == {"IO"}

    def test_missing_client_file_fails(self, tmp_path: Path) -> None:
        assert "R1" in _legs(tmp_path, {"scripts/ok.py": "x = 1\n"}, client=None)

    def test_real_client_with_injected_legacy_branch_fails(self, tmp_path: Path) -> None:
        real = (_common.ROOT / topology.READER_CLIENT_REL).read_text(encoding="utf-8")
        needle = "    return DuckLakeReader(profile=profile)"
        assert needle in real
        injected = real.replace(needle, "    if table == 'x':\n        return LegacyReader()\n" + needle)
        assert "R1" in _legs(tmp_path, {}, client=injected)

    def test_branch_between_declared_readers_passes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(topology, "DECLARED_READERS", (*topology.DECLARED_READERS, ("LocalReader", "T4.23 local adapter")))
        client = CLEAN_CLIENT.replace(
            "    return DuckLakeReader(profile=profile)",
            "    if table:\n        return LocalReader()\n    return DuckLakeReader(profile=profile)",
        )
        client = client.replace("def make_reader(", "class LocalReader(DuckLakeReader):\n    pass\n\n\ndef make_reader(")
        assert _legs(tmp_path, {}, client=client) == set()


class TestR2Readers:
    def test_undeclared_reader_class_fails(self, tmp_path: Path) -> None:
        body = "class ShadowReader:\n    def current_state(self, t): ...\n    def latest_snapshot(self, t): ...\n"
        assert "R2" in _legs(tmp_path, {"scripts/shadow.py": body})

    def test_exception_named_reader_passes(self, tmp_path: Path) -> None:
        assert _legs(tmp_path, {"scripts/errs.py": "class ReaderRuntimeError(RuntimeError):\n    pass\n"}) == set()


class TestR3Retired:
    @pytest.mark.parametrize(
        "body",
        [
            "import os\nflag = os.environ['OPS_STORAGE_BACKEND']\n",
            "reader = DuckDBIcebergReader()\n",
            "import os\nos.OPS_STORAGE_BACKEND\n",
            "from src.common.legacy import DuckDBIcebergReader\n",
            "import pyiceberg\n",
            "from src.common.iceberg_reader import x\n",
            "from src.common import iceberg_reader\n",
        ],
    )
    def test_retired_token_fails(self, tmp_path: Path, body: str) -> None:
        assert "R3" in _legs(tmp_path, {"scripts/legacy.py": body})

    @pytest.mark.parametrize(
        "body",
        [
            '"""Retired: OPS_STORAGE_BACKEND."""\n',
            "iceberg_type = 'long'\n",
        ],
    )
    def test_docstring_and_lookalikes_pass(self, tmp_path: Path, body: str) -> None:
        assert _legs(tmp_path, {"scripts/fine.py": body}) == set()


class TestR4Engine:
    @pytest.mark.parametrize(
        "body",
        [
            "def f():\n    import duckdb\n    return duckdb\n",
            "import psycopg2\n",
            "from duckdb import connect\n",
            "from src.common.ducklake_runtime import open_connection\n",
            "import src.common.ducklake_runtime as rt\nrt.get_warm_connection()\n",
            "from src.common import ducklake_runtime\nducklake_runtime.fetch_dsn()\n",
            "import src.common.ducklake_runtime\nsrc.common.ducklake_runtime.open_connection()\n",
            "from src.common import ducklake_spike\nducklake_spike._require_duckdb()\n",
        ],
    )
    def test_direct_engine_access_outside_allowlist_fails(self, tmp_path: Path, body: str) -> None:
        assert "R4" in _legs(tmp_path, {"scripts/direct.py": body})

    @pytest.mark.parametrize(
        "body",
        [
            "ACTION = 'reset_warm_connection'\n",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import duckdb\n",
            "import typing\nif typing.TYPE_CHECKING:\n    import duckdb\n",
            "from src.common.ducklake_runtime import resolve_table_spec\n",
        ],
    )
    def test_non_engine_shapes_pass(self, tmp_path: Path, body: str) -> None:
        assert _legs(tmp_path, {"scripts/fine.py": body}) == set()

    @pytest.mark.parametrize("rel", ["src/lambdas/ducklake_reader/x.py", "scripts/ducklake_smoke/x.py"])
    def test_allowlisted_plane_passes(self, tmp_path: Path, rel: str) -> None:
        body = "from src.common import ducklake_runtime\nducklake_runtime.open_connection()\n"
        assert _legs(tmp_path, {rel: body}) == set()


class TestR4bReexports:
    CORE = "from src.common import ducklake_runtime\nfetch_dsn = ducklake_runtime.fetch_dsn\nOTHER = 1\n"

    def test_reexported_entry_point_import_fails(self, tmp_path: Path) -> None:
        files = {
            "scripts/ducklake_smoke/core.py": self.CORE,
            "scripts/bad.py": "from scripts.ducklake_smoke.core import fetch_dsn\n",
        }
        assert _legs(tmp_path, files) == {"R4b"}

    def test_star_reexport_propagates(self, tmp_path: Path) -> None:
        files = {
            "scripts/ducklake_smoke/core.py": "from src.common.ducklake_runtime import *\n",
            "scripts/bad.py": "from scripts.ducklake_smoke.core import open_connection\n",
        }
        assert _legs(tmp_path, files) == {"R4b"}

    def test_reexporting_module_non_entry_import_passes(self, tmp_path: Path) -> None:
        files = {
            "scripts/ducklake_smoke/core.py": self.CORE,
            "scripts/ok.py": "from scripts.ducklake_smoke.core import OTHER\n",
        }
        assert _legs(tmp_path, files) == set()


class TestUnreadableFiles:
    def test_syntax_error_fails(self, tmp_path: Path) -> None:
        assert "IO" in _legs(tmp_path, {"scripts/broken.py": "def f(:\n"})

    def test_non_utf8_file_fails(self, tmp_path: Path) -> None:
        assert "IO" in _legs(tmp_path, {"scripts/latin.py": b"x = '\xe9'\n"})


class TestRealTree:
    def test_real_tree_passes(self) -> None:
        failed: list[str] = []
        topology.validate_recs_read_topology(failed)
        assert failed == []

    def test_rows_are_pinned(self) -> None:
        assert tuple(glob for glob, _ in topology.ALLOWLIST) == PINNED_GLOBS
        assert tuple(name for name, _ in topology.DECLARED_READERS) == ("DuckLakeReader",)

    def test_every_allowlist_row_matches_a_real_file(self) -> None:
        rels = topology._walk(_common.ROOT)
        stale = [glob for glob in PINNED_GLOBS if not any(fnmatch(rel, glob) for rel in rels)]
        assert stale == []

    def test_empty_allowlist_hits_exactly_the_live_rows(self) -> None:
        result = topology.scan(_common.ROOT, allowlist=())
        assert {f.leg for f in result.findings} == {"R4"}
        hit = {glob for glob in PINNED_GLOBS if any(fnmatch(f.path, glob) for f in result.findings)}
        assert hit == set(PINNED_GLOBS) - DEFENSIVE_ROWS

    def test_derived_exports_cover_the_smoke_core_and_exclude_seeds(self) -> None:
        exports = topology.derive_exports(_common.ROOT)
        assert {"fetch_dsn", "_libpq_conninfo"} <= exports["scripts.ducklake_smoke.core"]
        assert topology.RUNTIME_MODULE not in exports and topology.SPIKE_MODULE not in exports


class TestAccounting:
    def test_declares_examined_files(self, tmp_path: Path) -> None:
        _tree(tmp_path, {"scripts/ok.py": "x = 1\n"})
        with patch("scripts.checks._common.ROOT", tmp_path), registry.outcome_scope("test_scope"):
            failed: list[str] = []
            topology.validate_recs_read_topology(failed)
            declaration = registry.pop_declaration()
        assert failed == []
        assert declaration is not None and declaration.kind == "examined" and declaration.unit == "files"
        assert declaration.count == 2

    def test_declares_examined_on_failure(self, tmp_path: Path) -> None:
        _tree(tmp_path, {}, client=CLEAN_CLIENT.replace("def make_reader(", "def build_reader("))
        with patch("scripts.checks._common.ROOT", tmp_path), registry.outcome_scope("test_scope"):
            failed: list[str] = []
            topology.validate_recs_read_topology(failed)
            declaration = registry.pop_declaration()
        assert any(f.startswith("R1 ") for f in failed)
        assert declaration is not None and declaration.kind == "examined" and declaration.count == 1


class TestRegistration:
    def test_manifest_entry(self) -> None:
        entry = next(e for e in _manifest.ENTRIES if e.name == "validate_recs_read_topology")
        assert entry.pre is True
        assert entry.pre_globs is None
        assert entry.full_segment == "full_after_lint"

    def test_dispatched_in_both_tiers(self) -> None:
        assert "validate_recs_read_topology" in [s.name for s in registry.pre_sequence()]
        assert "validate_recs_read_topology" in [s.name for s in registry.full_sequence()]

    def test_taxonomy_row(self) -> None:
        taxonomy = yaml.safe_load((_common.ROOT / "config" / "ci_rca_taxonomy.yaml").read_text(encoding="utf-8"))
        assert taxonomy["function_to_category"]["validate_recs_read_topology"] == "code_regression"
