"""Derived output columns of the usage verbs (model_usage_rollup, tool_use_rollup, session_startup_baseline), in SQL order.

Pure data, no imports; the response stamps are appended by src.common.ducklake_reader_client.VERB_FIELDS.
"""

MODEL_USAGE_FIELDS = (
    "root_session_id",
    "harness_session_key",
    "branch",
    "workflow",
    "session_started_at",
    "last_event_timestamp",
    "roots_in_tree",
    "model",
    "session_count",
    "model_call_count",
    "tokens_input_total",
    "tokens_output_total",
    "tokens_cache_read_total",
    "tokens_cache_creation_total",
    "cache_creation_1h_total",
    "cache_creation_5m_total",
    "usage_missing_count",
)

TOOL_USE_FIELDS = (
    "root_session_id",
    "harness_session_key",
    "branch",
    "session_started_at",
    "roots_in_tree",
    "is_root",
    "agent_name",
    "workflow",
    "tool_name",
    "mcp_server",
    "attribution_mcp_server",
    "call_count",
    "error_count",
    "blocked_count",
    "interrupted_count",
    "session_count",
)

STARTUP_BASELINE_FIELDS = (
    "session_id",
    "root_session_id",
    "parent_session_id",
    "is_root",
    "harness_session_key",
    "branch",
    "workflow",
    "agent_name",
    "session_started_at",
    "roots_in_tree",
    "model_call_count",
    "first_call_at",
    "model",
    "tokens_input",
    "tokens_cache_read",
    "tokens_cache_creation",
    "cache_creation_1h_tokens",
    "cache_creation_5m_tokens",
    "tokens_output",
    "usage_missing",
)
