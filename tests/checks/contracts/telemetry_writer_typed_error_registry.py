"""Registry pinning each (verb, typed_error) pair of the telemetry writer contract to the test that asserts it (data only)."""

from __future__ import annotations

W = "tests/lambdas/ducklake_telemetry_writer/"
C = "tests/common/"
H = W + "test_handler.py::"
E = W + "test_telemetry_edge.py::"
TA = W + "test_telemetry_actions.py::"
CA = C + "test_ducklake_telemetry_append.py::"
CB = C + "test_ducklake_telemetry_blobs.py::"
CR = C + "test_ducklake_telemetry_registration.py::"

# (verb, typed_error) -> (test node, basis, anchor). verb: the node drives that verb's real entry to the error, anchored by the
# quoted action literal, the verb's entry function or a same-file helper naming the literal; layer: the node calls the anchor,
# a function on the verb's path below its entry; shared: the node proves a handler-wide answer through any action or a stub.
TYPED_ERROR_TESTS: dict[tuple[str, str], tuple[str, str, str | None]] = {
    ("append_telemetry", "bad_request"): (E + "test_request_errors_translate_by_type", "verb", "body_of"),
    ("append_telemetry", "blob_not_verified"): (
        CA + "test_unknown_project_blob_row_is_422_before_any_head",
        "layer",
        "run_blob",
    ),
    ("append_telemetry", "blob_store_unavailable"): (
        CA + "test_store_unavailable_is_503_and_opens_no_transaction",
        "layer",
        "run_blob",
    ),
    ("append_telemetry", "mixed_producer"): (E + "test_request_errors_translate_by_type", "verb", "body_of"),
    ("append_telemetry", "payload_too_large"): (E + "test_caps_are_413_at_the_boundaries", "verb", "body_of"),
    ("append_telemetry", "project_unregistered"): (
        CA + "test_unregistered_reserved_ref_is_refused_before_any_transaction",
        "layer",
        "run",
    ),
    ("append_telemetry", "row_rule"): (
        E + "test_inline_content_above_the_threshold_is_the_kernels_row_rule",
        "verb",
        "body_of",
    ),
    ("append_telemetry", "schema_gate"): (E + "test_request_errors_translate_by_type", "verb", "body_of"),
    ("append_telemetry", "tenant_unresolved"): (E + "test_tenant_is_read_once_and_unresolved_is_403", "verb", "body_of"),
    ("append_telemetry", "too_many_blobs"): (CA + "test_blob_rows_over_the_cap_are_413", "layer", "run_blob"),
    ("append_telemetry", "too_many_rows"): (E + "test_caps_are_413_at_the_boundaries", "verb", "body_of"),
    ("append_telemetry", "write_order"): (E + "test_request_errors_translate_by_type", "verb", "body_of"),
    ("begin_blobs", "bad_request"): (E + "test_begin_blobs_edge_response_and_refusals", "verb", "begin"),
    ("begin_blobs", "blob_not_verified"): (CB + "test_begin_blobs_size_mismatch_is_422_and_grants_nothing", "layer", "run"),
    ("begin_blobs", "blob_store_unavailable"): (CB + "test_begin_blobs_dedupes_and_counts", "layer", "run"),
    ("begin_blobs", "project_unregistered"): (CB + "test_begin_blobs_reserved_ref_is_403_before_any_head", "layer", "run"),
    ("begin_blobs", "projection_defect"): (
        E + "test_describe_verbs_and_the_payload_rule_come_from_the_projection",
        "layer",
        "payload_rule",
    ),
    ("begin_blobs", "tenant_unresolved"): (E + "test_begin_blobs_edge_response_and_refusals", "verb", "begin"),
    ("begin_blobs", "too_many_blobs"): (E + "test_begin_blobs_edge_response_and_refusals", "verb", "begin"),
    ("describe", "projection_defect"): (
        E + "test_describe_verbs_and_the_payload_rule_come_from_the_projection",
        "layer",
        "payload_rule",
    ),
    ("describe", "telemetry_env_unset"): (H + "test_describe_with_env_unset_is_500", "verb", '"describe"'),
    ("provision_telemetry", "bad_request"): (
        TA + "test_provision_refuses_force_recreate_keys",
        "verb",
        '"provision_telemetry"',
    ),
    ("provision_telemetry", "runtime"): (
        H + "test_provision_absent_tables_maps_to_500_runtime",
        "verb",
        '"provision_telemetry"',
    ),
    ("register_tenant", "bad_request"): (
        TA + "test_register_tenant_refuses_principal_parameters",
        "verb",
        '"register_tenant"',
    ),
    ("register_tenant", "row_rule"): (CR + "test_row_rule_violation_writes_nothing", "layer", "resolve_or_register_tenant"),
    ("register_project", "bad_request"): (
        H + "test_invalid_request_is_refused_before_any_connection",
        "verb",
        '"register_project"',
    ),
    ("register_project", "tenant_unresolved"): (
        TA + "test_register_project_requires_a_registered_tenant",
        "verb",
        '"register_project"',
    ),
}
# shared rows: (typed error, node) -> the verbs whose contract declares it
SHARED_ROWS = (
    ("blob_grant_self_check", H + "test_blob_faults_answer_a_typed_500", ("append_telemetry", "begin_blobs")),
    ("blob_integrity", H + "test_blob_faults_answer_a_typed_500", ("append_telemetry", "begin_blobs")),
    ("blob_store_access", H + "test_blob_faults_answer_a_typed_500", ("append_telemetry", "begin_blobs")),
    (
        "catalog_unavailable",
        H + "test_dead_connection_at_connect_is_503",
        ("append_telemetry", "begin_blobs", "provision_telemetry", "register_tenant", "register_project"),
    ),
    ("grain_conflict", H + "test_error_map_rows_for_kernel_errors", ("append_telemetry",)),
    (
        "missing_caller_identity",
        H + "test_missing_or_partial_identity_is_403",
        ("append_telemetry", "begin_blobs", "describe", "provision_telemetry", "register_tenant", "register_project"),
    ),
    (
        "occ_exhausted",
        H + "test_error_map_rows_for_registration_errors",
        ("begin_blobs", "provision_telemetry", "register_tenant", "register_project"),
    ),
    ("occ_exhausted", H + "test_masked_exhaustion_logs_its_retry_count", ("append_telemetry",)),
    ("row_rule", H + "test_error_map_rows_for_registration_errors", ("begin_blobs", "register_project")),
    ("runtime", H + "test_error_map_rows_for_registration_errors", ("register_tenant", "register_project")),
    ("runtime", H + "test_runtime_error_is_500", ("append_telemetry", "begin_blobs")),
    (
        "schema_gate",
        H + "test_error_map_rows_for_registration_errors",
        ("begin_blobs", "provision_telemetry", "register_tenant", "register_project"),
    ),
    (
        "telemetry_env_unset",
        H + "test_unset_env_is_500",
        ("append_telemetry", "begin_blobs", "provision_telemetry", "register_tenant", "register_project"),
    ),
    (
        "unmapped",
        H + "test_unexpected_exception_is_a_typed_500",
        ("append_telemetry", "begin_blobs", "describe", "provision_telemetry", "register_tenant", "register_project"),
    ),
)
TYPED_ERROR_TESTS.update({(v, e): (node, "shared", None) for e, node, verbs in SHARED_ROWS for v in verbs})
