"""Layer 5 — economic evaluation with explicit scope (protocol route51_v2 v1.0).

The single source of the value of time and of the monetisation formulas for the
service scripts and the prototype.

Scopes (never mixed inside one balance):
  am_peak                  waiting, crowding and resources of 07:00-10:00; passenger
                           in-vehicle time only if an applicable simulation supplies it
  all_day_model            layer-3 waiting and crowding of all periods + all-day
                           resources; no in-vehicle term is carried over from the
                           morning simulation
  all_day_with_simulation  not evaluated (no comparable simulation of all periods)

For one scope, with D days of the type per year and VoT in UZS per passenger-hour:
  wait_benefit_h      = w * (wait_base - wait_plan) * D / 60
  crowd_benefit_h     = (crowd_min_base - crowd_min_plan) * D / 60
  passenger_model_uzs = (wait_benefit_h + crowd_benefit_h) * VoT
  passenger_with_ivt  = passenger_model_uzs + ivt_benefit_uzs      (None if IVT unknown)
  delta_equiv_km_year = (K_plan - K_base) * D                      (12 m-equivalent km)
  operator_delta_cost = rate_12m * delta_equiv_km_year             (None if no rate)
  combined            = passenger_with_ivt - operator_delta_cost   (None if any unknown)

No factor 10 on crowding, and the money rate is not multiplied by r a second time:
the 18 m weighting is already in the equivalent kilometres. Passenger hours are
not revenue; the rate threshold is a threshold of the model's combined balance,
not an operator's profit or payback.
"""
from __future__ import annotations

import math

import result_contracts as rc

ECONOMICS_VERSION = "economics_scoped_v1"

# --- value of time -----------------------------------------------------------------
# Average monthly nominal accrued wage, Tashkent city (MHOBT 1726), indicator
# 2.02.02.1200 (National Statistics Committee, sdmx_data_522.xlsx), 2026 Q1 and Q2.
# Q1 is flagged preliminary in the source, so the half-year mean is used. The series
# is gross and excludes agriculture and small business (biased upward).
WAGE_GROSS_2026Q1 = 11_684_262.5
WAGE_GROSS_2026Q2 = 12_013_894.7
PIT_RATE = 0.12                  # flat personal income tax, gross -> net
MONTHLY_HOURS = 168              # 21 days x 8 h
COMMUTE_FRACTION = 0.45          # commuting VoT as a share of the net hourly wage
VOT_SENSITIVITY = 0.30           # +-30 % sensitivity, not a confidence interval
VALUE_OF_TIME_UZS_H = (COMMUTE_FRACTION * ((WAGE_GROSS_2026Q1 + WAGE_GROSS_2026Q2) / 2)
                       * (1 - PIT_RATE) / MONTHLY_HOURS)
VOT_BASIS = {
    "value_uzs_per_pax_h": VALUE_OF_TIME_UZS_H,
    "definition": "0.45 x net hourly wage",
    "source": "average monthly nominal accrued wage, Tashkent city (MHOBT 1726), indicator "
              "2.02.02.1200, National Statistics Committee",
    "reference_period": "2026 Q1-Q2 mean (Q1 preliminary)",
    "transformation": "gross -> net with 12% PIT; /168 h per month; x 0.45",
    "evidence_type": "published source with a transfer assumption (0.45)",
    "sensitivity": "+-30% (not a confidence interval)",
}

DAYS_PER_YEAR = 250              # days of this type per year — scenario assumption


def rate_threshold(B: float | None, dK: float | None) -> dict:
    """Condition on a money rate c >= 0 for a non-negative combined balance B - c*dK."""
    if B is None or dK is None:
        return {"kind": "unknown", "text": "Threshold unknown: benefit or kilometre change unknown."}
    if dK > 0:
        if B > 0:
            return {"kind": "max_rate", "value": B / dK,
                    "text": "balance >= 0 for c <= B / dK"}
        if B == 0:
            return {"kind": "only_zero", "value": 0.0, "text": "only c = 0 gives a non-negative balance"}
        return {"kind": "impossible", "text": "no c >= 0 gives a non-negative balance"}
    if dK == 0:
        return {"kind": "no_division", "text": ("balance >= 0 for every c" if B >= 0
                                                else "balance < 0 for every c")}
    if B >= 0:
        return {"kind": "any_rate", "text": "balance >= 0 for every c >= 0"}
    return {"kind": "min_rate", "value": B / dK, "text": "balance >= 0 for c >= B / dK"}


def scope_block(*, scope: str, wait_base: float, wait_plan: float, crowd_base: float,
                crowd_plan: float, K_base: float, K_plan: float, ivt_benefit_pax_min: float | None,
                ivt_reason: str | None, rate_12m: float | None, days: float = DAYS_PER_YEAR,
                vot: float = VALUE_OF_TIME_UZS_H, wait_weights=(1, 2),
                fingerprints: dict | None = None, evidence_kind: str = "model_only") -> dict:
    blocks = {}
    for w in wait_weights:
        wait_h = w * (wait_base - wait_plan) * days / 60.0
        crowd_h = (crowd_base - crowd_plan) * days / 60.0
        pm = (wait_h + crowd_h) * vot
        if ivt_benefit_pax_min is None:
            ivt_h = ivt_uzs = None
            pw = None
        else:
            ivt_h = ivt_benefit_pax_min * days / 60.0
            ivt_uzs = ivt_h * vot
            pw = pm + ivt_uzs
        dK = (K_plan - K_base) * days
        op_cost = None if rate_12m is None else rate_12m * dK
        combined = None if (pw is None or op_cost is None) else pw - op_cost
        B_for_threshold = pw
        blocks[f"wait_weight_{w}"] = {
            "wait_benefit_h": wait_h,
            "crowd_benefit_h": crowd_h,
            "passenger_model_uzs": pm,
            "passenger_model_uzs_vot_minus_30": pm * (1 - VOT_SENSITIVITY),
            "passenger_model_uzs_vot_plus_30": pm * (1 + VOT_SENSITIVITY),
            "ivt_benefit_h": ivt_h,
            "ivt_benefit_uzs": ivt_uzs,
            "passenger_with_ivt_uzs": pw,
            "passenger_with_ivt_reason": None if pw is not None else (ivt_reason or "ivt_unknown"),
            "delta_equiv_12m_km_year": dK,
            "operator_delta_cost_uzs": op_cost,
            "operator_delta_cost_reason": None if op_cost is not None else "no_sourced_rate",
            "combined_uzs": combined,
            "combined_reason": (None if combined is not None else
                                ("no_sourced_rate" if op_cost is None else (ivt_reason or "ivt_unknown"))),
            "rate_threshold_uzs_per_equiv_km": rate_threshold(B_for_threshold, dK),
            "rate_threshold_model_only": rate_threshold(pm, dK),
        }
    return {
        "scope": scope,
        "day_type": "weekday (scenario)",
        "annualisation_days": days,
        "evidence_kind": evidence_kind,
        "input_fingerprint": (fingerprints or {}).get("input"),
        "plan_fingerprint": (fingerprints or {}).get("plan_candidate"),
        "baseline_plan_fingerprint": (fingerprints or {}).get("plan_baseline"),
        "status": "evaluated",
        "reason_code": None,
        "inputs": {"wait_base": wait_base, "wait_plan": wait_plan, "crowd_min_base": crowd_base,
                   "crowd_min_plan": crowd_plan, "equiv_km_base_per_day": K_base,
                   "equiv_km_plan_per_day": K_plan, "ivt_benefit_pax_min_per_day": ivt_benefit_pax_min,
                   "rate_12m_uzs_per_km": rate_12m, "value_of_time": vot},
        "by_wait_weight": blocks,
    }


def not_evaluated(scope: str, reason: str, message: str) -> dict:
    return {"scope": scope, "status": "not_evaluated", "reason_code": reason, "message": message}


def simulation_ivt(simulation: dict | None, expected_fps: dict) -> tuple[float | None, str | None, dict]:
    """Morning-peak passenger in-vehicle benefit (pax·min/day) from a simulation analysis,
    only when the shared validator says the simulation stands behind this plan."""
    if simulation is None:
        return None, "no_simulation", rc.validate_simulation_analysis(None, expected_fps=expected_fps)
    verdict = rc.validate_simulation_analysis(simulation, expected_fps=expected_fps,
                                              expected_counts=expected_fps.get("counts"))
    if not verdict["applicable"]:
        return None, "simulation_not_applicable", verdict
    px = (simulation.get("secondary_exploratory") or {}).get("pax_ivt_am_min") or {}
    b, c = px.get("baseline"), px.get("candidate")
    if b is None or c is None or not all(map(math.isfinite, (b, c))):
        return None, "ivt_not_instrumented", verdict
    return b - c, None, verdict
