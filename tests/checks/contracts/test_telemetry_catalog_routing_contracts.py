"""Standing guard: ports.yaml's PC-01 names the catalog-routed event reads (PLAN-telemetry-catalog-aware-capture)."""

from __future__ import annotations

from pathlib import Path

import yaml

from scripts.telemetry_capture import read_route

ROOT = Path(__file__).resolve().parents[3]
ROUTE = "scripts/telemetry_capture/read_route.py"


def test_pc01_names_the_routed_event_reads() -> None:
    ports = yaml.safe_load((ROOT / "docs" / "contracts" / "ports.yaml").read_text(encoding="utf-8"))
    pc01 = next(f for f in ports["coupling_findings"] if f["id"] == "PC-01")
    description = " ".join(str(pc01["description"]).split())
    assert ROUTE in description and (ROOT / ROUTE).is_file() and read_route.__file__.endswith(ROUTE)
    assert read_route.CATALOG_PARAMETER in description
    assert any("PLAN-telemetry-catalog-aware-capture" in entry["summary"] for entry in ports["amendment_log"])
