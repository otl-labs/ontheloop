"""Per-bundle CI-RCA fingerprint dedup decision (CIRCA-03(b), Decision 55 fail-closed).

Single implementation, two callers -- mirrors ghas-probe's "single probe implementation,
multiple callers" invariant: .github/workflows/ci-rca.yml's fp_dedup step (the real dedup
gate) and .github/workflows/dedup-probe.yml's synthetic self-test both invoke decide() (or
this module's CLI) so the decision logic is unit-testable instead of living in untested
workflow shell.

Per-bundle, not all-or-nothing (the bug this replaces): the prior inline shell loop set
DEDUPED=true only if EVERY bundle matched an open rec, and `break`-ed on the first non-match --
so a single spurious/novel bundle skipped bumping every OTHER bundle that DID match an open rec.
Here each bundle's fingerprint is resolved independently: a matched-open bundle is skipped and
its rec's occurrence bumped regardless of what any other bundle in the same run resolved to; a
genuinely-novel or fingerprint-less bundle always contributes to running the agent (Decision 55
-- dedup never silently swallows an undiagnosed failure). The overall run-agent decision is the
OR of every bundle's individual verdict.

Status-aware over the fingerprint CHAIN (ci-rca-identity-lifecycle): `finder` only ever matches
the NEWEST record in a fingerprint's chain while it is status=open (see
scripts.ops_portal.ci_rca_lifecycle.newest_open_in_chain) -- a CLOSED head is never bumped. An
optional `closed_head_resolver` seam additionally classifies a closed-head match as a stale-code
rerun ('drop' -- skip the agent, no bump, no reopen) or a genuine regression ('regression' -- run
the agent same as a novel fingerprint) via git ancestry (classify_closed_head). It defaults to
None (never consulted) so existing callers of decide() that inject only finder/bumper keep their
exact prior behaviour unchanged; main() wires the real resolver explicitly.

CLI usage (invoked by the workflow):
    bin/venv-python -m scripts.ci_rca.dedup --bundles-dir /tmp/ci-rca-bundles [--force-rca]
Prints one line per bundle verdict, then `deduped=true|false` for the workflow to capture.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional, Union

from scripts.ci_rca.fingerprint import FULL_TIER_SIBLING_SLUGS, compute_fingerprint_v2

logger = logging.getLogger(__name__)

SIBLING_FILE = "sibling-fingerprints.txt"
Entry = Union[str, list[str]]

FinderFn = Callable[[str], Optional[str]]
BumperFn = Callable[[str], None]
ClosedHeadResolverFn = Callable[[str], Optional[str]]  # fingerprint -> "drop" | "regression" | None


@dataclass
class BundleVerdict:
    fingerprint: str
    matched_rec_id: Optional[str]
    run_agent: bool


@dataclass
class DedupResult:
    run_agent: bool
    verdicts: list[BundleVerdict] = field(default_factory=list)
    force_rca: bool = False

    @property
    def deduped(self) -> bool:
        return not self.run_agent


def _default_finder(fingerprint: str, profile: Optional[str] = None) -> Optional[str]:
    """Status-aware: only the NEWEST record in the fingerprint's chain, while status=open."""
    from scripts.ops_portal.ci_rca_lifecycle import newest_open_in_chain  # noqa: PLC0415

    return newest_open_in_chain(fingerprint, profile=profile)


def _default_bumper(rec_id: str, profile: Optional[str] = None) -> None:
    from scripts.ops_portal.ci_rca_runtime import bump_ci_rca_occurrence  # noqa: PLC0415

    bump_ci_rca_occurrence(rec_id, profile=profile)


def _default_closed_head_resolver(fingerprint: str, profile: Optional[str] = None) -> Optional[str]:
    """Real closed-head classifier: 'drop' | 'regression' | None (no closed head at all)."""
    from scripts.ops_portal.ci_rca_lifecycle import (  # noqa: PLC0415
        classify_closed_head,
        closed_head_of_chain,
        current_commit_sha,
    )

    head = closed_head_of_chain(fingerprint, profile=profile)
    if head is None:
        return None
    return classify_closed_head(current_commit_sha(), head)


def sibling_fingerprints(bundle: dict) -> list[str]:
    """The bundle's own fingerprint, then (for a full-tier sibling slug) its v2 fingerprint under each other sibling.

    Decision 142 cl.1 keeps the fingerprint itself unchanged; a bundle missing its fingerprint yields [], and one
    missing workflow_name, failure_category or error_signature is looked up on its own fingerprint only.
    """
    own = str(bundle.get("fingerprint") or "")
    if not own:
        return []
    name, category, signature = (str(bundle.get(k) or "") for k in ("workflow_name", "failure_category", "error_signature"))
    if not (name and category and signature):
        return [own]
    from scripts.ci_rca.evidence._bundle_core import _slugify_workflow  # noqa: PLC0415

    slug = _slugify_workflow(name)
    if slug not in FULL_TIER_SIBLING_SLUGS:
        return [own]
    out = [own]
    for other in FULL_TIER_SIBLING_SLUGS:
        if other != slug:
            fp = compute_fingerprint_v2(other, category, signature)
            if fp not in out:
                out.append(fp)
    return out


def decide(
    fingerprints: list[Entry],
    *,
    force_rca: bool = False,
    finder: Optional[FinderFn] = None,
    bumper: Optional[BumperFn] = None,
    closed_head_resolver: Optional[ClosedHeadResolverFn] = None,
    profile: Optional[str] = None,
) -> DedupResult:
    """Per-bundle dedup decision over a run's evidence-bundle fingerprints.

    Args:
        fingerprints: One fingerprint string per emitted evidence bundle for this run
            (empty string for a bundle with no fingerprint -- treated as novel/fail-closed).
        force_rca: CIRCA-03(c) / Decision 74 dedup-bypass. Bypasses ALL lookups and always
            runs the agent -- mirrors the fp_dedup step's `force_rca` input.
        finder: Injected callable(fingerprint) -> rec_id | None. Defaults to the real
            status-aware chain lookup (newest_open_in_chain) -- only the newest, OPEN record
            in a fingerprint's chain is ever a bump target; a closed head never matches here.
        bumper: Injected callable(rec_id) -> None. Defaults to the real occurrence bump
            (bump_ci_rca_occurrence). Called once per matched-open bundle.
        closed_head_resolver: Injected callable(fingerprint) -> "drop" | "regression" | None.
            Consulted ONLY when `finder` misses. "drop" skips the agent (no bump, matches a
            stale-code rerun of an already-fixed commit); "regression" or None (no closed head
            at all) runs the agent same as a genuinely novel fingerprint. Defaults to None
            (never consulted) so a caller injecting only finder/bumper keeps EXACTLY its prior
            behaviour -- main() wires the real resolver (_default_closed_head_resolver)
            explicitly for the workflow's fp_dedup step.
        profile: AWS profile override for the default finder/bumper/resolver.

    Returns:
        DedupResult.run_agent is True (deduped is False) iff force_rca, no fingerprints were
        supplied at all (zero evidence -- fail closed), or ANY bundle's fingerprint is missing,
        has no open-rec match AND no closed-head 'drop' verdict. A matched-open bundle's rec is
        bumped as a side effect via bumper; a closed-head 'drop' match is never bumped (a closed
        head is never mutated).
    """
    if force_rca:
        return DedupResult(run_agent=True, verdicts=[], force_rca=True)

    if not fingerprints:
        return DedupResult(run_agent=True, verdicts=[])

    resolved_finder: FinderFn = finder or (lambda fp: _default_finder(fp, profile=profile))
    resolved_bumper: BumperFn = bumper or (lambda rec_id: _default_bumper(rec_id, profile=profile))

    verdicts: list[BundleVerdict] = []
    any_novel = False
    for entry in fingerprints:
        fps = [entry] if isinstance(entry, str) else list(entry)
        fp = fps[0] if fps else ""
        if not fp:
            verdicts.append(BundleVerdict(fingerprint=fp, matched_rec_id=None, run_agent=True))
            any_novel = True
            continue
        rec_id = next((found for found in (resolved_finder(f) for f in fps) if found), None)
        if rec_id:
            resolved_bumper(rec_id)
            verdicts.append(BundleVerdict(fingerprint=fp, matched_rec_id=rec_id, run_agent=False))
            continue
        if closed_head_resolver is not None:
            readings = [closed_head_resolver(f) for f in fps]
            if "drop" in readings and "regression" not in readings:
                # Closed head, ancestry-proven stale-code rerun: skip the agent, never bump, never
                # reopen (a closed head is never mutated). Any regression reading runs the agent.
                verdicts.append(BundleVerdict(fingerprint=fp, matched_rec_id=None, run_agent=False))
                continue
        verdicts.append(BundleVerdict(fingerprint=fp, matched_rec_id=None, run_agent=True))
        any_novel = True

    return DedupResult(run_agent=any_novel, verdicts=verdicts)


def _load_fingerprints(bundles_dir: Path) -> list[Entry]:
    fingerprints: list[Entry] = []
    for f in sorted(bundles_dir.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not parse bundle %s: %s", f, exc)
            fingerprints.append("")
            continue
        fps = sibling_fingerprints(data)
        fingerprints.append(fps[0] if len(fps) == 1 else fps or "")
    return fingerprints


def _write_sibling_file(bundles_dir: Path, fingerprints: list[Entry]) -> None:
    lines = [e if isinstance(e, str) else " ".join(e) for e in fingerprints if e]
    try:
        (bundles_dir / SIBLING_FILE).write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    except OSError as exc:
        logger.warning("Could not write %s: %s", SIBLING_FILE, exc)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Per-bundle CI-RCA fingerprint dedup decision.")
    parser.add_argument("--bundles-dir", type=Path, required=True, help="Directory of emitted evidence bundle JSONs")
    parser.add_argument("--force-rca", action="store_true", help="Bypass dedup entirely (Decision 74)")
    parser.add_argument("--profile", default=None, help="AWS profile override")
    args = parser.parse_args(argv)

    fingerprints = _load_fingerprints(args.bundles_dir) if args.bundles_dir.exists() else []
    if args.bundles_dir.exists():
        _write_sibling_file(args.bundles_dir, fingerprints)
    result = decide(
        fingerprints,
        force_rca=args.force_rca,
        profile=args.profile,
        closed_head_resolver=lambda fp: _default_closed_head_resolver(fp, profile=args.profile),
    )

    if result.force_rca:
        print("force_rca=true; bypassing fingerprint dedup.")
    elif not fingerprints:
        print("No evidence bundles found; running agent.")
    for v in result.verdicts:
        if v.matched_rec_id:
            print(f"Fingerprint {v.fingerprint} matches open {v.matched_rec_id}; bumped occurrence.")
        elif v.fingerprint and not v.run_agent:
            print(f"Fingerprint {v.fingerprint} matches a closed head; ancestry-proven stale rerun, dropping.")
        elif v.fingerprint:
            print(f"Fingerprint {v.fingerprint} has no open match; running agent.")
        else:
            print("Bundle has no fingerprint; running agent.")

    print(f"deduped={'true' if result.deduped else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
