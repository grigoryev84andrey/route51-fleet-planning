"""Anonymization check (Layer-1 governance core).

Enforces the privacy policy a feed must satisfy BEFORE it is allowed into the
pipeline. Fail-closed: a breaching feed is quarantined, not ingested. Each check
is annotated with the ZRU-547 article it operationalises.

Checks (per feed, by privacy class):
  1. Forbidden fields        — design invariants (ATTO no alighting; telecom no
                               raw coordinates / raw network ids).
  2. Direct-identifier scan  — no emails, phone/MSISDN-like or PAN-like values in
                               any field [Art. 4 — definition of personal data].
  3. Pseudonym format        — IDENTIFIER fields must be hashes (>= N hex chars),
                               never raw numbers [Art. 4 / Art. 16 anonymization].
  4. Localization tag        — telecom records must be tagged in-country
                               [Art. 27-1, inserted by ZRU-666 (2021), amended by ZRU-1125 of 26.03.2026]. A rule check, not proof of legal compliance.
  (k-anonymity on aggregates is enforced in indexing.py and reported alongside.)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from .contracts import FeedContract

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_PHONE = re.compile(r"^\+?\d[\d\s\-]{8,14}$")     # 9-15 digit, MSISDN-like
_PAN = re.compile(r"\b\d{13,19}\b")               # bank card number
_HEXHASH = re.compile(r"^[0-9a-fA-F]+$")


@dataclass
class GovernanceResult:
    feed: str
    passed: bool
    violations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _scan_direct_identifiers(df: pd.DataFrame, contract: FeedContract, viol: list[str]) -> None:
    id_cols = set(contract.id_fields)
    for col in df.columns:
        s = df[col]
        if s.dtype != object:
            continue
        vals = s.dropna().astype(str).unique()
        if col in id_cols:
            continue  # pseudonym format handled separately
        hit_email = any(_EMAIL.search(v) for v in vals[:5000])
        hit_phone = any(_PHONE.match(v.strip()) for v in vals[:5000])
        hit_pan = any(_PAN.search(v) for v in vals[:5000])
        if hit_email:
            viol.append(f"[Art.4] column '{col}' contains email-like values")
        if hit_phone:
            viol.append(f"[Art.4] column '{col}' contains phone/MSISDN-like values")
        if hit_pan:
            viol.append(f"[Art.4] column '{col}' contains PAN-like (card-number) values")


def _check_pseudonyms(df: pd.DataFrame, contract: FeedContract, cfg, viol: list[str]) -> None:
    min_len = cfg["governance"]["hash_min_hex_len"]
    for col in contract.id_fields:
        if col not in df.columns:
            continue
        vals = df[col].dropna().astype(str).unique()
        bad = [v for v in vals[:5000]
               if not (_HEXHASH.match(v) and len(v) >= min_len)]
        if bad:
            ex = bad[0]
            viol.append(
                f"[Art.4/16] IDENTIFIER '{col}' is not a >= {min_len}-hex pseudonym "
                f"(e.g. '{ex[:24]}...'): looks like a raw identifier"
            )


def _check_localization(df: pd.DataFrame, contract: FeedContract, cfg, viol: list[str],
                        notes: list[str]) -> None:
    if not contract.localization_required:
        return
    region = cfg["governance"]["localization_region"]
    col = "processing_region"
    if col not in df.columns:
        viol.append(f"[Art.27-1] telecom feed missing in-country tag '{col}'")
        return
    offending = sorted(set(df[col].dropna().astype(str)) - {region})
    if offending:
        viol.append(f"[Art.27-1] '{col}' has out-of-country values {offending} "
                    f"(must be '{region}')")
    else:
        notes.append(f"[Art.27-1] localization tag present and in-country ('{region}')")


def check_feed(df: pd.DataFrame, contract: FeedContract, cfg) -> GovernanceResult:
    viol: list[str] = []
    notes: list[str] = []

    # 1. Forbidden fields (design invariants).
    present_forbidden = [c for c in contract.forbidden if c in df.columns]
    for c in present_forbidden:
        viol.append(f"forbidden field '{c}' present (invariant breach)")
    if contract.name == "atto" and not present_forbidden:
        notes.append("alighting-blindness invariant holds (no tap-out field)")

    # 2-4. Identifier / pseudonym / localization checks.
    if contract.is_personal:
        _scan_direct_identifiers(df, contract, viol)
        _check_pseudonyms(df, contract, cfg, viol)
        _check_localization(df, contract, cfg, viol, notes)
    else:
        # Non-personal feed: still confirm no PII slipped in.
        _scan_direct_identifiers(df, contract, viol)
        notes.append("non-personal feed: no pseudonymisation / k-anonymity required")

    return GovernanceResult(
        feed=contract.name,
        passed=len(viol) == 0,
        violations=viol,
        notes=notes,
        stats={"privacy_class": contract.privacy_class.value,
               "rows_scanned": int(len(df))},
    )
