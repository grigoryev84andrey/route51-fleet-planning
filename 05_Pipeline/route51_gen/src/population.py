"""Stage 1 — synthetic population for the Route-51 catchment.

Generates the FULL catchment population (all travellers), not a single-operator
subsample. Operator membership is a per-person attribute: penetration decides
whether a person owns a phone (demographically coupled), operator_shares decide
which network. The telecom feed later subsets to the observed operator; the
ground-truth OD and the ATTO feed use the whole population.

Home location is drawn from the corridor density surface, which is currently a
PLACEHOLDER (uniform / position-gradient) until OSM-landuse dasymetric
downscaling is available (D10 6.1) — the load-bearing gap, flagged not faked.
"""
from __future__ import annotations

import h3
import numpy as np
import pandas as pd

from geo import Corridor


def _sample_cat(rng: np.random.Generator, marg: dict, n: int) -> np.ndarray:
    keys = list(marg)
    p = np.array([marg[k] for k in keys], float)
    p = p / p.sum()
    return np.asarray(keys)[rng.choice(len(keys), size=n, p=p)]


def synth_population(cfg, corridor: Corridor, rng: np.random.Generator) -> pd.DataFrame:
    pcfg = cfg["population"]
    n = int(pcfg["n_override"] or pcfg["catchment_population"])
    dem = pcfg["demographics"]

    df = pd.DataFrame({"person_id": [f"P{i:06d}" for i in range(n)]})
    df["age_band"] = _sample_cat(rng, dem["age_band"], n)
    df["gender"] = _sample_cat(rng, dem["gender"], n)
    df["employment"] = _sample_cat(rng, dem["employment"], n)
    df["income"] = _sample_cat(rng, dem["income_proxy"], n)
    df["car_owner"] = _sample_cat(rng, dem["car_ownership"], n) == "yes"
    df["hh_children"] = _sample_cat(rng, dem["household_has_children"], n) == "yes"
    df["work_schedule"] = _sample_cat(rng, dem["work_schedule_type"], n)

    # --- Home cell from the (placeholder) density surface ---
    cells = list(corridor.density)
    w = np.array([corridor.density[c] for c in cells], float)
    w = w / w.sum()
    home = np.asarray(cells)[rng.choice(len(cells), size=n, p=w)]
    df["home_h3"] = home
    centroids = {c: h3.cell_to_latlng(c) for c in set(home.tolist())}
    df["home_lat"] = [centroids[c][0] for c in home]
    df["home_lon"] = [centroids[c][1] for c in home]

    # --- Phone ownership (penetration, demo-coupled) ---
    pen = cfg["telecom"]["penetration"]
    rate = np.full(n, float(pen["base_rate"]))
    if pen.get("couple_to_demographics"):
        ma, mi = pen["multiplier_by_age"], pen["multiplier_by_income"]
        rate *= np.array([ma.get(a, 1.0) for a in df["age_band"]])
        rate *= np.array([mi.get(x, 1.0) for x in df["income"]])
    rate = np.clip(rate, 0.0, 1.0)
    df["phone_owner"] = rng.random(n) < rate

    # --- Operator among phone owners (central shares) ---
    osh = cfg["telecom"]["operator_shares"]
    onames = list(osh)
    op_p = np.array([osh[k]["central"] for k in onames], float)
    op_p = op_p / op_p.sum()
    drawn_op = np.asarray(onames)[rng.choice(len(onames), size=n, p=op_p)]
    df["operator"] = np.where(df["phone_owner"], drawn_op, "none")

    # --- ATTO card type (cash banned 2025 -> a card is assigned to everyone;
    #     it is only *used* if the person actually rides) ---
    cards = cfg["atto"]["card_types"]
    cnames = list(cards)
    cp = np.array([cards[k]["share"] for k in cnames], float)
    cp = cp / cp.sum()
    df["atto_card"] = np.asarray(cnames)[rng.choice(len(cnames), size=n, p=cp)]

    # Primary activity purpose from employment status.
    purpose = np.full(n, "other", dtype=object)
    purpose[df["employment"].values == "employed"] = "work"
    purpose[df["employment"].values == "student"] = "education"
    df["primary_purpose"] = purpose
    return df
