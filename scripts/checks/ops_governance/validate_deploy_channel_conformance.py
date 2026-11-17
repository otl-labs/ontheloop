"""Deploy-channel conformance staleness gate (Decision 104, Decision 125/126).

Compares the ACTUAL terraform/personal state (does each Lambda's aws_lambda_function resource
carry lifecycle { ignore_changes = [source_code_hash] }?) against the textual SoT surfaces that
describe it -- docs/contracts/build-lambda.yaml's deploy_channels + docs/contracts/
environment-taxonomy.yaml's typed conformance.ducklake_class / conformance.prod_class fields --
and fails if either has drifted from reality. This is the guard against the #544 drift class: the
code shipped decoupled but the docs kept describing it as coupled.

Covers TWO classes, each with its own actual-state scan + typed conformance field (T2.43
extension; CFG-11 conversion repointed the taxonomy read from prose-paragraph regex matching to
the typed field):
  - ducklake (terraform/personal/ducklake*.tf; build-lambda.yaml deploy_channels.ducklake_functions;
    environment-taxonomy.yaml's conformance.ducklake_class -- the ORIGINAL, unwidened check).
  - prod (terraform/personal/prod_lambdas.tf and scheduled_workflow_dispatch.tf;
    build-lambda.yaml deploy_channels.prod_functions;
    environment-taxonomy.yaml's conformance.prod_class). The prod class
    additionally gets a completeness check (governed_channel + break_glass_only both populated)
    since its channel_class value ("decoupled_build_pipeline") does not use the ducklake class's
    _decoupled/_coupled suffix convention, so there is no channel_class-vs-actual comparison to
    make for it.
"""

from __future__ import annotations

import re

from scripts.checks import _common, registry

_DUCKLAKE_TF_GLOB = "ducklake*.tf"
_PROD_TF_GLOBS = ("prod_lambdas.tf", "scheduled_workflow_dispatch.tf")
_IGNORE_CHANGES_RE = re.compile(r"ignore_changes\s*=\s*\[[^\]]*source_code_hash[^\]]*\]")
_FUNCTION_BLOCK_RE = re.compile(r'resource\s+"aws_lambda_function"\s+"(\w+)"\s*\{')

_PROD_CHANNEL_KEYS = ("prod_functions",)


def _extract_function_blocks(text: str) -> dict[str, str]:
    """Return {resource_name: block_text} for every aws_lambda_function resource, via
    brace-depth counting from the opening brace (regex alone cannot match nested braces)."""
    blocks: dict[str, str] = {}
    for match in _FUNCTION_BLOCK_RE.finditer(text):
        name = match.group(1)
        start = match.end() - 1
        depth = 0
        i = start
        while i < len(text):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        blocks[name] = text[start : i + 1]
    return blocks


def _actual_decoupled_state(
    failed: list[str],
    glob_pattern: str | tuple[str, ...] = _DUCKLAKE_TF_GLOB,
    class_label: str = "ducklake",
) -> bool | None:
    """Scan terraform/personal/<glob_pattern> for aws_lambda_function resources.

    Generalised (T2.43) via glob_pattern + class_label so the same scan logic serves both the
    ducklake class (default args, unchanged behaviour) and the prod class
    (glob_pattern=_PROD_TF_GLOBS, class_label="prod").

    Returns True if every found function is decoupled (ignore_changes=[source_code_hash]),
    False if every found function is coupled (none carry it), or None (with a failure
    appended) if no functions were found or the state is a partial/mixed rollout --
    neither is a state the two textual SoT surfaces can validly agree with.
    """
    personal_dir = _common.ROOT / "terraform" / "personal"
    decoupled_count = 0
    total_count = 0
    patterns = (glob_pattern,) if isinstance(glob_pattern, str) else glob_pattern
    tf_paths = sorted({path for pattern in patterns for path in personal_dir.glob(pattern)})
    for tf_path in tf_paths:
        try:
            text = tf_path.read_text(encoding="utf-8")
        except OSError as exc:
            failed.append(f"Deploy-channel conformance: cannot read {tf_path}: {exc}")
            return None
        for _name, block in _extract_function_blocks(text).items():
            total_count += 1
            if _IGNORE_CHANGES_RE.search(block):
                decoupled_count += 1

    if total_count == 0:
        failed.append(
            f"Deploy-channel conformance: no aws_lambda_function resources found under "
            f"{personal_dir}/{','.join(patterns)} -- cannot determine actual {class_label} coupling state."
        )
        return None
    if 0 < decoupled_count < total_count:
        failed.append(
            f"Deploy-channel conformance: {decoupled_count}/{total_count} {class_label} "
            "aws_lambda_function resources carry ignore_changes=[source_code_hash] -- "
            "partial rollout is not a state either textual SoT can validly describe."
        )
        return None
    return decoupled_count == total_count


def _doc_state_from_channel_class(failed: list[str]) -> bool | None:
    """Parse build-lambda.yaml's deploy_channels.ducklake_functions.channel_class.

    Returns True for a channel_class ending in '_decoupled', False for one ending in
    '_coupled' (checked in that order since 'decoupled' ends in 'coupled' as a
    substring -- the '_decoupled' suffix match must run first), or None (with a
    failure appended) on a missing/unparseable/unrecognised value.
    """
    import yaml as _yaml  # noqa: PLC0415

    path = _common.ROOT / "docs" / "contracts" / "build-lambda.yaml"
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, _yaml.YAMLError) as exc:
        failed.append(f"Deploy-channel conformance: cannot read/parse {path}: {exc}")
        return None
    channel_class = (
        (data or {}).get("deploy_channels", {}).get("ducklake_functions", {}).get("channel_class")
        if isinstance(data, dict)
        else None
    )
    if not isinstance(channel_class, str) or not channel_class:
        failed.append(f"Deploy-channel conformance: {path} missing deploy_channels.ducklake_functions.channel_class.")
        return None
    if channel_class.endswith("_decoupled"):
        return True
    if channel_class.endswith("_coupled"):
        return False
    failed.append(
        f"Deploy-channel conformance: {path} channel_class {channel_class!r} does not end in "
        "'_coupled' or '_decoupled' -- cannot determine claimed state."
    )
    return None


def _prod_channels_complete(failed: list[str]) -> bool:
    """Parse build-lambda.yaml's deploy_channels.prod_functions for
    governed_channel + break_glass_only completeness.

    The prod class's channel_class ("decoupled_build_pipeline") does not use the ducklake
    class's _decoupled/_coupled suffix convention, so there is no channel_class-vs-actual
    coupling comparison to make for it (unlike _doc_state_from_channel_class above) -- this
    checks field COMPLETENESS instead: both governed_channel and break_glass_only must be
    populated for each prod-class deploy_channels entry. Returns True iff every entry is
    complete; appends a failure (and returns False) for each incomplete entry.
    """
    import yaml as _yaml  # noqa: PLC0415

    path = _common.ROOT / "docs" / "contracts" / "build-lambda.yaml"
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, _yaml.YAMLError) as exc:
        failed.append(f"Deploy-channel conformance: cannot read/parse {path}: {exc}")
        return False

    channels = (data or {}).get("deploy_channels", {}) if isinstance(data, dict) else {}
    complete = True
    for key in _PROD_CHANNEL_KEYS:
        entry = channels.get(key) if isinstance(channels, dict) else None
        entry = entry if isinstance(entry, dict) else {}
        if not entry.get("governed_channel") or not entry.get("break_glass_only"):
            failed.append(
                f"Deploy-channel conformance: {path} deploy_channels.{key} missing governed_channel and/or break_glass_only."
            )
            complete = False
    return complete


def _taxonomy_state(
    failed: list[str],
    class_key: str = "ducklake_class",
    label: str = "ducklake",
) -> bool | None:
    """Read docs/contracts/environment-taxonomy.yaml's typed conformance.<class_key> field.

    Generalised (T2.43) via class_key + label so the same typed-field read serves both the
    ducklake class (default args, unchanged behaviour) and the prod class
    (class_key="prod_class", label="prod"). A MISSING key fails loudly rather than defaulting --
    a typed read that silently defaults on a missing key is exactly the silent-pass shape this
    check must not introduce (CFG-11 conversion)."""
    import yaml as _yaml  # noqa: PLC0415

    path = _common.ROOT / "docs" / "contracts" / "environment-taxonomy.yaml"
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, _yaml.YAMLError) as exc:
        failed.append(f"Deploy-channel conformance: cannot read/parse {path}: {exc}")
        return None

    conformance = (data or {}).get("conformance") if isinstance(data, dict) else None
    if not isinstance(conformance, dict) or class_key not in conformance:
        failed.append(
            f"Deploy-channel conformance: {path} missing conformance.{class_key} -- cannot determine "
            f"claimed {label} conformance state."
        )
        return None

    value = conformance.get(class_key)
    if value == "DECOUPLED":
        return True
    if value == "COUPLED":
        return False
    failed.append(
        f"Deploy-channel conformance: {path} conformance.{class_key} value {value!r} is neither "
        "'DECOUPLED' nor 'COUPLED' -- ambiguous conformance status."
    )
    return None


@registry.register("validate_deploy_channel_conformance", owner="platform")
def validate_deploy_channel_conformance(failed: list[str]) -> None:
    """Fail if build-lambda.yaml or environment-taxonomy.yaml disagree with the actual
    terraform/personal ignore_changes=[source_code_hash] state, for EITHER the ducklake class or
    the prod class (T2.43 -- closes the ducklake-only blind spot)."""
    print("\n=== Deploy-channel conformance gate (Decision 125/126) ===")

    pre_count = len(failed)

    # --- ducklake class (original check, unwidened) ---
    actual = _actual_decoupled_state(failed)
    doc_state = _doc_state_from_channel_class(failed)
    taxonomy_state = _taxonomy_state(failed)

    if actual is not None and doc_state is not None and taxonomy_state is not None:
        if doc_state != actual:
            failed.append(
                "Deploy-channel conformance: build-lambda.yaml channel_class claims "
                f"{'decoupled' if doc_state else 'coupled'} but terraform/personal is actually "
                f"{'decoupled' if actual else 'coupled'} -- update deploy_channels.ducklake_functions.channel_class."
            )
        if taxonomy_state != actual:
            failed.append(
                "Deploy-channel conformance: environment-taxonomy.yaml conformance.ducklake_class claims "
                f"{'DECOUPLED' if taxonomy_state else 'COUPLED'} but terraform/personal is actually "
                f"{'decoupled' if actual else 'coupled'} -- update the conformance.ducklake_class field."
            )

    # --- prod class (T2.43 extension) ---
    actual_prod = _actual_decoupled_state(failed, glob_pattern=_PROD_TF_GLOBS, class_label="prod")
    prod_taxonomy_state = _taxonomy_state(failed, class_key="prod_class", label="prod")
    _prod_channels_complete(failed)

    if actual_prod is not None and prod_taxonomy_state is not None and prod_taxonomy_state != actual_prod:
        failed.append(
            "Deploy-channel conformance: environment-taxonomy.yaml conformance.prod_class "
            f"claims {'DECOUPLED' if prod_taxonomy_state else 'COUPLED'} but terraform/personal is "
            f"actually {'decoupled' if actual_prod else 'coupled'} -- update the conformance.prod_class field."
        )

    registry.examined(2, unit="deploy_channel_classes")
    if len(failed) == pre_count:
        print(
            "  PASS: docs and terraform/personal agree "
            f"(ducklake: {'decoupled' if actual else 'coupled'}; prod: {'decoupled' if actual_prod else 'coupled'})."
        )
