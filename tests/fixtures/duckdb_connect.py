"""The one test-side seam for DuckDB connections that run production read-verb SQL (rec-4890).

Production's runtime issues SET threads=1 on every connection (src/common/ducklake_runtime.py); DuckDB 1.5.4's
parallel-window race (duckdb/duckdb#22846) only fires above one thread, so tests open at the runtime's count too.
duckdb is imported inside connect(), never at module scope (fast-tier importers must not pay for it).
"""

from __future__ import annotations

from typing import Any

RUNTIME_THREADS = 1


def connect() -> Any:
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    con.execute(f"SET threads={RUNTIME_THREADS}")
    return con
