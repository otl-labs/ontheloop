"""Lambda handlers with no action registry, and why, for test_write_time_enforcement_conformance.py (Decision 210)."""

from __future__ import annotations

REGISTRY_LESS = {
    "src/lambdas/ducklake_catalog_dr/handler.py": "catalog disaster-recovery job: dump and restore, no row write",
    "src/data/handlers/scheduled_agent_handler.py": "dispatches scheduled agents, writes no warehouse row through the writer",
    "src/data/handlers/findings_processor_handler.py": "findings processor, writes through the portal, not a writer verb",
    "src/data/handlers/workflow_dispatcher_handler.py": "starts register workflows through the GitHub API, no warehouse row",
}
