"""HTTP face of the Secure API gateway (thin wrapper over Gateway).

Real deployment would run this behind a managed API gateway (TLS, rate limiting,
key rotation). Here it exists so the ingestion boundary is a concrete, testable
endpoint. Run: `uvicorn api:app`  (or drive it with fastapi.testclient).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from layer1.contracts import CONTRACTS
from layer1.gateway import AuthError, build_gateway

ROOT = Path(__file__).resolve().parent


class IngestBody(BaseModel):
    records: list[dict]


def create_app(config_path: str | Path = ROOT / "service_config.yaml") -> FastAPI:
    gateway, cfg = build_gateway(config_path)
    app = FastAPI(title="Route-51 Service — Layer 1 gateway")

    @app.get("/health")
    def health():
        return {"status": "ok", "stops": len(gateway.ref.stops),
                "grid_cells": len(gateway.ref.grid)}

    @app.get("/contracts")
    def contracts():
        return [{"feed": n, "privacy_class": c.privacy_class.value,
                 "localization_required": c.localization_required,
                 "required_fields": c.required} for n, c in CONTRACTS.items()]

    @app.post("/ingest/{feed}")
    def ingest(feed: str, body: IngestBody, x_api_key: str | None = Header(default=None)):
        df = pd.DataFrame(body.records)
        try:
            res = gateway.ingest(feed, df, api_key=x_api_key)
        except AuthError as e:
            raise HTTPException(status_code=401, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        out = {"feed": res.feed, "accepted": res.accepted,
               "n_in": res.n_in, "n_valid": res.n_valid,
               "schema_errors": res.schema_errors}
        if res.governance:
            out["governance"] = {
                "passed": res.governance.passed,
                "privacy_class": res.governance.stats.get("privacy_class"),
                "violations": res.governance.violations,
                "notes": res.governance.notes,
            }
        if res.index:
            out["indexing"] = {
                "cells_touched": res.index.stats["cells_touched"],
                "in_grid_share": res.index.stats["in_grid_share"],
                "k_anon_suppressed": res.index.stats["cells_suppressed"],
            }
        return out

    return app


app = create_app()
