"""TestDataLakeLifecycleGoverned (PLAN-ducklake-noncurrent-version-reclaim, T2.18) replaces this
module's former no-lifecycle-at-all invariant (which asserted "no lifecycle configuration on the
data-lake bucket at all" when the property meant was "no CURRENT-version expiration") with a
net-tightened guard: it RETAINS the predecessor's directory-wide sweep of every
terraform/personal/*.tf for every aws_s3_bucket_lifecycle_configuration block referencing
aws_s3_bucket.data_lake, whatever its resource name, and ADDS cardinality, prefix-scope,
trailing-slash, declared-coverage, SNAPSHOT_RETAIN_DAYS-binding and status assertions
(Decision 181). TestDataLakeLifecycleRedCases proves the guard can actually fail.

PLAN-smoke-prefix-lifecycle-coverage (T2.18) extends coverage to the DuckLake smoke prefix (a
SECOND rule on the same resource, never a rewrite of the production rule) and reshapes every
assertion from whole-resource-block matching to PER-RULE matching, since a second rule with a
wrong value would otherwise pass undetected under the predecessor's first-match-only guards. The
GC/alarm concern (TestProductionGcRuleInvariants and siblings) moved out to
tests/checks/iam_tf/test_ducklake_maintenance_gc_rule_invariants.py to keep both modules under the
SLOC budget by decomposition (Decision 128/130), never by a budget raise. Shared HCL-text helpers
live in tests/fixtures/terraform_hcl_blocks.py.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.fixtures.terraform_hcl_blocks import (
    _TERRAFORM_PERSONAL_DIR,
    assert_every_declared_prefix_covered,
    assert_every_rule_enabled,
    assert_every_rule_has_expired_object_delete_marker,
    assert_every_rule_noncurrent_days,
    assert_every_rule_prefix_derived_with_trailing_slash,
    find_resource_block,
    split_rule_blocks,
    tf_dir_file_texts,
)

_LIFECYCLE_CONFIG_RE = re.compile(r'resource\s+"aws_s3_bucket_lifecycle_configuration"\s+"(\w+)"\s*\{')


def _data_lake_lifecycle_blocks_from_texts(file_texts: dict[str, str]) -> list[tuple[str, str]]:
    """(filename, block_text) for every lifecycle-config block, in any file, referencing
    aws_s3_bucket.data_lake -- whatever its resource name (Decision 181 breadth)."""
    found: list[tuple[str, str]] = []
    for filename, text in file_texts.items():
        for match in _LIFECYCLE_CONFIG_RE.finditer(text):
            block = find_resource_block(text, "aws_s3_bucket_lifecycle_configuration", match.group(1))
            if "aws_s3_bucket.data_lake" in block:
                found.append((filename, block))
    return found


def _assert_exactly_one_data_lake_lifecycle_block(file_texts: dict[str, str]) -> list[tuple[str, str]]:
    """The one governed block -- asserts cardinality itself, not only via the dedicated test."""
    matches = _data_lake_lifecycle_blocks_from_texts(file_texts)
    assert len(matches) == 1, (
        f"expected exactly one aws_s3_bucket_lifecycle_configuration targeting "
        f"aws_s3_bucket.data_lake, found {len(matches)}: {[name for name, _ in matches]}"
    )
    return matches


def _single_data_lake_lifecycle_block(file_texts: dict[str, str]) -> str:
    return _assert_exactly_one_data_lake_lifecycle_block(file_texts)[0][1]


_DATA_PREFIX_LOCAL_RE = re.compile(r'\b(\w+_data_prefix)\s*=\s*"([^"]+)"')


def _declared_data_prefix_locals(file_texts: dict[str, str]) -> dict[str, str]:
    """{local_name: prefix_value}, DERIVED from every *_data_prefix local -- never hand-authored
    (a hand-written roster lets a future third prefix pass the coverage sweep vacuously)."""
    roster: dict[str, str] = {}
    for text in file_texts.values():
        for match in _DATA_PREFIX_LOCAL_RE.finditer(text):
            roster[match.group(1)] = match.group(2)
    return roster


# A future *_data_prefix local with no entry here fails the coverage test, never passes vacuously.
# PLAN-smoke-prefix-lifecycle-coverage retired the ducklake_smoke_data_prefix entry that used to
# live here: the smoke prefix is now positively covered by a second rule, not declared-excluded.
_DECLARED_LIFECYCLE_EXCLUSIONS: dict[str, str] = {}

# `\b` cannot match inside "noncurrent_version_expiration" (no boundary between two word chars),
# so the negative lookbehind is a belt-and-suspenders duplicate of that same exclusion.
_CURRENT_VERSION_EXPIRATION_RE = re.compile(r"(?<!noncurrent_version_)\bexpiration\s*\{([^}]*)\}", re.DOTALL)
_CURRENT_VERSION_EXPIRATION_AGE_RE = re.compile(r"\b(?:days|date)\s*=")
_GOVERNED_PREFIX_RE = re.compile(r'prefix\s*=\s*"\$\{local\.ducklake_prod_data_prefix\}/"')


def _assert_no_current_version_expiration_rule(block: str) -> None:
    """An age rule here would delete live Parquet the catalog still references. Already
    per-rule: the regex scans the WHOLE resource text, so a current-version expiration age
    argument on ANY rule (not merely the first) trips it."""
    for body in _CURRENT_VERSION_EXPIRATION_RE.findall(block):
        assert not _CURRENT_VERSION_EXPIRATION_AGE_RE.search(body), (
            f"expiration block carries a days/date argument ({body.strip()!r}) -- this deletes "
            "live Parquet the catalog still references (Decision 55/188)"
        )


_DEPLOY_RECORDS_PREFIX = "deploy-records/"
_LOCAL_REF_RE = re.compile(r"^\$\{local\.(\w+)\}(.*)$", re.DOTALL)
_RULE_PREFIX_RE = re.compile(r'\bprefix\s*=\s*"([^"]*)"')
_LOCAL_VALUE_RE = r'\b{name}\s*=\s*"([^"]*)"'


def _data_lake_versioning_block(file_texts: dict[str, str]) -> str:
    for text in file_texts.values():
        for match in re.finditer(r'resource\s+"aws_s3_bucket_versioning"\s+"(\w+)"\s*\{', text):
            block = find_resource_block(text, "aws_s3_bucket_versioning", match.group(1))
            if "aws_s3_bucket.data_lake" in block:
                return block
    return ""


def _resolve_rule_prefix(raw: str, file_texts: dict[str, str]) -> str:
    ref = _LOCAL_REF_RE.match(raw)
    if ref is None:
        return raw
    for text in file_texts.values():
        found = re.search(_LOCAL_VALUE_RE.format(name=re.escape(ref.group(1))), text)
        if found:
            return found.group(1) + ref.group(2)
    raise AssertionError(f"lifecycle prefix {raw!r} does not resolve to a declared local -- failing closed")


def _assert_deploy_record_history_unreclaimed(lifecycle_block: str, versioning_block: str, file_texts: dict[str, str]) -> None:
    assert re.search(r'\bstatus\s*=\s*"Enabled"', versioning_block), (
        "data_lake bucket versioning is not Enabled -- the deploy history would stop accruing"
    )
    for rule in split_rule_blocks(lifecycle_block):
        found = _RULE_PREFIX_RE.search(rule)
        assert found is not None and found.group(1), (
            f"lifecycle rule has no filter prefix (bucket-wide) and would expire deploy-records/ versions: {rule[:200]!r}"
        )
        resolved = _resolve_rule_prefix(found.group(1), file_texts)
        assert not (_DEPLOY_RECORDS_PREFIX.startswith(resolved) or resolved.startswith(_DEPLOY_RECORDS_PREFIX)), (
            f"lifecycle rule prefix {resolved!r} reaches deploy-records/, the append-only deploy history"
        )


_GOOD_VERSIONING_TF = """
resource "aws_s3_bucket_versioning" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id
  versioning_configuration {
    status = "Enabled"
  }
}
"""
_LOCALS_TF = 'locals {\n  ducklake_prod_data_prefix = "ducklake"\n  deploy_data_prefix = "deploy-records"\n}\n'


def _history_case(lifecycle_tf: str, versioning_tf: str = _GOOD_VERSIONING_TF) -> None:
    texts = {"main.tf": lifecycle_tf + versioning_tf + _LOCALS_TF}
    block = _single_data_lake_lifecycle_block(texts)
    _assert_deploy_record_history_unreclaimed(block, _data_lake_versioning_block(texts), texts)


class TestDataLakeLifecycleGoverned:
    """PLAN-ducklake-noncurrent-version-reclaim / PLAN-smoke-prefix-lifecycle-coverage:
    net-tightened, per-rule guard (Decision 181)."""

    def test_exactly_one_lifecycle_configuration_targets_the_data_lake_bucket(self) -> None:
        """Directory-wide: a second config added to ANY OTHER FILE must still be caught."""
        _assert_exactly_one_data_lake_lifecycle_block(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR))

    def test_read_helper_sweeps_every_tf_file_in_the_directory(self, tmp_path: Path) -> None:
        """BEHAVIORAL, not a source-text scan: a hardcoded single-file read would fail here."""
        (tmp_path / "main.tf").write_text('resource "x" "y" {}\n', encoding="utf-8")
        (tmp_path / "other.tf").write_text('resource "x" "z" {}\n', encoding="utf-8")
        (tmp_path / "not_tf.txt").write_text("ignored\n", encoding="utf-8")
        file_texts = tf_dir_file_texts(tmp_path)
        assert set(file_texts) == {"main.tf", "other.tf"}, (
            f"expected every .tf file in the directory to be read, got {sorted(file_texts)} -- a "
            "main.tf-only guard would be a weakening (Decision 181)"
        )

    def test_rule_status_is_enabled(self) -> None:
        """A Disabled rule satisfies every other assertion here while reclaiming nothing. PER-RULE:
        a second rule left Disabled would otherwise slip through a first-match-only guard."""
        block = _single_data_lake_lifecycle_block(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR))
        assert_every_rule_enabled(split_rule_blocks(block))

    def test_prefix_is_derived_from_the_declared_local_with_a_trailing_slash(self) -> None:
        """PER-RULE: every rule's prefix must derive from a declared *_data_prefix local with a
        trailing slash -- a retyped literal or a missing slash on ANY rule would also match a
        sibling prefix (Decision 191)."""
        block = _single_data_lake_lifecycle_block(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR))
        assert_every_rule_prefix_derived_with_trailing_slash(split_rule_blocks(block))

    def test_no_current_version_expiration_rule(self) -> None:
        block = _single_data_lake_lifecycle_block(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR))
        _assert_no_current_version_expiration_rule(block)

    def test_expired_object_delete_marker_is_present(self) -> None:
        """PER-RULE: rec-3907's acceptance criterion 1 second half, now asserted on every rule."""
        block = _single_data_lake_lifecycle_block(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR))
        assert_every_rule_has_expired_object_delete_marker(split_rule_blocks(block))

    def test_noncurrent_days_bound_to_snapshot_retain_days(self) -> None:
        """PER-RULE (the load-bearing reshape): the predecessor guard re.searched only the FIRST
        noncurrent_version_expiration block, so a second rule carrying a wrong value would pass
        undetected. Deliberately function-local import: the rest of this module reads terraform
        text and needs no first-party runtime import, so hoisting this would couple collection of
        every test here to src.common's own import chain."""
        from src.common.ducklake_maintenance import SNAPSHOT_RETAIN_DAYS

        block = _single_data_lake_lifecycle_block(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR))
        assert_every_rule_noncurrent_days(split_rule_blocks(block), SNAPSHOT_RETAIN_DAYS)

    def test_every_declared_data_prefix_local_is_covered_or_declared_excluded(self) -> None:
        file_texts = tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)
        block = _single_data_lake_lifecycle_block(file_texts)
        roster = _declared_data_prefix_locals(file_texts)
        assert roster, "no *_data_prefix local found -- roster derivation is broken"
        assert_every_declared_prefix_covered(roster, block, _DECLARED_LIFECYCLE_EXCLUSIONS)

    def test_smoke_prefix_is_covered_not_excluded(self) -> None:
        """rec-3945: now that rec-3892's unexplained-deleter RCA is resolved, the smoke prefix is
        POSITIVELY covered by a second lifecycle rule, never passed through a declared exclusion."""
        assert "ducklake_smoke_data_prefix" not in _DECLARED_LIFECYCLE_EXCLUSIONS, (
            "the smoke prefix is still a declared exclusion -- the coverage sweep would pass "
            "vacuously instead of proving positive coverage"
        )
        block = _single_data_lake_lifecycle_block(tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR))
        assert "local.ducklake_smoke_data_prefix" in block, (
            "the smoke prefix local is not referenced by any rule in the governed lifecycle block"
        )

    def test_no_rule_reclaims_deploy_record_history(self) -> None:
        """The versions under deploy-records/ are the append-only deploy history (build-lambda.yaml
        deploy_record.history); versioning must stay Enabled and no rule may reach that prefix."""
        file_texts = tf_dir_file_texts(_TERRAFORM_PERSONAL_DIR)
        _assert_deploy_record_history_unreclaimed(
            _single_data_lake_lifecycle_block(file_texts), _data_lake_versioning_block(file_texts), file_texts
        )
        good = _GOOD_DATA_LAKE_LIFECYCLE_TF
        _history_case(good)
        red_cases = {
            "literal deploy-records prefix": (
                good.replace('"${local.ducklake_prod_data_prefix}/"', '"deploy-records/"'),
                _GOOD_VERSIONING_TF,
            ),
            "local resolving to deploy-records": (
                good.replace("ducklake_prod_data_prefix", "deploy_data_prefix"),
                _GOOD_VERSIONING_TF,
            ),
            "empty filter": (good.replace('prefix = "${local.ducklake_prod_data_prefix}/"', ""), _GOOD_VERSIONING_TF),
            "unresolvable local": (good.replace("ducklake_prod_data_prefix", "missing_local"), _GOOD_VERSIONING_TF),
            "suspended versioning": (good, _GOOD_VERSIONING_TF.replace("Enabled", "Suspended")),
        }
        for lifecycle_tf, versioning_tf in red_cases.values():
            with pytest.raises(AssertionError):
                _history_case(lifecycle_tf, versioning_tf)


# Red-case fixtures proving the guard above actually discriminates ("a guard that cannot fail is
# not a guard" -- the plan's VP step 5 fix_if). Each reshaped-guard red case invokes the real
# module-level predicate under pytest.raises(AssertionError), rather than merely re-asserting that
# its own fixture text was mutated.

_GOOD_DATA_LAKE_LIFECYCLE_TF = """
resource "aws_s3_bucket_lifecycle_configuration" "data_lake" {
  bucket = aws_s3_bucket.data_lake.id

  rule {
    id     = "ducklake-prod-noncurrent-reclaim"
    status = "Enabled"

    filter {
      prefix = "${local.ducklake_prod_data_prefix}/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
"""

_PROD_RULE_TF = """
  rule {
    id     = "ducklake-prod-noncurrent-reclaim"
    status = "Enabled"

    filter {
      prefix = "${local.ducklake_prod_data_prefix}/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
"""

_SMOKE_RULE_TF = """
  rule {
    id     = "ducklake-smoke-noncurrent-reclaim"
    status = "Enabled"

    filter {
      prefix = "${local.ducklake_smoke_data_prefix}/"
    }

    noncurrent_version_expiration {
      noncurrent_days = 30
    }

    expiration {
      expired_object_delete_marker = true
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
"""


def _two_rule_lifecycle_tf(smoke_rule: str = _SMOKE_RULE_TF) -> str:
    return (
        'resource "aws_s3_bucket_lifecycle_configuration" "data_lake" {\n'
        "  bucket = aws_s3_bucket.data_lake.id\n"
        f"{_PROD_RULE_TF}\n{smoke_rule}"
        "}\n"
    )


class TestDataLakeLifecycleRedCases:
    def test_current_version_expiration_rule_trips(self) -> None:
        """(a) An age-based `days` argument added to the rule's `expiration` block must trip."""
        tf = _GOOD_DATA_LAKE_LIFECYCLE_TF.replace(
            "expiration {\n      expired_object_delete_marker = true\n    }",
            "expiration {\n      expired_object_delete_marker = true\n      days                        = 45\n    }",
        )
        block = _single_data_lake_lifecycle_block({"main.tf": tf})
        with pytest.raises(AssertionError):
            _assert_no_current_version_expiration_rule(block)

    def test_slashless_prefix_trips(self) -> None:
        """(b) A bare slashless prefix must trip -- it would also match the sibling smoke prefix."""
        tf = _GOOD_DATA_LAKE_LIFECYCLE_TF.replace(
            'prefix = "${local.ducklake_prod_data_prefix}/"', 'prefix = "${local.ducklake_prod_data_prefix}"'
        )
        block = _single_data_lake_lifecycle_block({"main.tf": tf})
        with pytest.raises(AssertionError):
            assert_every_rule_prefix_derived_with_trailing_slash(split_rule_blocks(block))

    def test_disabled_status_trips(self) -> None:
        """(c) A Disabled rule satisfies every other assertion while reclaiming nothing."""
        tf = _GOOD_DATA_LAKE_LIFECYCLE_TF.replace('status = "Enabled"', 'status = "Disabled"')
        block = _single_data_lake_lifecycle_block({"main.tf": tf})
        with pytest.raises(AssertionError):
            assert_every_rule_enabled(split_rule_blocks(block))

    def test_second_lifecycle_config_in_another_file_trips_the_cardinality_sweep(self) -> None:
        """(d) A second config in a DIFFERENT file under a DIFFERENT resource name must still trip."""
        file_texts = {
            "main.tf": _GOOD_DATA_LAKE_LIFECYCLE_TF,
            "rogue.tf": _GOOD_DATA_LAKE_LIFECYCLE_TF.replace(
                'resource "aws_s3_bucket_lifecycle_configuration" "data_lake"',
                'resource "aws_s3_bucket_lifecycle_configuration" "data_lake_rogue"',
            ),
        }
        with pytest.raises(AssertionError):
            _assert_exactly_one_data_lake_lifecycle_block(file_texts)

    def test_second_rule_with_wrong_noncurrent_days_fails(self) -> None:
        """VP step 5: a two-rule configuration whose SECOND rule carries a wrong noncurrent_days
        fails the per-rule guard -- under the predecessor's first-match-only assertion, this
        fixture passed."""
        smoke_rule = _SMOKE_RULE_TF.replace("noncurrent_days = 30", "noncurrent_days = 7")
        block = _single_data_lake_lifecycle_block({"main.tf": _two_rule_lifecycle_tf(smoke_rule)})
        rules = split_rule_blocks(block)
        assert len(rules) == 2, "fixture does not actually carry two rules"
        with pytest.raises(AssertionError):
            assert_every_rule_noncurrent_days(rules, 30)

    def test_second_rule_disabled_fails(self) -> None:
        smoke_rule = _SMOKE_RULE_TF.replace('status = "Enabled"', 'status = "Disabled"')
        block = _single_data_lake_lifecycle_block({"main.tf": _two_rule_lifecycle_tf(smoke_rule)})
        rules = split_rule_blocks(block)
        assert len(rules) == 2, "fixture does not actually carry two rules"
        with pytest.raises(AssertionError):
            assert_every_rule_enabled(rules)

    def test_second_rule_missing_expired_object_delete_marker_fails(self) -> None:
        smoke_rule = _SMOKE_RULE_TF.replace(
            "expiration {\n      expired_object_delete_marker = true\n    }", "expiration {\n    }"
        )
        block = _single_data_lake_lifecycle_block({"main.tf": _two_rule_lifecycle_tf(smoke_rule)})
        rules = split_rule_blocks(block)
        assert len(rules) == 2, "fixture does not actually carry two rules"
        with pytest.raises(AssertionError):
            assert_every_rule_has_expired_object_delete_marker(rules)

    def test_second_rule_prefix_without_trailing_slash_fails(self) -> None:
        smoke_rule = _SMOKE_RULE_TF.replace(
            'prefix = "${local.ducklake_smoke_data_prefix}/"', 'prefix = "${local.ducklake_smoke_data_prefix}"'
        )
        block = _single_data_lake_lifecycle_block({"main.tf": _two_rule_lifecycle_tf(smoke_rule)})
        rules = split_rule_blocks(block)
        assert len(rules) == 2, "fixture does not actually carry two rules"
        with pytest.raises(AssertionError):
            assert_every_rule_prefix_derived_with_trailing_slash(rules)

    def test_smoke_local_absent_from_coverage_fails(self) -> None:
        """A future/second prefix local that is declared but never referenced by any rule must
        fail the coverage sweep -- now that the exclusion roster no longer names the smoke prefix,
        this is the vacuous-pass rec-3945 closed off."""
        block = _single_data_lake_lifecycle_block({"main.tf": _GOOD_DATA_LAKE_LIFECYCLE_TF})
        roster = {"ducklake_prod_data_prefix": "ducklake", "ducklake_smoke_data_prefix": "ducklake-neon-smoke"}
        with pytest.raises(AssertionError):
            assert_every_declared_prefix_covered(roster, block, {})
