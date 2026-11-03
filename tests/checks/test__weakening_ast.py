"""Mirror test for scripts/checks/_weakening_ast.py (PLAN-test-weakening-surfacing)."""

from __future__ import annotations

import ast

from scripts.checks import _weakening_ast as wa


class TestWeakeningAst:
    def test_decorator_skip_with_reason_kw(self) -> None:
        src = 'import pytest\n\n@pytest.mark.skip(reason="rec-1")\ndef test_a():\n    pass\n'
        sites = wa.skip_sites(src)
        assert len(sites) == 1
        assert sites[0] == wa.SkipSite(kind="pytest.mark.skip", line=3, reason="rec-1", scope="test_a")

    def test_bare_skip_decorator_has_empty_reason(self) -> None:
        src = "import pytest\n\n@pytest.mark.skip\ndef test_a():\n    pass\n"
        sites = wa.skip_sites(src)
        assert sites[0].reason == ""
        assert sites[0].kind == "pytest.mark.skip"

    def test_skipif_second_positional_is_reason(self) -> None:
        src = 'import pytest\n\n@pytest.mark.skipif(True, "env: ci")\ndef test_a():\n    pass\n'
        sites = wa.skip_sites(src)
        assert sites[0].reason == "env: ci"

    def test_xfail_mark(self) -> None:
        src = 'import pytest\n\n@pytest.mark.xfail(reason="rec-2")\ndef test_a():\n    pass\n'
        sites = wa.skip_sites(src)
        assert sites[0].kind == "pytest.mark.xfail"

    def test_module_level_pytestmark_is_scope_star(self) -> None:
        src = 'import pytest\npytestmark = pytest.mark.skip(reason="rec-3")\n\ndef test_a():\n    pass\n'
        sites = wa.skip_sites(src)
        assert len(sites) == 1
        assert sites[0].scope == "*"

    def test_class_level_pytestmark_is_scope_class(self) -> None:
        src = (
            'import pytest\n\nclass TestFoo:\n    pytestmark = pytest.mark.skip(reason="rec-4")\n\n'
            "    def test_a(self):\n        pass\n"
        )
        sites = wa.skip_sites(src)
        assert len(sites) == 1
        assert sites[0].scope == "TestFoo"

    def test_pytestmark_list_of_marks(self) -> None:
        src = (
            'import pytest\npytestmark = [pytest.mark.skip(reason="rec-5"), pytest.mark.xfail(reason="rec-6")]\n'
            "\ndef test_a():\n    pass\n"
        )
        sites = wa.skip_sites(src)
        assert {s.kind for s in sites} == {"pytest.mark.skip", "pytest.mark.xfail"}

    def test_alias_assignment_is_a_site(self) -> None:
        src = 'import pytest\nx = pytest.mark.skipif(True, reason="rec-7")\n'
        sites = wa.skip_sites(src)
        assert len(sites) == 1
        assert sites[0].kind == "pytest.mark.skipif"

    def test_param_marks_single(self) -> None:
        src = 'import pytest\np = pytest.param(1, marks=pytest.mark.skip(reason="rec-8"))\n'
        sites = wa.skip_sites(src)
        assert len(sites) == 1
        assert sites[0].kind == "pytest.mark.skip"

    def test_param_marks_list(self) -> None:
        src = (
            'import pytest\np = pytest.param(1, marks=[pytest.mark.skip(reason="rec-9"), '
            'pytest.mark.xfail(reason="rec-10")])\n'
        )
        sites = wa.skip_sites(src)
        assert {s.kind for s in sites} == {"pytest.mark.skip", "pytest.mark.xfail"}

    def test_add_marker_argument(self) -> None:
        src = 'import pytest\n\ndef test_a(request):\n    request.node.add_marker(pytest.mark.skip(reason="rec-11"))\n'
        sites = wa.skip_sites(src)
        assert len(sites) == 1
        assert sites[0].kind == "pytest.mark.skip"
        assert sites[0].scope == "test_a"

    def test_pytest_skip_call_inside_test(self) -> None:
        src = 'import pytest\n\ndef test_a():\n    pytest.skip("rec-12")\n'
        sites = wa.skip_sites(src)
        assert sites[0].kind == "pytest.skip"
        assert sites[0].reason == "rec-12"
        assert sites[0].scope == "test_a"

    def test_pytest_xfail_call(self) -> None:
        src = 'import pytest\n\ndef test_a():\n    pytest.xfail("rec-13")\n'
        sites = wa.skip_sites(src)
        assert sites[0].kind == "pytest.xfail"

    def test_importorskip_reason_kw(self) -> None:
        src = 'import pytest\npytest.importorskip("numpy", reason="no numpy")\n'
        sites = wa.skip_sites(src)
        assert sites[0].kind == "pytest.importorskip"
        assert sites[0].reason == "no numpy"

    def test_importorskip_third_positional_is_reason(self) -> None:
        src = 'import pytest\npytest.importorskip("numpy", None, "third positional reason")\n'
        sites = wa.skip_sites(src)
        assert sites[0].reason == "third positional reason"

    def test_importorskip_module_name_never_the_reason(self) -> None:
        src = 'import pytest\npytest.importorskip("numpy")\n'
        sites = wa.skip_sites(src)
        assert sites[0].reason == ""

    def test_self_skiptest_call(self) -> None:
        src = 'import unittest\n\nclass TestFoo(unittest.TestCase):\n    def test_a(self):\n        self.skipTest("rec-14")\n'
        sites = wa.skip_sites(src)
        assert sites[0].kind == "self.skipTest"
        assert sites[0].scope == "TestFoo::test_a"

    def test_unittest_skip_decorator(self) -> None:
        src = (
            "import unittest\n\nclass TestFoo(unittest.TestCase):\n"
            '    @unittest.skip("rec-15")\n    def test_a(self):\n        pass\n'
        )
        sites = wa.skip_sites(src)
        assert sites[0].kind == "unittest.skip"
        assert sites[0].reason == "rec-15"

    def test_unittest_skipif_second_positional(self) -> None:
        src = (
            "import unittest\n\nclass TestFoo(unittest.TestCase):\n"
            '    @unittest.skipIf(True, "rec-16")\n    def test_a(self):\n        pass\n'
        )
        sites = wa.skip_sites(src)
        assert sites[0].reason == "rec-16"

    def test_unittest_expected_failure_bare(self) -> None:
        src = (
            "import unittest\n\nclass TestFoo(unittest.TestCase):\n"
            "    @unittest.expectedFailure\n    def test_a(self):\n        pass\n"
        )
        sites = wa.skip_sites(src)
        assert sites[0].kind == "unittest.expectedFailure"
        assert sites[0].reason == ""

    def test_raise_unittest_skiptest(self) -> None:
        src = 'import unittest\n\ndef test_a():\n    raise unittest.SkipTest("rec-17")\n'
        sites = wa.skip_sites(src)
        assert sites[0].kind == "unittest.SkipTest"
        assert sites[0].reason == "rec-17"

    def test_raise_pytest_skip_exception(self) -> None:
        src = 'import pytest\n\ndef test_a():\n    raise pytest.skip.Exception("rec-18")\n'
        sites = wa.skip_sites(src)
        assert sites[0].kind == "pytest.skip.Exception"
        assert sites[0].reason == "rec-18"

    def test_import_aliases(self) -> None:
        src = (
            "import pytest as pt\nimport unittest as ut\nfrom pytest import skip as pskip\n"
            "from unittest import SkipTest as UST\n\n"
            'def test_a():\n    pt.skip("rec-19")\n\n'
            'def test_b():\n    pskip("rec-20")\n\n'
            'def test_c():\n    raise UST("rec-21")\n'
        )
        sites = wa.skip_sites(src)
        kinds = {s.kind for s in sites}
        assert kinds == {"pytest.skip", "unittest.SkipTest"}
        reasons = {s.reason for s in sites}
        assert reasons == {"rec-19", "rec-20", "rec-21"}

    def test_fstring_reason_contributes_constant_parts(self) -> None:
        src = 'import pytest\n\ndef test_a():\n    pytest.skip(f"rec-{22} fixed")\n'
        sites = wa.skip_sites(src)
        assert sites[0].reason == "rec- fixed"

    def test_unparseable_source_yields_empty(self) -> None:
        assert wa.skip_sites("def f(:\n") == []
        assert wa.test_functions("def f(:\n") == {}
        assert wa.numeric_constants("def f(:\n") == {}

    def test_applicable_skip_sites_three_scopes(self) -> None:
        src = (
            'import pytest\npytestmark = pytest.mark.skip(reason="rec-23")\n\n'
            'class TestFoo:\n    pytestmark = pytest.mark.skip(reason="rec-24")\n\n'
            '    def test_a(self):\n        pytest.skip("rec-25")\n'
        )
        applicable = wa.applicable_skip_sites(src, "TestFoo::test_a")
        kinds = {s.reason for s in applicable}
        assert kinds == {"rec-23", "rec-24", "rec-25"}
        unrelated = wa.applicable_skip_sites(src, "TestBar::test_z")
        assert {s.reason for s in unrelated} == {"rec-23"}

    def test_added_skip_sites_multiset(self) -> None:
        base = "import pytest\n\ndef test_a():\n    pass\n"
        head = 'import pytest\n\n@pytest.mark.skip(reason="rec-26")\ndef test_a():\n    pass\n'
        added = wa.added_skip_sites({"t.py": (base, head)})
        assert "t.py" in added
        assert len(added["t.py"]) == 1
        assert added["t.py"][0].reason == "rec-26"

    def test_added_skip_sites_untracked_file_has_no_base(self) -> None:
        head = 'import pytest\n\n@pytest.mark.skip(reason="rec-27")\ndef test_a():\n    pass\n'
        added = wa.added_skip_sites({"new.py": (None, head)})
        assert len(added["new.py"]) == 1

    def test_added_skip_sites_rename_carries_no_added_site(self) -> None:
        """A skip moved with its test from a deleted file into a brand-new file carries -- the
        scope is new to the destination, but the site is a rename, not an addition."""
        old = 'import pytest\n\n@pytest.mark.skip(reason="rec-28")\ndef test_a():\n    pass\n'
        new = 'import pytest\n\n@pytest.mark.skip(reason="rec-28")\ndef test_a():\n    pass\n'
        added = wa.added_skip_sites({"old.py": (old, None), "new.py": (None, new)})
        assert "new.py" not in added

    def test_added_skip_sites_carried_onto_existing_scope_is_added(self) -> None:
        """A module-level skip carried into a PRE-EXISTING test file (the destination scope
        already existed at base) is added, not carried."""
        old = 'import pytest\npytestmark = pytest.mark.skip(reason="rec-29")\n'
        dest_base = "import pytest\n\ndef test_a():\n    pass\n"
        dest_head = 'import pytest\npytestmark = pytest.mark.skip(reason="rec-29")\n\ndef test_a():\n    pass\n'
        added = wa.added_skip_sites({"old.py": (old, None), "dest.py": (dest_base, dest_head)})
        assert "dest.py" in added

    def test_assertions_counts_assert_and_matching_calls(self) -> None:
        src = "def test_x():\n    assert a == 1\n    b.approx(c, rel=0.1)\n"
        node = ast.parse(src).body[0]
        counts = wa.assertions(node)
        assert sum(counts.values()) >= 2

    def test_tolerances_extracts_keyword_values(self) -> None:
        src = "def test_x():\n    a.approx(b, rel=0.1)\n    c.isclose(d, atol=-2)\n"
        node = ast.parse(src).body[0]
        tol = wa.tolerances(node)
        assert tol[("approx", "rel", 0)] == 0.1
        assert tol[("isclose", "atol", 0)] == -2.0

    def test_numeric_constants_unary_minus_and_bool_excluded(self) -> None:
        consts = wa.numeric_constants("X = 5\nY = -2.5\nBOOL = True\nz = 10\n")
        assert consts == {"X": 5.0, "Y": -2.5}

    def test_test_functions_module_and_class_level(self) -> None:
        src = (
            "def test_a():\n    pass\n\nclass TestFoo:\n    def test_b(self):\n        pass\n\n"
            "class Other:\n    def test_c(self):\n        pass\n"
        )
        found = wa.test_functions(src)
        assert set(found) == {"test_a", "TestFoo::test_b"}


class TestWeakeningAstEdgeCases:
    """Direct unit coverage of the small internal helpers' own defensive branches -- unreachable
    or hard to trigger via skip_sites()'s public surface alone."""

    def test_imports_resolve_empty_chain_is_none(self) -> None:
        assert wa._Imports().resolve([]) is None

    def test_imports_resolve_unknown_head_is_none(self) -> None:
        assert wa._Imports().resolve(["os", "path"]) is None

    def test_dotted_chain_non_name_base_is_none(self) -> None:
        import ast

        tree = ast.parse("foo().bar\n")
        attr = tree.body[0].value
        assert wa._dotted_chain(attr) is None

    def test_kind_from_resolved_unknown_root_is_none(self) -> None:
        assert wa._kind_from_resolved(("other", ["x"])) is None

    def test_kind_from_resolved_unknown_pytest_rest_is_none(self) -> None:
        assert wa._kind_from_resolved(("pytest", ["unknown"])) is None

    def test_kind_from_resolved_unknown_unittest_rest_is_none(self) -> None:
        assert wa._kind_from_resolved(("unittest", ["unknown"])) is None

    def test_const_str_none_node(self) -> None:
        assert wa._const_str(None) == ""

    def test_const_str_unsupported_node_type(self) -> None:
        import ast

        node = ast.parse("x\n").body[0].value
        assert wa._const_str(node) == ""

    def test_reason_from_call_none_call(self) -> None:
        assert wa._reason_from_call(None, "pytest.skip") == ""

    def test_unittest_skip_unless_decorator(self) -> None:
        src = (
            "import unittest\n\nclass TestFoo(unittest.TestCase):\n"
            '    @unittest.skipUnless(False, "rec-30")\n    def test_a(self):\n        pass\n'
        )
        sites = wa.skip_sites(src)
        assert sites[0].kind == "unittest.skipUnless"
        assert sites[0].reason == "rec-30"

    def test_unrelated_call_attribute_and_raise_are_not_sites(self) -> None:
        """Lines whose chain resolves (or fails to) but names no pytest/unittest skip API --
        exercises every early-return branch in _handle_call/_handle_bare_attribute/_handle_raise."""
        src = (
            "import os\n\n"
            "def helper():\n"
            "    return (lambda: None)()\n\n"
            "y = os.path.join('a', 'b')\n"
            "z = os.path.sep\n\n"
            "def test_a():\n"
            "    try:\n"
            "        pass\n"
            "    except Exception:\n"
            "        raise os.error()\n\n"
            "import unittest\n\n"
            "def test_b():\n"
            "    raise unittest.TestCase()\n\n"
            "def test_c():\n"
            "    raise (lambda: Exception())()()\n"
        )
        assert wa.skip_sites(src) == []

    def test_known_scopes_at_none_source(self) -> None:
        assert wa._known_scopes_at(None) == {"*"}

    def test_known_scopes_at_unparseable_source(self) -> None:
        assert wa._known_scopes_at("def f(:\n") == {"*"}

    def test_added_skip_sites_no_added_sites_is_omitted(self) -> None:
        src = "def test_a():\n    pass\n"
        added = wa.added_skip_sites({"t.py": (src, src)})
        assert added == {}

    def test_added_skip_sites_deleted_file_has_no_head_entry(self) -> None:
        base = 'import pytest\n\n@pytest.mark.skip(reason="rec-1")\ndef test_a():\n    pass\n'
        added = wa.added_skip_sites({"gone.py": (base, None)})
        assert "gone.py" not in added

    def test_numeric_constants_ann_assign(self) -> None:
        consts = wa.numeric_constants("X: int = 5\n")
        assert consts == {"X": 5.0}

    def test_tolerances_unrelated_callee_skipped(self) -> None:
        import ast

        node = ast.parse("def test_a():\n    foo.bar(rel=0.1)\n").body[0]
        assert wa.tolerances(node) == {}

    def test_tolerances_non_numeric_keyword_skipped(self) -> None:
        import ast

        node = ast.parse("def test_a():\n    x.approx(y, rel='not-a-number')\n").body[0]
        assert wa.tolerances(node) == {}

    def test_scope_of_non_test_module_level_function_is_star(self) -> None:
        src = 'import pytest\n\ndef helper():\n    pytest.skip("rec-31")\n'
        sites = wa.skip_sites(src)
        assert sites[0].scope == "*"

    def test_record_if_mark_unresolvable_func_is_ignored(self) -> None:
        src = "x = (lambda: None)()\n"
        assert wa.skip_sites(src) == []

    def test_bare_attribute_on_non_name_base_is_ignored(self) -> None:
        src = "def f():\n    return 1\n\nf().attr\n"
        assert wa.skip_sites(src) == []

    def test_known_scopes_at_class_with_methods(self) -> None:
        src = "class TestFoo:\n    def test_a(self):\n        pass\n"
        assert wa._known_scopes_at(src) == {"*", "TestFoo", "TestFoo::test_a"}

    def test_added_skip_sites_exhausted_multiset_entry_is_skipped(self) -> None:
        base = 'import pytest\npytestmark = pytest.mark.skip(reason="rec-1")\n'
        head = 'import pytest\npytestmark = [pytest.mark.skip(reason="rec-1"), pytest.mark.skip(reason="rec-1")]\n'
        added = wa.added_skip_sites({"t.py": (base, head)})
        assert len(added["t.py"]) == 1

    def test_call_display_name_bare_name(self) -> None:
        import ast

        node = ast.parse("def test_a():\n    approx(x, rel=0.1)\n").body[0]
        assert wa.assertions(node) != {}

    def test_call_display_name_neither_name_nor_attribute(self) -> None:
        import ast

        node = ast.parse("def test_a():\n    funcs[0](x)\n").body[0]
        assert wa.assertions(node) == {}
