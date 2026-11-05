# Spike: one audit prompt, six model runs, four blinded verifiers

The machine-readable record is `audits/model-comparison-spike-2b8c4326.yaml`; this report summarises it.

**Evidence strength: shallow.** There was one completed run per model (two for DeepSeek), across two harnesses. The Vibe runs had no web access, and Claude was both a contestant and one of the verifiers. Read the findings as direction, not as measured fact.

## Question

Can models far cheaper than Claude execute a hard, self-contained repository audit, and verify one, well enough to take on gate roles such as decision-scout and code-review?

## Method

**The audit runs.**
- Six runs executed `docs/audit-prompts/AUDIT-verdict-personas-on-actions.md` as written:
  - Claude, in Claude Code;
  - Z.ai GLM 5.3, Mistral Large 4, DeepSeek V4.1 Flash (twice) and Kimi K3.1 Pro, in the Mistral Vibe CLI.
- The Vibe runs added a one-line waiver of the hook check in step 0, because this repository's Claude Code hooks don't run under Vibe.
- Every Vibe run used an explicit shell allowlist. Auto-approve was never used, because it bypasses Vibe's own denylist.
- AWS stayed reachable for preflight and the live rec search, as it was for the Claude run.
- Session journals were checked afterwards: no run filed a rec or called `ops_data_portal`, `aws` or `gh`.

**The verification.**
- **What was judged:**
  - the 13 findings raised beyond the 18 candidates, blinded as runs P/Q/R/S;
  - the 8 candidates the runs disagreed on, given only in the prompt's neutral wording.
- **Who judged:**
  - a fresh-context Claude Code subagent, as a benchmark;
  - GLM 5.3, DeepSeek V4.1 Flash and Kimi K3.1 Pro, under an identical read-only Vibe setup.
- **Blinding:** no verifier could read any run's deliverable.

## Results

| Run | Beyond-candidate findings (verifier consensus) | Clear candidate misses | Evidence anchors | List cost | Wall time |
|---|---|---|---|---|---|
| Claude (#1861) | 3: 2 SUPPORTED, 1 split; **CL-01 rated material by all four** | 0 | 33 | ~USD 28 equivalent (subscription) | ~1 h |
| DeepSeek V4.1 Flash, run 2 (#1877) | 4: 1 SUPPORTED, 3 PARTLY | 1 contested (C12) | 31 | ~USD 0.18 | 22 min |
| Kimi K3.1 Pro (#1878) | 2: both SUPPORTED | 0 | 18 | ~USD 8.4 | ~1 h 55 min |
| GLM 5.3 (#1872) | none | 1 (C12) | 8 | USD 3.94 | 27 min |
| Mistral Large 4 (#1874) | none | 0, plus a weak C2 dismissal | 13 | USD 3.07 | 54 min |
| DeepSeek V4.1 Flash, run 1 (#1875) | 4: the weakest set (harness-degraded) | 1 | 43 | ~USD 0.17 | 18.5 min |

**Quality ranking:**
1. Claude;
2. DeepSeek run 2 and Kimi, level;
3. GLM;
4. Mistral Large 4;
5. DeepSeek run 1, which run 2 supersedes.

## Findings

- **SPK-01.** DeepSeek V4.1 Flash executed the audit comparably to models far more expensive than it.
  - It cost about 1/155 of the Claude run's list-price equivalent.
  - Its citations were nearly as dense, and it raised original findings and ran with no help.
  - Its gaps are depth, some inflated severities and one contested candidate call.
- **SPK-02.** Only the Claude run produced a material finding beyond the candidate list: CL-01, where the code-review flip moves review behind a draft-PR push that the full-tier handoff gate guards. Depth of original thinking is where the expensive model led.
- **SPK-03.** Verifiers lean towards their own model's work, even blinded.
  - Self-preference was DeepSeek +0.58 and +0.33, Kimi +0.17 and Claude -0.11.
  - Every verifier that had a run of its own named it strongest in its summary.
- **SPK-04.** A second model surfaced every disagreement that mattered: C12, C15 and CL-05. Exact claim agreement between pairs of verifiers ranged from 6/13 to 9/13.
- **SPK-05.** The judges differ in strictness. GLM is the most lenient (never NOT-SUPPORTED, never "too high"), the Claude verifier the strictest, and DeepSeek the most central.
- **SPK-06.** Kimi is slow: about 55 s per step against about 7-11 s for DeepSeek and GLM on the identical verification task. It also carries procurement friction:
  - a UK VAT number is needed to buy as a business;
  - rate-limit tiers depend on cumulative top-ups;
  - its terms say a risk-control restriction cannot be lifted.
- **SPK-07.** Cost is dominated by re-sent context.
  - The cached-input price and the number of steps decide the bill.
  - A follow-up after the 1-hour cache expired re-wrote a 610k-token context at twice the input price.

## Direction

**The operator's conclusion.** The significant finding is SPK-01. It supports DeepSeek taking on gate roles such as decision-scout and code-review, run alongside Claude for comparison, with work to cover its limitations.

**How it fits the existing design.**
- That is the roadmap's existing path. T4.28 (code_reviewer), T4.29 (plan_critic) and T4.30 (decision_scout) add Actions verdict personas on DeepSeek via LiteLLM and dual-run them against the in-session Claude gates before any flip (Decisions 205, 229).
- It fits the model-portability stance of Decisions 43, 128 and 130. Decision 128 says "lower-tier models (Sonnet/Gemini/Deepseek) degrade on comprehension".
- This is evidence, not a Decision.

**Limitations to cover:**
- self-preference, so encourage second opinions on key forks (rec-4948);
- severity inflation, and controls missed in the very criteria a finding cites;
- less depth on original findings;
- no web access in these runs;
- harness sensitivity.

## Recommendations filed

- **rec-4943 (High):** the T4.28 flip collides with the full-tier handoff gate.
- **rec-4944:** no override path for a failing scout or critique verdict check.
- **rec-4945:** the flip items' `files_in_scope` omit `coordinator.yaml` and `capabilities.yaml`.
- **rec-4946:** no re-scout when a Decision a plan cites is amended on main.
- **rec-4947:** the Actions verdict runtime has no egress criterion.
- **rec-4948:** encourage a second-model opinion on key forks.

## Disposition

- Five run PRs are closed and their branches deleted: #1872, #1874, #1875, #1877 and #1878. Their summaries, findings and rejected candidates are kept in the YAML record.
- #1861 stays open, pending the operator's call: merge it as the audit of record, or close it too.
