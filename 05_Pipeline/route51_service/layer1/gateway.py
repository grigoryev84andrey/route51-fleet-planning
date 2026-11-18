"""Secure API gateway (Layer-1 ingestion boundary).

Single entry point every Layer-0 feed passes through:
    auth stub -> schema validation -> governance (anonymization) -> H3 indexing
Fail-closed: if governance fails and reject_on_violation is set, the feed is
quarantined (no indexed output is produced). In a real deployment this class sits
behind a managed API gateway; here it is the boundary object the HTTP layer wraps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd
import yaml

from .contracts import CONTRACTS, FeedContract
from .governance import GovernanceResult, check_feed
from .indexing import IndexResult, index_feed
from .reference import Reference, load_reference


class AuthError(Exception):
    pass


@dataclass
class IngestResult:
    feed: str
    accepted: bool
    n_in: int
    n_valid: int
    schema_errors: list[str] = field(default_factory=list)
    governance: GovernanceResult | None = None
    index: IndexResult | None = None


def load_service_config(path: str | Path) -> dict:
    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["_root"] = str(path.parent)
    out = path.parent / cfg["paths"]["output_dir"]
    out.mkdir(parents=True, exist_ok=True)
    cfg["paths"]["output_dir_abs"] = str(out)
    return cfg


def _validate_schema(df: pd.DataFrame, contract: FeedContract) -> list[str]:
    errs = []
    missing = [c for c in contract.required if c not in df.columns]
    if missing:
        errs.append(f"missing required columns: {missing}")
    return errs


class Gateway:
    def __init__(self, cfg: dict, reference: Reference):
        self.cfg = cfg
        self.ref = reference

    def _auth(self, api_key: str | None) -> None:
        expected = self.cfg["service"]["api_key"]
        if api_key != expected:
            raise AuthError("invalid or missing API key")

    def ingest(self, feed_name: str, df: pd.DataFrame, api_key: str | None = None) -> IngestResult:
        self._auth(api_key)
        if feed_name not in CONTRACTS:
            raise ValueError(f"unknown feed '{feed_name}'; registered: {list(CONTRACTS)}")
        contract = CONTRACTS[feed_name]
        n_in = len(df)

        # 1. Schema gate.
        schema_errors = _validate_schema(df, contract)
        if schema_errors:
            return IngestResult(feed=feed_name, accepted=False, n_in=n_in,
                                n_valid=0, schema_errors=schema_errors)

        # 2. Governance (anonymization).
        gov = check_feed(df, contract, self.cfg)
        if not gov.passed and self.cfg["governance"]["reject_on_violation"]:
            return IngestResult(feed=feed_name, accepted=False, n_in=n_in,
                                n_valid=0, governance=gov)

        # 3. H3 indexing (+ k-anon).
        idx = index_feed(df, contract, self.ref, self.cfg)
        return IngestResult(feed=feed_name, accepted=True, n_in=n_in,
                            n_valid=int(len(idx.indexed)), governance=gov, index=idx)


def build_gateway(config_path: str | Path) -> tuple[Gateway, dict]:
    cfg = load_service_config(config_path)
    ref = load_reference(cfg)
    return Gateway(cfg, ref), cfg
