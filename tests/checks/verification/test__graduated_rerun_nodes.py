"""Mirror of scripts/checks/verification/_graduated_rerun_nodes.py (static pytest node-id resolution)."""

from pathlib import Path

from scripts.checks.verification import _graduated_rerun_nodes as nodes

SAMPLE = """\
import pytest


def test_plain():
    pass


class TestOuter:
    def test_method(self):
        pass

    class TestInner:
        def test_deep(self):
            pass


@pytest.mark.integration
def test_marked():
    pass


@pytest.mark.integration
class TestMarkedClass:
    def test_in_marked(self):
        pass
"""


def _tree(tmp_path: Path, body: str = SAMPLE) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(body, encoding="utf-8")
    return tmp_path


class TestResolve:
    def test_function_class_nested_and_param(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        ids = [
            "tests/test_x.py::test_plain",
            "tests/test_x.py::TestOuter::test_method",
            "tests/test_x.py::TestOuter::TestInner::test_deep",
            "tests/test_x.py::test_plain[a-b]",
        ]
        assert nodes.unresolved_nodes(ids, root) == []

    def test_bare_file_and_directory(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        assert nodes.unresolved_nodes(["tests/test_x.py", "tests"], root) == []

    def test_missing_file_unresolved(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        assert nodes.unresolved_nodes(["tests/nope.py::test_a"], root) == ["tests/nope.py::test_a"]

    def test_missing_function_unresolved(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        missing = nodes.unresolved_nodes(["tests/test_x.py::test_gone", "tests/test_x.py::TestOuter::test_gone"], root)
        assert missing == ["tests/test_x.py::test_gone", "tests/test_x.py::TestOuter::test_gone"]

    def test_flags_and_expressions_skipped(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        args = ["-q", "-k", "not a_node", "-m", "not integration", "--tb=short", "tests/test_x.py::test_plain"]
        assert nodes.node_arguments(args) == ["tests/test_x.py::test_plain"]
        assert nodes.unresolved_nodes(args, root) == []

    def test_foreign_base_class_is_lenient_local_base_is_checked(self, tmp_path: Path) -> None:
        body = "class Base:\n    def test_a(self): pass\n\nclass TestA(Base):\n    pass\n\nclass TestB(Other):\n    pass\n"
        root = _tree(tmp_path, body)
        assert nodes.unresolved_nodes(["tests/test_x.py::TestA::test_a", "tests/test_x.py::TestB::test_any"], root) == []
        assert nodes.unresolved_nodes(["tests/test_x.py::TestA::test_gone"], root) == ["tests/test_x.py::TestA::test_gone"]

    def test_integration_marker_detected(self, tmp_path: Path) -> None:
        root = _tree(tmp_path)
        assert nodes.can_select_integration(["tests/test_x.py::test_marked"], root)
        assert nodes.can_select_integration(["tests/test_x.py::TestMarkedClass::test_in_marked"], root)
        assert nodes.can_select_integration(["tests/test_x.py", "-k", "something"], root)
        assert nodes.can_select_integration(["tests"], root)
        assert not nodes.can_select_integration(["tests/test_x.py::test_plain"], root)
        assert not nodes.can_select_integration(["tests/test_x.py::TestOuter::test_method"], root)

    def test_module_pytestmark_marks_every_node(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "import pytest\npytestmark = pytest.mark.integration\n\ndef test_a(): pass\n")
        assert nodes.can_select_integration(["tests/test_x.py::test_a"], root)


class TestEdges:
    def test_unparseable_file_is_unresolved_and_not_integration(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "def broken(:\n")
        assert nodes.unresolved_nodes(["tests/test_x.py::test_a"], root) == ["tests/test_x.py::test_a"]
        assert not nodes.can_select_integration(["tests/test_x.py::test_a", "tests/test_x.py", "tests"], root)

    def test_missing_target_cannot_select_integration(self, tmp_path: Path) -> None:
        assert not nodes.can_select_integration(["nowhere.py"], _tree(tmp_path))

    def test_marker_shapes(self, tmp_path: Path) -> None:
        body = "import pytest\npytestmark = [pytest.mark.slow, pytest.mark.integration]\nx = 1\ndef test_a(): pass\n"
        assert nodes.can_select_integration(["tests/test_x.py::test_a"], _tree(tmp_path, body))

    def test_class_level_pytestmark_and_call_decorator(self, tmp_path: Path) -> None:
        body = (
            "import pytest\n"
            "class TestA:\n"
            "    other = 1\n"
            "    pytestmark = pytest.mark.integration\n"
            "    def test_m(self): pass\n"
            "class TestB:\n"
            "    pytestmark = pytest.mark.slow\n"
            "    @pytest.mark.integration()\n"
            "    def test_n(self): pass\n"
            "    def test_o(self): pass\n"
        )
        root = _tree(tmp_path, body)
        assert nodes.can_select_integration(["tests/test_x.py::TestA::test_m"], root)
        assert nodes.can_select_integration(["tests/test_x.py::TestB::test_n"], root)
        assert not nodes.can_select_integration(["tests/test_x.py::TestB::test_o"], root)
        assert nodes.can_select_integration(["tests/test_x.py"], root)

    def test_unmarked_bare_file_and_missing_member_in_marker_walk(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "pytestmark = 5\nx = 1\nclass TestA:\n    y = 1\n    def test_m(self): pass\n")
        assert not nodes.can_select_integration(["tests/test_x.py"], root)
        assert not nodes.can_select_integration(["tests/test_x.py::TestA::test_gone"], root)

    def test_inherited_through_local_base_chain(self, tmp_path: Path) -> None:
        body = (
            "class Root:\n    def test_r(self): pass\n"
            "class Mid(Root):\n    pass\n"
            "class Leaf(Mid):\n    pass\n"
            "class Ext(Foreign):\n    pass\n"
            "class Wrapped(Ext):\n    pass\n"
            "def test_fn(): pass\n"
        )
        root = _tree(tmp_path, body)
        assert nodes.unresolved_nodes(["tests/test_x.py::Leaf::test_r", "tests/test_x.py::Wrapped::test_any"], root) == []
        assert nodes.unresolved_nodes(["tests/test_x.py::Leaf::test_gone", "tests/test_x.py::test_fn::inner"], root) == [
            "tests/test_x.py::Leaf::test_gone",
            "tests/test_x.py::test_fn::inner",
        ]

    def test_module_mark_on_bare_file_and_trivial_base(self, tmp_path: Path) -> None:
        root = _tree(tmp_path, "import pytest\npytestmark = pytest.mark.integration\n")
        assert nodes.can_select_integration(["tests/test_x.py"], root)
        body = "import unittest\nclass TestA(unittest.TestCase):\n    def test_m(self): pass\n"
        (tmp_path / "second").mkdir()
        root2 = _tree(tmp_path / "second", body)
        assert nodes.unresolved_nodes(["tests/test_x.py::TestA::test_gone"], root2) == ["tests/test_x.py::TestA::test_gone"]
