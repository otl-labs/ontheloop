"""Monthly cost reconciliation monitor (CD.28 / ULF-08).

Ingests a per-provider cost snapshot (invoice totals + AWS service line-items
+ Anthropic programmatic-pool utilisation), evaluates the five
cost_projection.reevaluation_triggers (docs/ROADMAP-PLATFORM.yaml) plus KG.7's
Tier-2 pool-exhaustion concern, and files/updates/closes a recommendation per
trigger through the ops portal (alarm-not-gate, Decision 55 / Decision 62).
The telemetry leg (invoice-vs-telemetry discrepancy) degrades gracefully until
T2.36 lands est_cost_usd on the reader boundary.

Public-repo boundary (Decision 101): build_report() splits a full report
(absolute dollars + measured ratios, private sink only) from a public_summary
(trigger names + PASS/FAIL + public threshold values only -- never a measured
value or dollar figure). Import-safe: no exceptions at import time; no
eval/exec.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict

from scripts import rec_episode

DEFAULT_CONFIG_PATH = Path("config/agent/cost_reconciliation.yaml")
ROADMAP_FILE = "docs/ROADMAP-PLATFORM.yaml"
SOURCE = "cost_reconciliation"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


class DeepseekProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_total_usd: float
    unit_price_input_per_mtok: float
    unit_price_output_per_mtok: float


class AnthropicProvider(BaseModel):
    model_config = ConfigDict(extra="forbid")
    invoice_total_usd: float
    programmatic_pool_size_usd: float
    pool_spend_usd: float


class Providers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deepseek: DeepseekProvider
    anthropic: AnthropicProvider


class AwsCosts(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total_bill_usd: float
    line_items_usd: dict[str, float]


class CostSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    month: str
    currency: str = "USD"
    providers: Providers
    aws: AwsCosts
    runner_scheduled_alternative_usd: float = 0.0


class Baseline(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deepseek_input_per_mtok: float
    deepseek_output_per_mtok: float
    anthropic_pool_size_usd: float


class Thresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deepseek_price_change_factor: float
    anthropic_pool_utilisation_pct: float
    s3_share_of_bill_pct: float
    runner_cost_vs_scheduled_factor: float
    step_functions_share_of_bill_pct: float
    invoice_vs_telemetry_discrepancy_pct: float


class ReportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    private_s3_prefix: str


class CostConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: Baseline
    thresholds: Thresholds
    report: ReportConfig


@dataclass
class TriggerResult:
    key: str
    label: str
    tripped: bool
    measured: Optional[float]
    threshold: float
    unit: str  # "x" (factor) or "%" (percentage)


@dataclass
class TelemetryCost:
    month: str
    est_cost_usd: float


@dataclass
class Discrepancy:
    invoice_total_usd: float
    telemetry_est_usd: float
    pct_diff: float
    tripped: bool
    threshold: float


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------


def load_snapshot(path: str) -> CostSnapshot:
    """Load and schema-validate a monthly cost snapshot from a local JSON file."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return CostSnapshot.model_validate(raw)


def load_config(path: Optional[str] = None) -> CostConfig:
    """Load baselines + thresholds from config/agent/cost_reconciliation.yaml (or path)."""
    cfg_path = Path(path) if path else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    return CostConfig.model_validate(raw)


def load_telemetry_cost(month: str) -> Optional[TelemetryCost]:
    """Return telemetry-derived est_cost_usd for month, or None (graceful stub).

    T2.36 forward-constraint (Decision 88 / Decision 84 I-3): the telemetry store
    is not yet migrated (TelemetryModelCalls carries tokens_input/output but no
    est_cost_usd). When T2.36 lands, this MUST read est_cost_usd through a
    registered named reader verb over the ducklake_reader closed boundary --
    NOT raw SQL and NOT a read-cache re-fetch (Decision 84 I-3).
    """
    del month  # unused until T2.36
    return None


# ---------------------------------------------------------------------------
# Trigger evaluation
# ---------------------------------------------------------------------------


def _pct(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return (numerator / denominator) * 100.0


def _price_change_factor(current: float, baseline: float) -> float:
    if current <= 0 or baseline <= 0:
        return 0.0
    return max(current / baseline, baseline / current)


def evaluate_triggers(snapshot: CostSnapshot, config: CostConfig) -> list[TriggerResult]:
    """Evaluate the five cost_projection.reevaluation_triggers.

    Trigger 2 (anthropic_pool_utilisation) IS KG.7 concern #1 -- same 70% threshold,
    same measured ratio; no separate sixth entry.
    """
    baseline = config.baseline
    thresholds = config.thresholds
    ds = snapshot.providers.deepseek
    an = snapshot.providers.anthropic
    aws = snapshot.aws

    price_factor = max(
        _price_change_factor(ds.unit_price_input_per_mtok, baseline.deepseek_input_per_mtok),
        _price_change_factor(ds.unit_price_output_per_mtok, baseline.deepseek_output_per_mtok),
    )
    pool_pct = _pct(an.pool_spend_usd, an.programmatic_pool_size_usd or baseline.anthropic_pool_size_usd)
    s3_pct = _pct(aws.line_items_usd.get("s3", 0.0), aws.total_bill_usd)
    sf_pct = _pct(aws.line_items_usd.get("step_functions", 0.0), aws.total_bill_usd)

    scheduled_alt = snapshot.runner_scheduled_alternative_usd
    runner_cost = aws.line_items_usd.get("self_hosted_runner", 0.0)
    # Vestigial post-CD.21 (runner retired 2026-05-28): guard the zero/absent
    # scheduled-alternative denominator so a $0 runner line-item never divides-by-zero
    # or false-trips.
    runner_factor = (runner_cost / scheduled_alt) if scheduled_alt > 0 else 0.0

    return [
        TriggerResult(
            key="deepseek_price_change",
            label="DeepSeek direct API pricing change",
            tripped=price_factor > thresholds.deepseek_price_change_factor,
            measured=price_factor,
            threshold=thresholds.deepseek_price_change_factor,
            unit="x",
        ),
        TriggerResult(
            key="anthropic_pool_utilisation",
            label="Anthropic Max x5 programmatic-pool utilisation (KG.7 #1)",
            tripped=pool_pct > thresholds.anthropic_pool_utilisation_pct,
            measured=pool_pct,
            threshold=thresholds.anthropic_pool_utilisation_pct,
            unit="%",
        ),
        TriggerResult(
            key="s3_share_of_bill",
            label="S3 storage share of total monthly bill",
            tripped=s3_pct > thresholds.s3_share_of_bill_pct,
            measured=s3_pct,
            threshold=thresholds.s3_share_of_bill_pct,
            unit="%",
        ),
        TriggerResult(
            key="runner_cost_vs_scheduled",
            label="Self-hosted runner cost vs scheduled-runner alternative",
            tripped=runner_factor > thresholds.runner_cost_vs_scheduled_factor,
            measured=runner_factor,
            threshold=thresholds.runner_cost_vs_scheduled_factor,
            unit="x",
        ),
        TriggerResult(
            key="step_functions_share_of_bill",
            label="Step Functions share of total monthly bill",
            tripped=sf_pct > thresholds.step_functions_share_of_bill_pct,
            measured=sf_pct,
            threshold=thresholds.step_functions_share_of_bill_pct,
            unit="%",
        ),
    ]


def compute_discrepancy(
    snapshot: CostSnapshot, telemetry: Optional[TelemetryCost], config: CostConfig
) -> Optional[Discrepancy]:
    """Return the invoice-vs-telemetry discrepancy, or None if telemetry is unavailable."""
    if telemetry is None:
        return None
    invoice_total = snapshot.providers.deepseek.invoice_total_usd + snapshot.providers.anthropic.invoice_total_usd
    pct_diff = _pct(abs(invoice_total - telemetry.est_cost_usd), invoice_total)
    threshold = config.thresholds.invoice_vs_telemetry_discrepancy_pct
    return Discrepancy(
        invoice_total_usd=invoice_total,
        telemetry_est_usd=telemetry.est_cost_usd,
        pct_diff=pct_diff,
        tripped=pct_diff > threshold,
        threshold=threshold,
    )


# ---------------------------------------------------------------------------
# Report (Decision 101 private/public split)
# ---------------------------------------------------------------------------


def build_report(
    triggers: list[TriggerResult],
    discrepancy: Optional[Discrepancy],
    month: str,
) -> tuple[str, str]:
    """Return (full_markdown, public_summary).

    full carries absolute figures + measured ratios (private sink only). public
    carries ONLY trigger names + pass/fail verdicts + public (roadmap-sourced)
    threshold values -- never a measured value, absolute dollar, or line-item.
    """
    full_lines = [f"# Cost reconciliation report -- {month}", ""]
    public_lines = [f"Cost reconciliation -- {month}", ""]

    for trig in triggers:
        verdict = "FAIL" if trig.tripped else "PASS"
        full_lines.append(
            f"- {trig.label}: {verdict} (measured={trig.measured:.2f}{trig.unit}, threshold={trig.threshold:.2f}{trig.unit})"
        )
        public_lines.append(f"- {trig.label}: {verdict} (threshold={trig.threshold:.2f}{trig.unit})")

    if discrepancy is None:
        full_lines.append("- Invoice-vs-telemetry discrepancy: PENDING (telemetry leg awaits T2.36)")
        public_lines.append("- Invoice-vs-telemetry discrepancy: PENDING (T2.36)")
    else:
        verdict = "FAIL" if discrepancy.tripped else "PASS"
        full_lines.append(
            f"- Invoice-vs-telemetry discrepancy: {verdict} "
            f"(invoice=${discrepancy.invoice_total_usd:.2f}, telemetry=${discrepancy.telemetry_est_usd:.2f}, "
            f"measured={discrepancy.pct_diff:.2f}%, threshold={discrepancy.threshold:.2f}%)"
        )
        public_lines.append(f"- Invoice-vs-telemetry discrepancy: {verdict} (threshold={discrepancy.threshold:.2f}%)")

    return "\n".join(full_lines) + "\n", "\n".join(public_lines) + "\n"


# ---------------------------------------------------------------------------
# Rec payload
# ---------------------------------------------------------------------------

# Per-trigger remedy text (Decision 66 -- actionable context, not a generic template).
_REMEDY: dict[str, str] = {
    "deepseek_price_change": "review DeepSeek direct API pricing and, if the change is durable, "
    "update the baseline in config/agent/cost_reconciliation.yaml.",
    "anthropic_pool_utilisation": "provision an org-billed Anthropic API key as overflow credential (CD.28 discipline point).",
    "s3_share_of_bill": "review S3 storage growth (lifecycle policies, storage class, retention) to "
    "bring the S3 line-item back under its share of the bill.",
    "runner_cost_vs_scheduled": "review self-hosted runner usage/pricing against the scheduled-runner alternative.",
    "step_functions_share_of_bill": "review Step Functions state-transition volume for an anomalous "
    "per-rec transition count before optimising.",
    "invoice_vs_telemetry_discrepancy": "reconcile telemetry est_cost_usd against the provider invoice; "
    "investigate under/over-estimation in the cost model.",
}


def _discrepancy_trigger(discrepancy: Discrepancy) -> TriggerResult:
    """Adapt a Discrepancy into a TriggerResult so it flows through the same file/update/close loop."""
    return TriggerResult(
        key="invoice_vs_telemetry_discrepancy",
        label="Invoice-vs-telemetry discrepancy",
        tripped=discrepancy.tripped,
        measured=discrepancy.pct_diff,
        threshold=discrepancy.threshold,
        unit="%",
    )


def build_rec_fields(trigger: TriggerResult, month: str, snapshot_path: str) -> dict[str, Any]:
    """Build a schema-shaped rec payload for a single tripped trigger.

    Runtime rec fields (Decision 66 write-time validators): title carries a
    STABLE per-trigger tag so find_open_cost_rec_for matches run-to-run; context
    names the measured breach + a trigger-specific CD.28 remedy + the
    auto-close-on-clear note; acceptance is a bare runnable command (no prose,
    no placeholders).
    """
    title = f"[cost:{trigger.key}] {trigger.label} exceeded threshold ({month})"
    remedy = _REMEDY.get(trigger.key, "review the breach against config/agent/cost_reconciliation.yaml thresholds.")
    context = (
        f"Monthly cost reconciliation (CD.28 / ULF-08) measured {trigger.label} at "
        f"{trigger.measured:.2f}{trigger.unit} against a threshold of {trigger.threshold:.2f}{trigger.unit} "
        f"for {month}. Remedy: {remedy} This rec auto-closes on the next monthly run once the trigger "
        f"clears (update_rec status=closed)."
    )
    acceptance = f"bin/venv-python -m scripts.cost_reconciliation --invoice {snapshot_path} --month {month}"
    return {
        "title": title,
        "file": ROADMAP_FILE,
        "status": "open",
        "source": SOURCE,
        "effort": "S",
        "priority": "Medium",
        "context": context,
        "acceptance": acceptance,
    }


def _validate_rec_payload(fields: dict[str, Any]) -> None:
    """Validate a rec payload through file_rec's own pre-check path, minus the write.

    validate_source, derivation on a copy, then gate_write over the contract's projection: the acceptance
    lint takes require_discrimination from the contract's declared leg, exactly as file_rec does.
    """
    from scripts.executor.jsonl_store import Recommendation  # noqa: PLC0415
    from scripts.executor.rec_write_guidance import validate_source  # noqa: PLC0415
    from scripts.ops_portal.risk_scoring import _derive_computed_fields  # noqa: PLC0415
    from scripts.ops_portal.write_validators import gate_write  # noqa: PLC0415

    validate_source(fields["source"])
    derived = dict(fields)
    _derive_computed_fields(derived)
    gate_write("ops_recommendations", derived, insert=True)
    Recommendation.model_validate({**derived, "id": "rec-0"})


def find_open_cost_rec_for(trigger_key: str) -> Optional[str]:
    """Return the id of the first open cost_reconciliation rec tagged for trigger_key, or None.

    Reads live through the source-scoped reader (rec_episode.find_recs), which raises on a reader
    failure, so an unreachable warehouse can never be mistaken for "no open rec" and file a duplicate.
    """
    tag = f"[cost:{trigger_key}]"
    for rec in rec_episode.find_recs(SOURCE, status="open"):
        if tag in (rec.get("title") or ""):
            return rec.get("id")
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def reconcile(
    snapshot_path: str,
    month: str,
    dry_run: bool = False,
    config_path: Optional[str] = None,
    report_out: Optional[str] = None,
    profile: Optional[str] = None,
) -> dict[str, Any]:
    """Load, evaluate, report, and (in live mode) dedupe-and-auto-close per trigger.

    Persistent-condition discipline (Decision 103): in live mode the dedupe lookup
    reads live rows through the source-scoped reader (no local recs cache,
    Decision 215). dry_run builds + validates the rec payload for every tripped
    trigger and performs NO reader read and NO portal write (proves the rec-filing path without a
    live warehouse write, per ULF-08 acceptance).
    """
    snapshot = load_snapshot(snapshot_path)
    config = load_config(config_path)
    telemetry = load_telemetry_cost(month)
    triggers = evaluate_triggers(snapshot, config)
    discrepancy = compute_discrepancy(snapshot, telemetry, config)
    full_report, public_summary = build_report(triggers, discrepancy, month)

    # The discrepancy trigger flows through the same file/update/close loop as the five
    # invoice-derived triggers (acceptance criterion #2: ANY trigger tripping OR a >5%
    # discrepancy files a rec). Only present once telemetry supplies a Discrepancy (T2.36).
    alarm_conditions = list(triggers)
    if discrepancy is not None:
        alarm_conditions.append(_discrepancy_trigger(discrepancy))

    result: dict[str, Any] = {
        "dry_run": dry_run,
        "triggers": [t.key for t in alarm_conditions if t.tripped],
        "full_report": full_report,
        "public_summary": public_summary,
        "actions": [],
    }

    if dry_run:
        would_file = []
        for cond in alarm_conditions:
            if not cond.tripped:
                continue
            fields = build_rec_fields(cond, month, snapshot_path)
            _validate_rec_payload(fields)
            would_file.append(fields)
        result["would_file"] = would_file
    else:
        from scripts.ops_data_portal import file_rec, update_rec  # noqa: PLC0415

        for cond in alarm_conditions:
            existing_id = find_open_cost_rec_for(cond.key)
            if cond.tripped:
                fields = build_rec_fields(cond, month, snapshot_path)
                if existing_id:
                    update_rec(existing_id, {"context": fields["context"]}, profile=profile)
                    result["actions"].append({"trigger": cond.key, "action": "update", "rec_id": existing_id})
                else:
                    new_id = file_rec(fields, profile=profile)
                    result["actions"].append({"trigger": cond.key, "action": "file", "rec_id": new_id})
            elif existing_id:
                update_rec(
                    existing_id,
                    {
                        "status": "closed",
                        "resolution": f"Trigger {cond.key} cleared on the {month} cost-reconciliation run.",
                    },
                    profile=profile,
                )
                result["actions"].append({"trigger": cond.key, "action": "close", "rec_id": existing_id})

    if report_out:
        Path(report_out).write_text(full_report, encoding="utf-8")

    return result


def _previous_month(today: Optional[date] = None) -> str:
    """Return the previous calendar month as YYYY-MM."""
    d = today or datetime.now(timezone.utc).date()
    first_of_this_month = d.replace(day=1)
    last_of_prev_month = first_of_this_month.fromordinal(first_of_this_month.toordinal() - 1)
    return last_of_prev_month.strftime("%Y-%m")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Monthly cost reconciliation monitor (CD.28 / ULF-08)")
    parser.add_argument("--invoice", required=True, help="Path to the monthly cost snapshot JSON")
    parser.add_argument("--month", default=None, help="YYYY-MM; defaults to the previous calendar month")
    parser.add_argument("--dry-run", action="store_true", help="Validate the rec payload; no warehouse write")
    parser.add_argument("--config", default=None, help="Override config/agent/cost_reconciliation.yaml path")
    parser.add_argument("--report-out", default=None, help="Path to write the full (private) markdown report")
    args = parser.parse_args(argv)

    month = args.month or _previous_month()

    try:
        result = reconcile(
            args.invoice,
            month,
            dry_run=args.dry_run,
            config_path=args.config,
            report_out=args.report_out,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[cost_reconciliation] ERROR: {exc}", file=sys.stderr)
        return 1

    print(result["public_summary"])

    if args.dry_run:
        n = len(result["would_file"])
        print(f"[cost_reconciliation] DRY RUN -- no warehouse write occurred. {n} rec(s) would be filed/updated:")
        for fields in result["would_file"]:
            print(f"  title={fields['title']!r} source={fields['source']!r} acceptance={fields['acceptance']!r}")
    else:
        for action in result["actions"]:
            print(f"[cost_reconciliation] {action['trigger']}: {action['action']} -> {action['rec_id']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
