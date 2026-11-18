"""Prove the governance layer ACCEPTS compliant feeds and QUARANTINES bad ones."""
import hashlib
import sys
import pandas as pd
from fastapi.testclient import TestClient

from layer1.gateway import build_gateway
from api import create_app

gw, cfg = build_gateway("service_config.yaml")
KEY = cfg["service"]["api_key"]
def h(x): return hashlib.sha256(str(x).encode()).hexdigest()   # 64-hex pseudonym
stops = list(gw.ref.stops.keys())
ts = lambda hh, mm=0: f"2025-06-16 {hh:02d}:{mm:02d}:00"

passed = 0; failed = 0
def check(name, cond):
    global passed, failed
    ok = bool(cond); passed += ok; failed += (not ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

print("="*70); print("GOVERNANCE TESTS"); print("="*70)

# --- 1. GOOD ATTO: hashed ids, many persons per cell -> accepted, sparse cells suppressed
rows = []
for i in range(400):
    rows.append({"hashed_card_id": h(i % 120), "timestamp": ts(8, i % 60),
                 "boarding_stop_id": stops[i % len(stops)], "route_id": "51",
                 "card_type": ["blue_general","green_pupil","yellow_student","red_social"][i % 4]})
good_atto = pd.DataFrame(rows)
r = gw.ingest("atto", good_atto, api_key=KEY)
check("good ATTO accepted", r.accepted and r.governance.passed)
check("good ATTO k-anon applied (some suppression possible)",
      r.index.stats["suppression_applies"] and r.index.stats["cells_suppressed"] >= 0)

# --- 2. BAD ATTO: raw PAN as id + forbidden tap_out -> quarantined
bad_atto = good_atto.copy()
bad_atto["hashed_card_id"] = ["4276" + str(1000000000 + i) for i in range(len(bad_atto))]  # PAN-like
bad_atto["tap_out"] = stops[0]
r = gw.ingest("atto", bad_atto, api_key=KEY)
check("bad ATTO (PAN id + tap_out) quarantined", not r.accepted)
check("  -> flags forbidden alighting field",
      any("tap_out" in v for v in r.governance.violations))
check("  -> flags non-hash identifier",
      any("IDENTIFIER" in v for v in r.governance.violations))

# --- 3. GOOD telecom: hashed sub id, h3 cell, in-country tag -> accepted
cells = list(gw.ref.grid)
trows = []
for i in range(300):
    trows.append({"hashed_subscriber_id": h(10000 + (i % 90)), "event_type": ["LAU","HO","TA"][i%3],
                  "timestamp": ts(17, i % 60), "h3_cell": cells[i % len(cells)],
                  "processing_region": "UZ"})
good_tel = pd.DataFrame(trows)
r = gw.ingest("telecom", good_tel, api_key=KEY)
check("good telecom accepted", r.accepted and r.governance.passed)
check("  -> localization note present",
      any("localization" in n for n in r.governance.notes))

# --- 4. BAD telecom: raw MSISDN, raw coords, missing localization tag -> quarantined
bad_tel = good_tel.drop(columns=["processing_region"]).copy()
bad_tel["msisdn"] = ["998901234567"] * len(bad_tel)
bad_tel["lat"] = 41.31; bad_tel["lon"] = 69.27
r = gw.ingest("telecom", bad_tel, api_key=KEY)
check("bad telecom quarantined", not r.accepted)
check("  -> flags forbidden raw coords/MSISDN",
      any(("lat" in v or "msisdn" in v) for v in r.governance.violations))
check("  -> flags missing localization tag",
      any("27-1" in v for v in r.governance.violations))

# --- 5. AUTH + API endpoint via TestClient
client = TestClient(create_app("service_config.yaml"))
check("health ok", client.get("/health").status_code == 200)
check("contracts lists 3 feeds", len(client.get("/contracts").json()) == 3)
gps_rows = pd.read_csv("../route51_gen/output/bus_gps_raw.csv").head(50).to_dict("records")
resp = client.post("/ingest/gps", json={"records": gps_rows}, headers={"X-API-Key": KEY})
check("API GPS ingest accepted", resp.status_code == 200 and resp.json()["accepted"])
bad_auth = client.post("/ingest/gps", json={"records": gps_rows}, headers={"X-API-Key": "WRONG"})
check("API rejects bad key (401)", bad_auth.status_code == 401)

print("-"*70)
print(f"RESULT: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
