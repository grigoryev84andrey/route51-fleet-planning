"""Backend contract checks (plan S11, S03, S17, S18).

Runs the real engine on the frozen thesis matrix. The simulation step is not run
here (minutes of SUMO); its status logic is covered by the service tests.
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ["R51_EXPORTS_DIR"] = tempfile.mkdtemp(prefix="r51_exports_test_")

import pytest

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

import server  # noqa: E402

client = TestClient(server.app)


def wait(job_id, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.5)
    raise TimeoutError(job_id)


@pytest.fixture(scope="module")
def ready():
    r = client.post("/api/data/run", json={"matrix": "thesis", "scale": 3.0})
    assert r.status_code == 200
    j = wait(r.json()["job"]["id"])
    assert j["state"] == "completed" and j["published"]
    r = client.post("/api/plan/front", json={"budget_idx": 1, "delta_idx": 1})
    j = wait(r.json()["job"]["id"])
    assert j["state"] == "completed" and j["published"], j
    return client.get("/api/session").json()


def test_validation_rejects_bad_requests():
    for body, code in (({"source": "upload"}, "unsupported_source"),
                       ({"matrix": "real"}, "unsupported_value"),
                       ({"scale": 5}, "unsupported_value"),
                       ({"seed": -1}, "out_of_range"),
                       ({"seed": 1.5}, "invalid_integer")):
        r = client.post("/api/data/run", json=body)
        assert r.status_code == 422 and r.json()["detail"]["code"] == code, body
    r = client.post("/api/data/run", content=b'{"seed": NaN}', headers={"content-type": "application/json"})
    assert r.status_code in (400, 422)


def test_unknown_job_is_reported():
    r = client.get("/api/jobs/19990101T000000_deadbeef")
    assert r.status_code == 404 and r.json()["code"] == "job_unknown"


def test_front_contract(ready):
    s = ready
    assert s["data"]["quality"]["top20_od_matched"] == 13
    assert abs(s["data"]["quality"]["alighting_distribution_similarity"] - 0.881564629531548) < 1e-12
    f = s["front"]
    assert 1 <= len(f["alternatives"]) <= 3
    ids = [a["id"] for a in f["alternatives"]]
    assert len(set(ids)) == len(ids)
    assert "recommended" in f["alternatives"][0]["keys"]
    # the same protocol rule as the service run: 43:1 on the frozen inputs
    assert f["alternatives"][0]["id"] == "43:1"
    assert f["alternatives"][0]["registered_result_available"] is True
    assert all(a["feasibility"]["operational_feasibility"] == "unknown" for a in f["alternatives"])
    assert s["simulation"] is None


def test_registered_result_is_readable_without_sumo(ready, monkeypatch):
    monkeypatch.setattr(server, "capabilities", lambda: {"simulation": False})
    r = client.post("/api/result/simulate", json={})
    assert r.status_code == 200
    j = wait(r.json()["job"]["id"])
    assert j["state"] == "completed" and j["published"]
    sim = client.get("/api/session").json()["simulation"]
    assert sim["registered"] is True and sim["evidence_kind"] == "confirmatory"
    assert "not re-run" in sim["source"]
    exported = client.post("/api/export", json={}).json()
    page = client.get(exported["primary_url"]).text
    assert f"<strong>{sim['status_label']}</strong>" in page
    assert f"<strong>{sim['primary']['baseline_mean']:.2f} min</strong>" in page
    assert f"<strong>{sim['primary']['candidate_mean']:.2f} min</strong>" in page
    assert "<strong>+0.15 min</strong>" not in page
    server.SESSION["sims"].clear()


def test_tampered_release_is_not_registered(ready, monkeypatch):
    import copy
    original = server.archive.active_release()
    altered = copy.deepcopy(original)
    altered["simulation_analysis.json"]["fingerprints"]["model"] = "stale-model"
    monkeypatch.setattr(server.archive, "active_release", lambda: altered)
    f = server.SESSION["front"]
    alt = next(a for a in f["alternatives"] if a["id"] == "43:1")
    assert not server.pipeline.engine().registered(server.SESSION["data"], f, alt)


def test_plan_index_validation(ready):
    assert client.post("/api/plan/select", json={"id": "-1"}).status_code == 422
    assert client.post("/api/plan/front", json={"budget_idx": -1}).status_code == 422
    assert client.post("/api/plan/front", json={"budget_idx": 9}).status_code == 422


def test_economics_without_simulation_has_no_borrowed_ivt(ready):
    e = client.post("/api/result/economics", json={}).json()
    b = e["scopes"]["am_peak"]["by_wait_weight"]["wait_weight_1"]
    assert b["ivt_benefit_uzs"] is None and b["passenger_with_ivt_reason"] == "no_simulation"
    assert b["operator_delta_cost_uzs"] is None and b["combined_uzs"] is None
    assert e["scopes"]["all_day_with_simulation"]["status"] == "not_evaluated"


def test_export_is_model_only_and_unique(ready):
    a = client.post("/api/export", json={}).json()
    b = client.post("/api/export", json={}).json()
    assert a["stem"] != b["stem"] and a["state"] == "model_only"
    rec = json.loads(client.get(a["files"][2]["url"]).content)
    assert rec["simulation"] is None and rec["export_label"] == "Not simulated for this plan"
    assert isinstance(rec["plan"]["dispatch_metrics"]["gen_cost"], float)
    page = client.get(a["primary_url"])
    assert page.status_code == 200 and "text/html" in page.headers["content-type"]
    assert "attachment" not in page.headers.get("content-disposition", "")
    # The thesis example improves crowding but increases waiting and operating cost.
    import re
    rows = re.findall(r"<tr><td>(.*?)</tr>", page.text)
    assert "<strong>" in next(r for r in rows if r.startswith("Crowding disutility</td>"))
    assert "<strong>" in next(r for r in rows if r.startswith("Waiting + crowding</td>"))
    assert "<strong>" not in next(r for r in rows if r.startswith("Waiting</td>"))
    assert "<strong>" not in next(r for r in rows if r.startswith("Operating-cost index</td>"))
    benefit = rec["economics"]["scopes"]["all_day_model"]["by_wait_weight"]["wait_weight_1"]["passenger_model_uzs"]
    assert f"<strong>{server.report._n(benefit / 1e6)} million UZS/year</strong>" in page.text
    assert "[Operator &amp; fleet, whole day]" in page.text
    assert "<strong>124 800</strong> per year" in page.text
    assert "<strong>93 019</strong> UZS per 12 m-equivalent km" in page.text
    assert "<strong>27 930</strong> UZS per passenger-hour" in page.text
    morning = rec["economics"]["scopes"]["am_peak"]["by_wait_weight"]["wait_weight_1"]
    whole_day = rec["economics"]["scopes"]["all_day_model"]["by_wait_weight"]["wait_weight_1"]
    for metric in (morning["crowd_benefit_h"], whole_day["crowd_benefit_h"]):
        assert f"<strong>{server.report._n(metric)}</strong> h/year" in page.text
    assert f"<strong>{server.report._n(morning['passenger_model_uzs_vot_minus_30'] / 1e6)} million UZS/year</strong>" in page.text
    assert f"<strong>{server.report._n(whole_day['passenger_model_uzs_vot_minus_30'] / 1e6)} million UZS/year</strong>" not in page.text
    md = client.get(next(f["url"] for f in a["files"] if f["name"] == "report.md")).text
    assert "**" in next(line for line in md.splitlines() if line.startswith("| Crowding disutility |"))
    assert "**" not in next(line for line in md.splitlines() if line.startswith("| Waiting |"))
    assert f"**{server.report._n(whole_day['crowd_benefit_h'])}** h/year" in md
    import io
    import zipfile
    with zipfile.ZipFile(io.BytesIO(client.get(a["zip_url"]).content)) as archive:
        assert archive.read(f"{a['stem']}/report.html") == page.content


def test_preview_uses_selected_waiting_weight(ready):
    assert client.post("/api/export", json={"wait_weight": 3}).status_code == 422
    exported = client.post("/api/export", json={"wait_weight": 2}).json()
    record = json.loads(client.get(next(f["url"] for f in exported["files"]
                                      if f["name"] == "decision_record.json")).content)
    assert record["wait_weight"] == 2
    day = record["economics"]["scopes"]["all_day_model"]["by_wait_weight"]["wait_weight_2"]
    amount = f"<strong>{server.report._n(day['passenger_model_uzs'] / 1e6)} million UZS/year</strong>"
    page = client.get(exported["primary_url"]).text
    assert "waiting-time weight of 2×" in page and amount in page


def test_report_does_not_highlight_losses_or_uncertain_simulation(ready):
    import copy
    economics = copy.deepcopy(client.post("/api/result/economics", json={}).json())
    for scope in ("am_peak", "all_day_model"):
        economics["scopes"][scope]["by_wait_weight"]["wait_weight_1"]["passenger_model_uzs"] = -1000000
    assert "<strong>-1 million UZS/year</strong>" not in "".join(server.report._eco_lines(economics, html_format=True))
    sim = {"completion_status": "complete", "status_label": "<script>test</script>",
           "primary": {"defined": True, "baseline_mean": 20, "candidate_mean": 19,
                       "delta_min": 1, "ci95": [-1, 3], "n_baseline": 30, "n_candidate": 30}}
    lines = "".join(server.report._sim_lines(sim, html_format=True))
    assert "<script>" not in lines and "<strong>&lt;script&gt;test&lt;/script&gt;</strong>" in lines
    assert "<strong>20.00 min</strong>" in lines and "<strong>19.00 min</strong>" in lines
    assert "<strong>+1.00 min</strong>" not in lines
    sim["primary"]["ci95"] = [0.5, 1.5]
    assert "<strong>+1.00 min</strong>" not in "".join(server.report._sim_lines(sim, html_format=True))


def test_stale_result_is_not_published(ready):
    # start a front job, then change conditions: the first result must not become current
    r1 = client.post("/api/plan/front", json={"budget_idx": 0, "delta_idx": 1}).json()["job"]["id"]
    r2 = client.post("/api/plan/front", json={"budget_idx": 1, "delta_idx": 1}).json()["job"]["id"]
    j1, j2 = wait(r1), wait(r2)
    assert j1["state"] == "completed" and j1["published"] is False
    assert j2["published"] is True
    s = client.get("/api/session").json()
    assert s["front"]["conditions"]["budget_idx"] == 1 and not s["stale"]["front"]
