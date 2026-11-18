"""Result contract, schema version 2 — one validator for every consumer.

Economics, the console and the export must reach the same verdict on whether a
simulation result can stand behind a given plan. This module holds that verdict:
the status vocabulary, the applicability rule, and the fingerprints it compares.

Separate fingerprints, never one blended hash:

  input     — the demand data and resolved configuration the plan was computed on
  plan      — the executable plan of one condition (baseline and candidate differ
              by definition; each is checked against its own expected plan)
  model     — model / environment versions
  protocol  — the frozen analysis protocol
  run       — one run: the four above plus condition and seed

A file hash (`file_sha256`) and a semantic fingerprint are different fields.
Scientific inputs are hashed at full precision: floats are serialised with
Python's round-trip `repr`, never rounded. Order is kept for sequences (route
order is meaningful); unordered collections must be sorted explicitly by the
caller, and OD tables are sorted by (board_stop, alight_stop, hour) here.

Results written before schema 2 are read only through `from_legacy_simulation`,
which marks them as historical references. They never confirm a plan.
"""
from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

INPUT_MATCH = ("exact", "different", "unknown")
PLAN_MATCH = ("exact", "different", "unknown")
EVIDENCE_KIND = ("model_only", "historical_reference", "exploratory", "confirmatory")
EXECUTION_STATE = ("pending", "running", "completed", "failed", "interrupted")
COMPLETION_STATUS = ("not_checked", "complete", "incomplete")
PROTOCOL_STATUS = ("not_checked", "passed", "failed")
EFFECT_STATUS = ("improved", "worsened", "inconclusive", "not_tested")
OPERATIONAL_FEASIBILITY = ("unknown", "failed", "verified")

STATUS_FIELDS = {
    "input_match": INPUT_MATCH,
    "plan_match": PLAN_MATCH,
    "evidence_kind": EVIDENCE_KIND,
    "execution_state": EXECUTION_STATE,
    "completion_status": COMPLETION_STATUS,
    "protocol_status": PROTOCOL_STATUS,
    "effect_status": EFFECT_STATUS,
    "operational_feasibility": OPERATIONAL_FEASIBILITY,
}

# Reason codes used below. Consumers show `message`; the code is for tests and logs.
ISSUE_MESSAGES = {
    "no_result": "No simulation result is available for this plan.",
    "schema_unsupported": "Result schema is not version 2; read it through the historical adapter.",
    "legacy_schema": "Result predates schema 2 and is kept as a historical reference only.",
    "historical_issues_remain": "Historical run — validation issues remain.",
    "bad_status_value": "A status field has a value outside the contract.",
    "input_different": "The result was computed on different input data.",
    "input_unknown": "The input data of the result cannot be matched.",
    "plan_different": "The result simulated a different plan.",
    "plan_unknown": "The simulated plan cannot be matched.",
    "protocol_different": "The result was produced under a different protocol.",
    "model_different": "The result was produced under a different model or environment.",
    "baseline_different": "The baseline timetable differs from the registered result.",
    "cohort_count_different": "The number of runs differs from the registered protocol.",
    "primary_not_defined": "The registered primary outcome is not defined.",
    "invalid_runs_present": "The registered series contains invalid runs.",
    "not_completed": "The computation did not complete.",
    "incomplete_cohort": "Incomplete run — the measured group did not finish.",
    "completion_not_checked": "Completion of the measured group was not checked.",
    "protocol_failed": "The registered protocol checks failed.",
    "protocol_not_checked": "The result was not checked against a registered protocol.",
    "not_confirmatory": "Technical or exploratory run — not confirmatory evidence.",
    "not_registered": "The series is not registered as confirmatory.",
}


# --------------------------------------------------------------------------- #
# Canonical serialisation and fingerprints
# --------------------------------------------------------------------------- #
def _canon(x: Any) -> Any:
    """Convert to JSON-safe values without losing precision or order."""
    # numpy / pandas scalars expose .item(); do not import numpy here.
    if hasattr(x, "item") and not isinstance(x, (str, bytes)) and getattr(x, "ndim", 0) == 0:
        try:
            x = x.item()
        except (AttributeError, ValueError):
            pass
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, int):
        return x
    if isinstance(x, float):
        if not math.isfinite(x):
            raise ValueError(f"non-finite number in fingerprinted data: {x!r}")
        return {"__float__": repr(x)} if x != int(x) or abs(x) >= 2**53 else x
    if isinstance(x, Mapping):
        keys = list(x.keys())
        if not all(isinstance(k, str) for k in keys):
            raise TypeError("fingerprinted mappings must have string keys")
        return {k: _canon(x[k]) for k in sorted(keys)}
    if isinstance(x, (set, frozenset)):
        raise TypeError("sets have no defined order; sort them explicitly before fingerprinting")
    if isinstance(x, (list, tuple)):
        return [_canon(v) for v in x]
    if hasattr(x, "tolist"):
        return _canon(x.tolist())
    raise TypeError(f"cannot fingerprint value of type {type(x).__name__}")


def canonical_json(obj: Any) -> str:
    return json.dumps(_canon(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def fingerprint(kind: str, obj: Any) -> str:
    """Semantic fingerprint `<kind>:sha256:<hex>` of a canonicalised object."""
    h = hashlib.sha256()
    h.update(kind.encode("utf-8") + b"\x00" + str(SCHEMA_VERSION).encode() + b"\x00")
    h.update(canonical_json(obj).encode("utf-8"))
    return f"{kind}:sha256:{h.hexdigest()}"


def file_sha256(path: str | Path) -> str:
    """Byte hash of a file — not a semantic fingerprint."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


OD_KEYS = ("board_stop", "alight_stop", "hour")


def od_records(df, value_col: str) -> list[list]:
    """OD table as rows sorted by (board_stop, alight_stop, hour), full precision."""
    missing = [c for c in (*OD_KEYS, value_col) if c not in df.columns]
    if missing:
        raise ValueError(f"OD table lacks columns {missing}")
    d = df[[*OD_KEYS, value_col]].sort_values(list(OD_KEYS), kind="mergesort")
    if d.duplicated(subset=list(OD_KEYS)).any():
        raise ValueError("OD table has duplicate (board_stop, alight_stop, hour) keys")
    return [[str(b), str(a), int(h), float(v)] for b, a, h, v in d.itertuples(index=False)]


def input_fingerprint(demand_df, resolved_config: Mapping, *,
                      value_col: str = "demand", extra: Mapping | None = None) -> str:
    """Demand rows (sorted) + configuration after all values are resolved."""
    return fingerprint("input", {
        "demand": od_records(demand_df, value_col),
        "config": dict(resolved_config),
        "extra": dict(extra or {}),
    })


def plan_fingerprint(plan: Mapping | list) -> str:
    """Executable plan of one condition. Sequences keep their order."""
    return fingerprint("plan", plan)


def model_fingerprint(versions: Mapping) -> str:
    return fingerprint("model", dict(versions))


def protocol_fingerprint(protocol: Mapping) -> str:
    return fingerprint("protocol", dict(protocol))


def run_fingerprint(*, input_fp: str, plan_fp: str, model_fp: str,
                    protocol_fp: str | None, condition: str, seed: int) -> str:
    return fingerprint("run", {
        "input": input_fp, "plan": plan_fp, "model": model_fp,
        "protocol": protocol_fp, "condition": condition, "seed": int(seed),
    })


# --------------------------------------------------------------------------- #
# Values that may be unknown
# --------------------------------------------------------------------------- #
def unknown(reason_code: str, message: str | None = None) -> dict:
    """A numeric field without evidence: value None and a reason, never 0."""
    return {"value": None, "reason_code": reason_code,
            "message": message or ISSUE_MESSAGES.get(reason_code, reason_code)}


def known(value: float, **meta) -> dict:
    return {"value": value, "reason_code": None, **meta}


def issue(code: str, message: str | None = None) -> dict:
    return {"code": code, "message": message or ISSUE_MESSAGES.get(code, code)}


# --------------------------------------------------------------------------- #
# Effect classification (plan S15): Δ = baseline − candidate
# --------------------------------------------------------------------------- #
def classify_effect(ci_low: float | None, ci_high: float | None, *,
                    protocol_passed: bool) -> str:
    if not protocol_passed or ci_low is None or ci_high is None:
        return "not_tested"
    if ci_low > ci_high:
        raise ValueError("ci_low exceeds ci_high")
    if ci_low > 0:
        return "improved"
    if ci_high < 0:
        return "worsened"
    return "inconclusive"


# --------------------------------------------------------------------------- #
# The validator
# --------------------------------------------------------------------------- #
def new_result(**fields) -> dict:
    """A schema-2 result with conservative defaults for every status."""
    r = {
        "schema_version": SCHEMA_VERSION,
        "scenario_revision": 0,
        "input_match": "unknown",
        "plan_match": "unknown",
        "evidence_kind": "model_only",
        "execution_state": "pending",
        "completion_status": "not_checked",
        "protocol_status": "not_checked",
        "effect_status": "not_tested",
        "operational_feasibility": "unknown",
        "validation_issues": [],
        "fingerprints": {"input": None, "plan": None, "model": None,
                         "protocol": None, "run": None},
    }
    r.update(fields)
    return r


def validate_result(result: Mapping | None, *,
                    expected_input_fp: str | None,
                    expected_plan_fp: str | None,
                    expected_model_fp: str | None = None,
                    expected_protocol_fp: str | None = None,
                    registered_confirmatory: bool = False) -> dict:
    """Decide whether `result` can stand behind the plan identified by the
    expected fingerprints. Returns a verdict; does not modify `result`.

    Applicable for confirmation only when: schema 2; input and plan fingerprints
    match exactly; the computation completed; the whole measured group completed;
    the registered protocol passed and matches; the series is registered as
    confirmatory. `effect_status` plays no part — a worsening can be valid.
    """
    issues: list[dict] = []

    if result is None:
        return {"applicable": False, "input_match": "unknown", "plan_match": "unknown",
                "evidence_kind": None, "effect_status": "not_tested",
                "operational_feasibility": "unknown",
                "validation_issues": [issue("no_result")]}

    if result.get("schema_version") != SCHEMA_VERSION:
        return {"applicable": False, "input_match": "unknown", "plan_match": "unknown",
                "evidence_kind": result.get("evidence_kind"), "effect_status": "not_tested",
                "operational_feasibility": "unknown",
                "validation_issues": [issue("schema_unsupported")]}

    for field, allowed in STATUS_FIELDS.items():
        if result.get(field) not in allowed:
            issues.append(issue("bad_status_value",
                                f"{field} = {result.get(field)!r} is outside the contract"))

    issues.extend(result.get("validation_issues") or [])

    fps = result.get("fingerprints") or {}

    def match(have: str | None, want: str | None) -> str:
        if not have or not want:
            return "unknown"
        return "exact" if have == want else "different"

    input_match = match(fps.get("input"), expected_input_fp)
    plan_match = match(fps.get("plan"), expected_plan_fp)
    if input_match != "exact":
        issues.append(issue("input_different" if input_match == "different" else "input_unknown"))
    if plan_match != "exact":
        issues.append(issue("plan_different" if plan_match == "different" else "plan_unknown"))

    if expected_model_fp is not None and fps.get("model") != expected_model_fp:
        issues.append(issue("model_different"))

    if expected_protocol_fp is not None and fps.get("protocol") != expected_protocol_fp:
        issues.append(issue("protocol_different"))

    if result.get("execution_state") != "completed":
        issues.append(issue("not_completed"))
    cs = result.get("completion_status")
    if cs == "incomplete":
        issues.append(issue("incomplete_cohort"))
    elif cs != "complete":
        issues.append(issue("completion_not_checked"))
    ps = result.get("protocol_status")
    if ps == "failed":
        issues.append(issue("protocol_failed"))
    elif ps != "passed":
        issues.append(issue("protocol_not_checked"))
    if result.get("evidence_kind") != "confirmatory":
        issues.append(issue("not_confirmatory"))
    if not registered_confirmatory:
        issues.append(issue("not_registered"))

    blocking = {i["code"] for i in issues}
    applicable = not blocking

    return {
        "applicable": applicable,
        "input_match": input_match,
        "plan_match": plan_match,
        "evidence_kind": result.get("evidence_kind"),
        "effect_status": result.get("effect_status", "not_tested"),
        "operational_feasibility": result.get("operational_feasibility", "unknown"),
        "validation_issues": issues,
    }


def validate_simulation_analysis(analysis: Mapping | None, *, expected_fps: Mapping,
                                 expected_counts: Mapping | None = None) -> dict:
    """Apply the shared contract to an analysis file before display or monetisation."""
    if analysis is None:
        return validate_result(None, expected_input_fp=expected_fps.get("input"),
                               expected_plan_fp=expected_fps.get("plan_candidate"))
    fps = analysis.get("fingerprints") or {}
    result = new_result(
        schema_version=analysis.get("schema_version"),
        evidence_kind=analysis.get("evidence_kind", "exploratory"),
        execution_state="completed",
        completion_status=analysis.get("completion_status", "not_checked"),
        protocol_status=analysis.get("protocol_status", "not_checked"),
        effect_status=analysis.get("effect_status", "not_tested"),
        fingerprints={"input": fps.get("input"), "plan": fps.get("plan_candidate"),
                      "model": fps.get("model"), "protocol": fps.get("protocol"), "run": None})
    verdict = validate_result(
        result, expected_input_fp=expected_fps.get("input"),
        expected_plan_fp=expected_fps.get("plan_candidate"),
        expected_model_fp=expected_fps.get("model"),
        expected_protocol_fp=expected_fps.get("protocol"),
        registered_confirmatory=analysis.get("series") == "confirmatory")
    extra = []
    if fps.get("plan_baseline") != expected_fps.get("plan_baseline"):
        extra.append(issue("baseline_different"))
    primary = analysis.get("primary") or {}
    if not primary.get("defined"):
        extra.append(issue("primary_not_defined"))
    if analysis.get("invalid_runs"):
        extra.append(issue("invalid_runs_present"))
    for condition, key in (("baseline", "n_baseline"), ("candidate", "n_candidate")):
        if expected_counts is not None and primary.get(key) != expected_counts.get(condition):
            extra.append(issue("cohort_count_different", f"{condition} run count differs."))
    verdict["validation_issues"].extend(extra)
    verdict["applicable"] = verdict["applicable"] and not extra
    return verdict


# --------------------------------------------------------------------------- #
# Historical adapter
# --------------------------------------------------------------------------- #
def from_legacy_simulation(old: Mapping, *, similar_note: str | None = None) -> dict:
    """Wrap a pre-schema-2 simulation dict (e.g. `archive.verified_simulation()`).

    The old boolean `applies` and any tolerance-based similarity are not carried
    over as applicability; `similar_note` may describe resemblance for display.
    """
    r = new_result(
        evidence_kind="historical_reference",
        execution_state="completed",
        completion_status="not_checked",
        protocol_status="not_checked",
        effect_status="not_tested",
        validation_issues=[issue("legacy_schema"), issue("historical_issues_remain")],
    )
    r["legacy"] = dict(old)
    if similar_note:
        r["similarity_note"] = similar_note
    return r


def statuses_only(result: Mapping) -> dict:
    """The status block of a result, for logs and exports."""
    return {k: result.get(k) for k in ("schema_version", "scenario_revision", *STATUS_FIELDS,
                                       "validation_issues")}


__all__: Iterable[str] = [
    "SCHEMA_VERSION", "STATUS_FIELDS", "ISSUE_MESSAGES",
    "canonical_json", "fingerprint", "file_sha256", "od_records",
    "input_fingerprint", "plan_fingerprint", "model_fingerprint",
    "protocol_fingerprint", "run_fingerprint",
    "unknown", "known", "issue", "classify_effect",
    "new_result", "validate_result", "validate_simulation_analysis",
    "from_legacy_simulation", "statuses_only",
]
