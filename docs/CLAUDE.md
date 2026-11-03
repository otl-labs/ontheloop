# docs/ - directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in repo-root `CLAUDE.md` still apply.

## docs/ root allowlist (RS-03)
The `docs/` root (depth-1 files) holds ONLY canonical governance surfaces; every other class lives
in a subtree. A new file at the docs root fails `validate_placement` unless it is an allowlisted
governance file or a grandfathered retiring-set member. The machine-readable allowlist is the
`docs_root_allowlist:` key in `docs/contracts/file-router.yaml` (the single source of truth the
check reads) -- register a genuinely-new governance surface there, never by loosening the check.

Allowed governance root files: `CLAUDE.md` (this file), `PROJECT_CONTEXT.md`,
`decisions-index.json`, and the roadmaps (`ROADMAP-PLATFORM.yaml` with its retired-item archive
`ROADMAP-PLATFORM-ARCHIVE.yaml`, `ROADMAP-SEMANTO.yaml`).

Grandfathered retiring sets (allowed now, retire on their own schedule -- do not add to them):
`DECISIONS.md` / `DECISIONS_ARCHIVE.md` (owner T1.5), `SESSION_LOG_ARCHIVE.md`
(owner T2.26 / T-1.9).

## Class -> home map
Every non-governance doc class has a subtree home; put new files there, not at the root:

| Class | Home |
|---|---|
| Plan documents (`PLAN-{slug}.yaml`) | `docs/plans/` |
| Machine-readable contracts | `docs/contracts/` |
| Audit prompts (`AUDIT-{slug}.md`) | `docs/audit-prompts/` |
| REPORT-ONLY deliverables + spike notes | `docs/plans/reports/` |
| Operator procedures (agent-led) | `procedure:` blocks in `docs/contracts/*.yaml` |
| Work-item pilot fixture (CD.45 one-off, provisional_v0; not a roadmap) | `docs/work-item-pilot/` |

Example: `docs/contracts/package-registry-reservation.yaml`'s `procedure:` block holds the
PyPI/npm/crates.io namespace-reservation walkthrough (rec-3941).
`docs/contracts/github-org-transfer.yaml`'s `procedure:` block holds the GitHub organisation
transfer's pre-stage steps and transfer-window checklist (rec-3905).

Audit OUTPUTS live in `audits/`, not under `docs/`. The discovery index is
`docs/contracts/file-router.yaml`.

`docs/runbooks/` is a RETIRING class (Decision 127): the existing
`docs/runbooks/ducklake-catalog-operations.md` is grandfathered, but no new file may be added
there -- new operator procedures are `procedure:` blocks in the owning contract, per the row above.

## Plans lifecycle (RS-07)
`.md` is the retired pre-T1.11 planning format, superseded one-way by the schema-validated
`.yaml` format (Decision 85, amended by Decision 174: the superseded format carries no
working-tree retention obligation). Retirement is by deletion, with provenance in git history --
`docs/plans/archive/` is no longer a live destination; no `.md` plan exists in the working tree
and none may be newly authored there.

`docs/plans/` root holds ACTIVE `.yaml` plans (not yet merged-and-verified) plus the still-at-root
completed `.yaml` history. A `.yaml` plan's "completed" status is a judgment call
(merged-and-verified), not a cheap extension check, so its archival stays deliberately LAGGED to a
future, separately-scoped sweep pending upload to the DuckLake warehouse table -- this avoids a
standing done/not-done classifier in the hot `/plan` and `/implement` load path. Until that lagged
sweep lands, completed `.yaml` plans remain at the `docs/plans/` root alongside active ones; do
not hand-move an individual `.yaml` plan out of it outside that sweep.

## Names (Decision 240)
`docs/contracts/glossary.yaml` holds the names that replace "platform" and "product" in new governed
text; text older than Decision 240 reads through its read_as rows, never by rewriting it.
