"""Layer 5 — decision support: render already-computed results (schema 2).

Reads the layer-3 result object and, if given, the simulation analysis and the
economics result; recomputes nothing.

Usage:
  python run_layer5.py --output-dir DIR --input layer3_result.json=PATH
        [--input simulation_analysis.json=PATH] [--input economics_result.json=PATH]
        [--input governance_report.md=PATH]
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import runctx  # noqa: E402
from layer5.brief import render_brief  # noqa: E402
from layer5.dashboard import build_dashboard  # noqa: E402
from layer5.decision import build_record  # noqa: E402
from layer5.reports import render_equity_matrix, render_implementation_spec  # noqa: E402


def main() -> None:
    t0 = time.time()
    ctx = runctx.start(__file__, inputs=["layer3_result.json"],
                       optional_inputs=["simulation_analysis.json", "economics_result.json",
                                        "governance_report.md"])
    out = ctx.out

    def load(name):
        p = ctx.inputs.get(name)
        return json.loads(p.read_text(encoding="utf-8")) if p else None

    rec = build_record(load("layer3_result.json"), simulation=load("simulation_analysis.json"),
                       economics=load("economics_result.json"),
                       governance_report=ctx.inputs.get("governance_report.md"))
    (out / "decision_record.json").write_text(json.dumps(rec, indent=2, ensure_ascii=False),
                                              encoding="utf-8")
    (out / "DECISION_BRIEF.md").write_text(render_brief(rec), encoding="utf-8")
    (out / "EQUITY_POLICY_MATRIX.md").write_text(render_equity_matrix(rec), encoding="utf-8")
    (out / "IMPLEMENTATION_MONITORING_SPEC.md").write_text(render_implementation_spec(rec),
                                                           encoding="utf-8")
    (out / "results_page.html").write_text(build_dashboard(rec), encoding="utf-8")
    print(f"Layer 5 written to {out} ({time.time()-t0:.1f}s): decision_record.json, "
          "DECISION_BRIEF.md, EQUITY_POLICY_MATRIX.md, IMPLEMENTATION_MONITORING_SPEC.md, "
          "results_page.html")


if __name__ == "__main__":
    main()
