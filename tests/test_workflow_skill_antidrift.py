"""Anti-drift contract for the /plan and /implement workflow surfaces.

Each assertion here pins a decision that has no other enforcement counterpart: the shared
preflight venv row, the one-full-tier rule, the verify-do-not-re-derive shape of the critique
checks, the single author-side `plan_obligations` run, the plan context-block cap, and (PLAN-
plan-followon-recs-field, PDB-01) the follow-on filing contract -- the Step 7 filing block's
guarded, correctly-ordered instruction text, the Step 8 planning-time split clause, the planning
template line plus its ledger pointer, and the ledger contract's two plan-linkage field keys.
"""

from pathlib import Path

import yaml

_IMPLEMENT_SKILL = Path(".claude/skills/implement/SKILL.md")
_PLANNING_SKILL = Path(".claude/skills/planning/SKILL.md")
_CRITIQUE_SKILL = Path(".claude/skills/plan-critique/SKILL.md")
_IMPLEMENT_CMD = Path(".claude/commands/implement.md")
_PLAN_CMD = Path(".claude/commands/plan.md")
_DQ_RUNNER = Path("scripts/data_quality_runner.py")
_EXIT_CRITERIA_LEDGER = Path("docs/contracts/exit-criteria-ledger.yaml")

_FULL_TIER = "bin/venv-python -m scripts.validate"

_VENV_ROW = (
    '- **`venv_ok: false`** -- Verify `bin/venv-python -c "import sys; print(sys.executable)"` '
    "resolves to the venv interpreter and rerun preflight. If still false, STOP."
)


def test_preflight_venv_row_is_shared_verbatim_between_planning_and_implement() -> None:
    """The two skills' venv_ok remediation must not drift (AGENTS.md `## Shell invocations`)."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    implement = _IMPLEMENT_SKILL.read_text(encoding="utf-8")
    assert _VENV_ROW in planning
    assert _VENV_ROW in implement
    for text in (planning, implement):
        assert "Auto-activate venv" not in text
        assert "source .venv/bin/activate" not in text


def test_full_tier_runs_once_and_only_in_the_step7_closure() -> None:
    """The full `scripts.validate` tier runs once per /implement session, in Step 7's closure."""
    workflow = _IMPLEMENT_CMD.read_text(encoding="utf-8")
    step6 = workflow[workflow.index("## Step 6") : workflow.index("## Step 7")]
    assert f"{_FULL_TIER} --pre" in step6
    assert "One-full-tier rule" in step6
    assert not any(line.strip().rstrip("`").endswith(_FULL_TIER) and "--pre" not in line for line in step6.splitlines())

    skill = _IMPLEMENT_SKILL.read_text(encoding="utf-8")
    closure = skill[skill.index("### Run the full gate locally first") :]
    assert skill.count(f"`{_FULL_TIER}`") == 1
    assert f"`{_FULL_TIER}`" in closure


def test_handoff_full_tier_runs_after_commit_and_rebase() -> None:
    """PLAN-handoff-validates-committed-tree: within the implement skill's IMPLEMENTATION Commit
    Flow, the full tier runs on the COMMITTED, rebased tree -- git commit < git rebase < the full
    tier < --verify-head < git push. Also asserts the retired pre-commit-HEAD / no-commit-happened
    / --log-housekeeping language is gone from both surfaces, and that the skill's single
    backticked full-tier mention (pinned by test_full_tier_runs_once_and_only_in_the_step7_closure
    above) sits in the Commit Flow's own closure item, not detached from it."""
    skill = _IMPLEMENT_SKILL.read_text(encoding="utf-8")
    command = _IMPLEMENT_CMD.read_text(encoding="utf-8")

    commit_flow = skill[skill.index("### IMPLEMENTATION Commit Flow") : skill.index("### STRATEGIC Commit Flow")]
    verify_head_line = "bin/venv-python -m scripts.checks.validation_result --verify-head"
    assert (
        commit_flow.index("git commit")
        < commit_flow.index("scripts.session.rebase_resolve")
        < commit_flow.index(f"\n{_FULL_TIER}\n")
        < commit_flow.index(verify_head_line)
        < commit_flow.index("git push")
    )

    assert "STOP on conflict" not in commit_flow

    for text in (skill, command):
        assert "pre-commit HEAD" not in text
        assert "pre-commit-HEAD" not in text

    assert "no commit has happened" not in command
    assert "--log-housekeeping" not in command

    assert ", then `bin/venv-python -m scripts.validate`" not in skill
    for line in skill.splitlines():
        if f"`{_FULL_TIER}`" in line:
            assert "Commit Flow" in line


def test_critique_verifies_rather_than_rederives() -> None:
    """12b/12k cross-check the plan's stated answer instead of recomputing it from raw sources."""
    critique = _CRITIQUE_SKILL.read_text(encoding="utf-8")
    lambda_check = critique[critique.index("12b.") : critique.index("12c.")]
    assert "VERIFY, do not re-derive" in lambda_check
    assert "--list-patterns" not in lambda_check
    assert "compute_affected_artifacts" in lambda_check
    assert "src/lambdas/<slug>/manifest.yaml" in lambda_check

    closure = critique[critique.index("12k.") : critique.index("12l.")]
    assert "VERIFY the plan's own declarations" in closure
    assert "**Closure Obligation (12k)" in critique


def test_planning_enforcement_names_the_critique_check_id() -> None:
    """Planning's closure-obligation enforcement must name the critique check that fires (12k)."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    assert "fails plan-critique 12k" in planning


def test_closure_obligation_requires_caller_disposition() -> None:
    """A deleted writer's callers are disposed at the contract and at all three skill surfaces."""
    lifecycle = yaml.safe_load(Path("docs/contracts/tier-item-lifecycle.yaml").read_text(encoding="utf-8"))
    closure = " ".join(lifecycle["replacement_closure_check"].split())
    assert "(5) Dispose of every caller" in closure
    assert "fails loudly" in closure
    assert "stub" in closure
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    assert "every caller of a deleted writer" in planning or "lists every caller" in planning
    critique = _CRITIQUE_SKILL.read_text(encoding="utf-8")
    assert "replacement_closure_check` (5)" in critique[critique.index("12k.") : critique.index("12l.")]
    review = Path(".claude/skills/code-review/SKILL.md").read_text(encoding="utf-8")
    assert "replacement_closure_check" in review


def test_plan_workflow_invokes_obligations_once_at_alignment_sign_off() -> None:
    """`plan_obligations` runs once author-side, at the alignment sign-off, against a draft written OUTSIDE the repo.

    The draft path must stay outside `docs/plans/`: `{slug}` is not derived until Step 7, and a
    Write to the tracked plan path while on `main` is denied by `.claude/hooks/never_on_main.py`
    two steps before Step 7's designed clean STOP.
    """
    workflow = _PLAN_CMD.read_text(encoding="utf-8")
    assert workflow.count("scripts.roadmap.plan_obligations") == 1
    step4 = workflow[workflow.index("## Step 4") : workflow.index("## Step 5")]
    assert "plan_obligations" not in step4
    step6 = workflow[workflow.index("## Step 6") : workflow.index("## Step 7")]
    assert "scripts.roadmap.plan_obligations" in step6
    assert "a scratch path outside the repo" in step6
    assert "--plan docs/plans/" not in step6


def test_plan_context_block_cap_is_declared_on_both_surfaces() -> None:
    """The 40-line context cap appears in the PLAN template and as an authoring rule."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    workflow = _PLAN_CMD.read_text(encoding="utf-8")
    assert "context: # <= 40 rendered lines" in planning
    assert "**Context-block discipline (<= 40 rendered lines).**" in workflow
    assert "`commit_message_conventions.change_record_content_rule`" in workflow


def _step9_convergence() -> str:
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    return planning[
        planning.index("**Convergence rule -- findings, not rounds:**\n\nEvery round") : planning.index(
            "## Report Critique Gate"
        )
    ]


def test_critique_gate_convergence_is_finding_shaped() -> None:
    """Step 9 converges on findings, never a count, and nits never reach the human (rec-2944, rec-3517)."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    rule = _step9_convergence()
    assert "after 3 REVISE rounds" not in planning
    assert "Every round is a full fresh read and no count caps them" in rule
    assert "REVISE means at least one blocking finding" in rule
    assert "PROCEED listing nits -> apply them and dispatch ONE fresh confirming round, no card" in rule
    assert "it ends the loop unless it returns a blocking finding" in rule
    assert "(oscillation interrupts, not the count)" in rule
    assert "recurring at a tag+anchor you recorded fixed" in rule
    assert "a disputed blocking finding" in rule
    assert "a fix that would change approved scope, tier or a cited Decision" in rule
    assert "starts a new series" in rule
    assert "Apply the Step 9 Convergence rule in kind" in planning


def test_open_escalation_stops_the_loop_and_records_the_answer() -> None:
    """A posted escalation is a stop, carried in both dispatch modes (rec-2802)."""
    rule = _step9_convergence()
    assert "An open escalation is a stop: no round, no plan edit, until the human answers" in rule
    assert "record the answer as the row's `escalation_choice`" in rule
    assert "Carrier: AskUserQuestion if held, else chat; dispatched, GATE_REQUEST `open_questions`" in rule


def test_decision_scout_convergence_is_finding_shaped() -> None:
    """The Decision Scout gate uses the critique rule in kind, anchored on the BLOCKing decision id (rec-3526)."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    scout = planning[planning.index("## Decision Scout Gate") : planning.index("## Alignment Sign-off")]
    assert "Do not loop more than 3 times" not in planning
    assert planning.count("findings, not rounds") >= 3
    assert "re-BLOCKed on the same decision id" in scout
    assert "Carrier (AskUserQuestion, chat or GATE_REQUEST) and stop as in Step 9" in scout
    assert "(file a rec; record accept-with-deferral)" in scout


def test_oscillation_revisit_condition_is_countable() -> None:
    """Every revisit condition counts something the gate_rounds record holds (rec-3514)."""
    rule = _step9_convergence()
    assert "40 cap-hitting plans" not in rule
    assert "Revisit when 20 plans carry `gate_rounds`" in rule
    assert "Revisit after 10 contested escalations" in rule


def test_contested_residue_consult_names_its_dispatch_carrier() -> None:
    """The Fable consult names its subagent-dispatch carrier and the canonical model spelling (rec-3515)."""
    rule = _step9_convergence()
    consult = rule[rule.index("**Contested-residue consult:**") :]
    assert "(dispatched: an `open_questions` item of the same GATE_REQUEST)" in consult
    assert 'model: "fable"' in consult
    assert 'model:"fable"' not in rule


def test_plan_template_records_gate_rounds_not_a_gates_line() -> None:
    """The structured per-round record replaces the free-text gates line (rec-3041)."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    assert "plan-critique=<verdict> after <N> round(s)" not in planning
    assert "gate_rounds: # REQUIRED on new plans; one row per gate round (+gate_run_id if dispatched)" in planning
    assert (
        "- {gate: decision-scout, round: 1, verdict: NO_FLAGS} # critique rows add blocking, nits; "
        "escalated: true + escalation_choice when asked"
    ) in planning
    assert "**Verdict handling** (add a `gate_rounds` row per scout round):" in planning


def test_plan_command_and_overseer_contract_defer_to_the_skill_rule() -> None:
    """No surface restates a round cap the skill no longer has."""
    plan_cmd = " ".join(_PLAN_CMD.read_text(encoding="utf-8").split())
    assert "round 4 runs one autonomous confirming round" not in plan_cmd
    assert "a PROCEED that lists nits gets them applied and ONE fresh confirming round, with no card" in plan_cmd
    assert "An open escalation stops the loop until the human answers" in plan_cmd
    assert "the gates line" not in plan_cmd
    assert "gate history lives in `gate_rounds`" in plan_cmd
    dispatch = " ".join(Path("docs/contracts/overseer-dispatch.yaml").read_text(encoding="utf-8").split())
    assert "3-round" not in dispatch
    assert "per the planning skill's Decision Scout convergence rule" in dispatch
    assert "or GATE_REQUEST round=N+1 after a BLOCK pivot" in dispatch
    assert "On a PROCEED that ends the planning skill's Step 9 loop (no nits, or the confirming round's)" in dispatch
    assert "On REVISE, or any other PROCEED listing nits: the subagent fixes and re-requests" in dispatch


def test_dispatch_contract_relays_an_escalation_instead_of_a_round() -> None:
    """A dispatched escalation and its Fable consult have a receiver that stops the loop (rec-2802, rec-3515)."""
    trampoline = yaml.safe_load(Path("docs/contracts/overseer-dispatch.yaml").read_text(encoding="utf-8"))[
        "gate_request_trampoline"
    ]
    escalation = " ".join(trampoline["escalation"].split())
    assert "with non-empty `open_questions` is the planning skill's convergence escalation, not a round request" in escalation
    assert "first, relays both to the human verbatim" in escalation
    assert "dispatches no gate round, and resumes the author with the answer" in escalation
    assert "any Fable consult it requests (an `open_questions` item) first" in escalation
    assert "write-ahead-recorded with its `open_questions` as `inputs` and mints no gate_run_id" in escalation
    assert "on resume it is re-relayed, never re-dispatched as a round" in escalation
    assert "convergence-rule counter" not in trampoline["request_schema"]


def test_critic_tags_every_finding_with_a_stable_anchor() -> None:
    """B3-R5: every finding carries a mechanical/judgement tag with a stable anchor grammar."""
    critique = _CRITIQUE_SKILL.read_text(encoding="utf-8")
    assert "tag each finding, with a stable anchor (plan field path or VP step id)" in critique
    assert "mechanical (a deterministic check could catch it -- name it)" in critique
    assert "tag each registration-closure finding" not in critique
    assert "mechanical (scripts.roadmap.plan_obligations)" not in critique


def test_critic_tags_severity_and_states_figures_as_hypotheses() -> None:
    """Blocking/nit severity is the critic's call; Frame Challenge is never a nit; figures are re-measured (rec-3742)."""
    critique = _CRITIQUE_SKILL.read_text(encoding="utf-8")
    assert "blocking (correctness, safety, scope, a Decision conflict or failed check above; when unsure)" in critique
    assert "nit (wording, citation form, ordering; never a Frame Challenge contradiction)" in critique
    assert "**Recommendation:** PROCEED (no blocking finding; list nits) / REVISE (any blocking finding)" in critique
    assert "State each measured figure as a hypothesis with the command that produced it" in critique
    assert "the reviser will re-measure it and dispute, with evidence, any it cannot reproduce" in critique
    assert "600KB" not in critique
    assert "**Finding-Origin Attribution and severity:**" in critique


def test_implementation_branch_asks_the_split_question() -> None:
    """PDB-02: the IMPLEMENTATION branch asks the split question and names the deferred half's disposition."""
    critique = _CRITIQUE_SKILL.read_text(encoding="utf-8")
    assert "For IMPLEMENTATION: Are all scope entries necessary? Too large (suggest split)?" in critique
    assert "deferred half filed as a follow-on rec" in critique


def test_escalation_menu_is_identical_on_command_and_skill() -> None:
    """PDB-05: plan.md's Step 9 escalation menu is the planning skill's menu, verbatim."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    plan_cmd = _PLAN_CMD.read_text(encoding="utf-8")
    menu = "accept-with-deferral / re-scope / split / abandon / one more round"
    assert menu in planning
    assert menu in plan_cmd


def test_implement_step7_files_the_deferred_half_and_writes_followon_recs() -> None:
    """PLAN-plan-followon-recs-field (PDB-01, B1-R1): Step 7 opens with the follow-on filing
    block, guarded and correctly ordered ahead of the Commit Flow handoff sentence -- where the
    implement skill's closure items 3-4 set `implementation_declared` true. An UNCONDITIONAL
    rewrite that drops the guard token would mint a spurious follow-on rec every session and is
    exactly what (iii) below catches; it must go red."""
    workflow = _IMPLEMENT_CMD.read_text(encoding="utf-8")
    step7 = workflow[workflow.index("## Step 7") : workflow.index("## Step 8")]
    handoff = "Apply the appropriate **Commit Flow**"

    assert "Deferred half (follow-on split)" in step7
    assert step7.index("Deferred half (follow-on split)") < step7.index(handoff)

    assert "only if this session deferred a half" in step7
    assert "no-op" in step7
    assert step7.index("only if this session deferred a half") < step7.index(handoff)

    # The block's substantive body, not just its heading and guard: without these the graduated
    # entry followon-half-filing-step-pinned would claim to pin a filing step whose portal call,
    # flags and write-back could all be deleted while the check stayed green. Asserted against a
    # whitespace-collapsed copy because the source is wrapped prose, so a literal that reads as
    # one phrase may straddle a newline.
    flat = " ".join(step7.split())
    write_back = "append the returned id to this plan's `followon_recs`"
    for needle in (
        "scripts.ops_data_portal --guidance",
        "--file-rec",
        "--source manual",
        "--tags follow-on",
        "Deferred half of PLAN-{slug}",
        "lint_acceptance_command(require_discrimination=True)",
        write_back,
    ):
        assert needle in flat, needle
    assert flat.index("--file-rec") < flat.index(write_back)

    tail = workflow[workflow.index("## Step 9") :]
    assert "follow-on rec ids" in tail


def test_plan_step8_names_the_planning_time_split_filing() -> None:
    """PLAN-plan-followon-recs-field (PDB-01, B1-R1): Step 8 carries the conditional
    planning-time split clause naming followon_recs; an unguarded clause is red on the guard
    token."""
    workflow = _PLAN_CMD.read_text(encoding="utf-8")
    step8 = workflow[workflow.index("## Step 8") : workflow.index("## Step 9")]
    assert "Planning-time split" in step8
    assert "only if this plan defers a half" in step8
    assert "followon_recs" in step8


def test_planning_template_carries_followon_recs_and_points_at_the_ledger_contract() -> None:
    """PLAN-plan-followon-recs-field (PDB-01, B1-R5): the plan template carries the
    followon_recs line, and the field-format section points at the ledger contract instead of
    restating the referential rule inline."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    assert "followon_recs: [] # deferred halves as follow-on rec ids, or []" in planning
    assert "### closes_criteria / followon_recs field format" in planning
    assert "docs/contracts/exit-criteria-ledger.yaml" in planning
    assert "check (iii) in validate_platform_roadmap" not in planning


def test_exit_criteria_ledger_carries_both_plan_linkage_fields() -> None:
    """PLAN-plan-followon-recs-field (PDB-01): the ledger contract carries the relocated
    closes_criteria referential rule plus the new followon_recs field's discovery-marker
    convention."""
    doc = yaml.safe_load(_EXIT_CRITERIA_LEDGER.read_text(encoding="utf-8"))
    fields = doc["fields"]
    assert "plan_closes_criteria" in fields
    assert "check (iii) in validate_platform_roadmap" in (fields["plan_closes_criteria"].get("write_time_validation") or "")
    assert "plan_followon_recs" in fields
    governance_notes = fields["plan_followon_recs"].get("governance_notes") or ""
    assert "follow-on" in governance_notes
    assert "Deferred half of PLAN-<parent-slug>" in governance_notes


def test_data_quality_semantics_live_with_their_producer() -> None:
    """The two-layer DQ health picture relocates to its producer; planning/SKILL.md keeps only a pointer."""
    planning = _PLANNING_SKILL.read_text(encoding="utf-8")
    runner = _DQ_RUNNER.read_text(encoding="utf-8")
    assert "### What Data-Quality Health Represents" not in planning
    assert "checks_defined == 0" in planning
    assert "config/agent/data_quality/" in planning
    assert str(_DQ_RUNNER) in planning
    assert "Two-layer health picture" in runner
    assert "checks_defined > 0" in runner
    assert "last_run.verdict == PASS" in runner
