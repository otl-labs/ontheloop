# scripts/ - directory-scoped rules

Loaded automatically when Claude reads or edits files in this directory. Universal rules in repo-root `CLAUDE.md` still apply.

## Placement: root vs subpackage
`scripts/` root holds only entry points (run as `python -m scripts.<name>`) and genuine unclassed
singles. Any prefix family of >= 3 related modules is a subpackage, not loose root files -- the
existing `scripts/checks/`, `scripts/executor/`, `scripts/verifiers/` packages prove the pattern.

- Governs NEW files now: do not add a third `scripts/<prefix>_*.py` sibling at the root -- create
  `scripts/<prefix>/` and place it there.
- Only `ops_*` remains grandfathered un-nested (owner T-1.24); it migrates under the final RS-01
  subpackaging plan (rec-164) with a same-commit reference rewrite. Do not migrate it ad hoc.
- Nested homes so far (RS-01 / rec-164): `scripts/ci_rca/` (evidence, filing, taxonomy, tier_map,
  probe_health, back_validation, vacuous_pass), `scripts/session/` (preflight, postflight,
  metrics), `scripts/sync/` (ops, recommendations, ducklake_version), `scripts/roadmap/`
  (platform_roadmap, plan_document, plan_audit, find_plan -- names kept), `scripts/llm/`
  (client, utils -- prefix stripped; model_registry, github_models_client -- names kept).
  Pending: `scripts/ops/` (ops_data_portal; T-1.24; highest fan-out, deliberately deferred).
- The `scripts_root_allowlist` key in `docs/contracts/file-router.yaml` (enforced by
  `validate_placement`) now makes "scripts/ root = entry points + declared singles" machine-checked:
  every depth-1 `scripts/` file must be allowlisted or match a grandfathered glob (currently just
  the `ops_*` pair), or the build fails.

## Invocation
Always invoke `bin/venv-python` (never bare `python`/`python3`) -- the wrapper auto-detects the
OS and resolves the correct venv binary. Each Bash tool call is independent; do not rely on
`source .venv/bin/activate`.

## Adding a validate.py check
Checks are `@register(...)`-decorated and tier-sequenced by per-domain manifests (Decision 169,
amends Decision 104) -- `scripts/validate.py` is never touched. Add the module under
`scripts/checks/<domain>/`, decorate it, and add one `Entry(name=, module=, attr=, ...)` literal
(bare string literals only -- never `"module:attr"` or computed; see
`docs/contracts/check-manifest.yaml`) to that domain's `_manifest.py`. Set `pre=True` (+
`pre_globs=`) for `--pre` membership and `full_segment=` (`_schema.SEGMENT_TOKENS`) for full-tier
membership; omit both if invoked directly elsewhere (e.g. `validate_terraform_try`).

Dispatch and the full registration-surface list: see `scripts/checks/registry.py`.
`validate_check_manifests.py` enforces the grammar; mirror tests live at
`tests/checks/<domain>/test__manifest.py`.

## User-set settings
A new environment variable, secret or config key a user sets gets a row in
`docs/contracts/project-configuration.yaml` in the same change (Decision 224).
