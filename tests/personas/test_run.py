"""The entry point: main's root first on sys.path, no snapshot path, and each verb's imports match its job's install."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

from scripts.personas import run

ROOT = Path(__file__).resolve().parents[2]
STDLIB = set(sys.stdlib_module_names)
ALLOWED = {
    "resolve_pr": {"yaml"},
    "route": {"yaml"},
    "snapshot": {"yaml"},
    "override": {"yaml"},
    "materialise": {"yaml"},
    "execute": {"jsonschema"},
    "post_verdict": {"yaml", "jsonschema"},
    "wake": {"yaml"},
    "egress_probe": set(),
    "egress_self_check": set(),
    "egress_control": {"yaml"},
}


def test_main_root_is_derived_from_the_file_and_goes_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "path", ["/elsewhere", str(ROOT)])
    monkeypatch.chdir(tmp_path)
    called: list[str] = []
    monkeypatch.setattr(
        run.importlib, "import_module", lambda name: type("M", (), {"main": staticmethod(lambda a: called.append(name) or 0)})
    )
    assert run.main(["post"]) == 0
    assert sys.path[0] == str(ROOT) and called == ["scripts.personas.post_verdict"]
    assert sys.path.count(str(ROOT)) == 1


def test_override_render_verb_dispatches_to_override_main(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    called: list[str] = []
    monkeypatch.setattr(
        run.importlib, "import_module", lambda name: type("M", (), {"main": staticmethod(lambda a: called.append(name) or 0)})
    )
    assert run.main(["override-render"]) == 0 and called == ["scripts.personas.override"]


def test_unknown_verb_exits_2() -> None:
    assert run.main(["nope"]) == 2
    assert run.main([]) == 2


def test_snapshot_on_the_path_exits_3_before_any_import(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    snap = tmp_path / "snap"
    (snap / "pkg").mkdir(parents=True)
    monkeypatch.setenv("PERSONA_SNAPSHOT", str(snap))
    monkeypatch.setattr(sys, "path", [str(ROOT), str(snap / "pkg")])
    monkeypatch.setattr(run.importlib, "import_module", lambda name: (_ for _ in ()).throw(AssertionError(name)))
    assert run.main(["resolve"]) == 3


def test_snapshot_not_on_the_path_is_fine(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    assert run.snapshot_on_path(["", "/usr/lib"], str(tmp_path / "snap")) is None
    assert run.snapshot_on_path(["/x"], None) is None
    assert run.snapshot_on_path([str(tmp_path / "snap")], str(tmp_path / "snap")) is not None


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def _closure(module: str, seen: set[str]) -> set[str]:
    external: set[str] = set()
    path = ROOT / (module.replace(".", "/") + ".py")
    if module in seen or not path.exists():
        return external
    seen.add(module)
    for name in _imports(path):
        top = name.split(".")[0]
        if top == "scripts":
            external |= _closure(name, seen)
        elif top not in STDLIB and top != "__future__":
            external.add(top)
    return external


@pytest.mark.parametrize("verb", sorted(ALLOWED))
def test_import_closure_matches_the_jobs_install(verb: str) -> None:
    assert _closure(f"scripts.personas.{verb}", set()) <= ALLOWED[verb]


def test_run_itself_is_stdlib_only_at_import() -> None:
    assert {n.split(".")[0] for n in _imports(ROOT / "scripts/personas/run.py")} <= STDLIB | {"__future__"}


@pytest.mark.parametrize(
    ("verb", "module"),
    [
        ("egress-probe", "egress_probe"),
        ("egress-self-check", "egress_self_check"),
        ("egress-control", "egress_control"),
    ],
)
def test_egress_verbs_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, verb: str, module: str) -> None:
    monkeypatch.chdir(tmp_path)
    called: list[str] = []
    monkeypatch.setattr(
        run.importlib, "import_module", lambda name: type("M", (), {"main": staticmethod(lambda a: called.append(name) or 0)})
    )
    assert run.main([verb]) == 0 and called == [f"scripts.personas.{module}"]
