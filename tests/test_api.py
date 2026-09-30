"""End-to-end API tests with a temporary SQLite file."""

import os
import tempfile

import pytest

# Point the app at a throwaway DB before importing it
_tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_tmp.close()
os.environ["LIGHTING_DB"] = _tmp.name

from fastapi.testclient import TestClient  # noqa: E402

from app import db, engine  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _clean_db():
    db.init_db()
    with db.connect() as conn:
        conn.execute("DELETE FROM cues")
        conn.execute("DELETE FROM projects")
    yield


def cue(cid, target=255, duration=1000, fixture="L1", channel="d", start=None):
    return {"id": cid, "fixture": fixture, "channel": channel,
            "target": target, "duration": duration,
            "start": start or {"mode": "absolute", "at": 0}}


GOOD = {"name": "p", "cues": [
    cue("a", target=100, duration=1000, start={"mode": "absolute", "at": 0}),
    cue("b", target=200, duration=500,
        start={"mode": "reference", "refId": "a", "anchor": "end", "offset": 0}),
]}


def test_create_and_get_with_compiled_timeline():
    r = client.post("/api/projects", json=GOOD)
    assert r.status_code == 201
    pid = r.json()["id"]
    tl = r.json()["timeline"]
    at = {c["id"]: c["actualStart"] for c in tl["cues"]}
    assert at == {"a": 0, "b": 1000}

    r = client.get(f"/api/projects/{pid}")
    assert r.status_code == 200
    assert r.json()["project"]["cues"][0]["target"] == 100


def test_save_acceptance_atomically_replaces():
    r = client.post("/api/projects", json=GOOD)
    pid = r.json()["id"]
    r = client.put(f"/api/projects/{pid}", json={
        "name": "p2",
        "cues": [cue("z", target=10, duration=0,
                     start={"mode": "absolute", "at": 42})]})
    assert r.status_code == 200
    stored = client.get(f"/api/projects/{pid}").json()["project"]
    assert stored["name"] == "p2" and len(stored["cues"]) == 1
    assert stored["cues"][0]["id"] == "z"


def test_rejected_save_does_not_change_project():
    r = client.post("/api/projects", json=GOOD)
    pid = r.json()["id"]
    before = client.get(f"/api/projects/{pid}").json()["project"]

    # cycle -> reject
    bad = {"name": "p2", "cues": [
        cue("a", start={"mode": "reference", "refId": "b",
                        "anchor": "end", "offset": 0}),
        cue("b", start={"mode": "reference", "refId": "a",
                        "anchor": "start", "offset": 0}),
    ]}
    r = client.put(f"/api/projects/{pid}", json=bad)
    assert r.status_code == 400
    codes = {e["code"] for e in r.json()["detail"]["errors"]}
    assert "reference_cycle" in codes

    after = client.get(f"/api/projects/{pid}").json()["project"]
    assert after == before  # untouched


def test_deleting_a_referenced_cue_is_rejected():
    r = client.post("/api/projects", json=GOOD)
    pid = r.json()["id"]
    # delete "a" while "b" still references it
    r = client.put(f"/api/projects/{pid}", json={
        "name": "p", "cues": [
            cue("b", target=200, duration=500,
                start={"mode": "reference", "refId": "a",
                       "anchor": "end", "offset": 0})]})
    assert r.status_code == 400
    assert any(e["code"] == "missing_reference"
               for e in r.json()["detail"]["errors"])
    # still two cues in storage
    stored = client.get(f"/api/projects/{pid}").json()["project"]
    assert {c["id"] for c in stored["cues"]} == {"a", "b"}


def test_same_channel_same_start_rejected():
    r = client.post("/api/projects", json=GOOD)
    pid = r.json()["id"]
    r = client.put(f"/api/projects/{pid}", json={
        "name": "p", "cues": [
            cue("a", target=10, duration=100,
                start={"mode": "absolute", "at": 50}),
            cue("b", target=20, duration=0,
                start={"mode": "absolute", "at": 50})]})
    assert r.status_code == 400
    assert any(e["code"] == "same_channel_same_start"
               for e in r.json()["detail"]["errors"])


def test_bad_fields_400_via_pydantic():
    r = client.post("/api/projects", json={"name": "p", "cues": [
        cue("a", target=999, duration=0)  # >255
    ]})
    assert r.status_code == 400

    r = client.post("/api/projects", json={"name": "p", "cues": [
        cue("a", target=10.5, duration=0)  # float not accepted
    ]})
    assert r.status_code == 400


def test_validate_endpoint_does_not_persist():
    r = client.post("/api/projects", json=GOOD)
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/validate", json={
        "name": "draft", "cues": []})
    assert r.status_code == 200 and r.json()["timeline"]["lanes"] == []
    stored = client.get(f"/api/projects/{pid}").json()["project"]
    assert stored["name"] == "p" and len(stored["cues"]) == 2


def test_evaluate_integer_and_fraction():
    r = client.post("/api/projects", json=GOOD)
    pid = r.json()["id"]
    # b starts at 1000 and ramps 100->200 over 500 ms
    r = client.post(f"/api/projects/{pid}/evaluate", json={"time": 1250})
    assert r.status_code == 200
    lane = r.json()["lanes"]["L1|d"]
    assert lane["value"] == 150 and lane["activeCueId"] == "b"

    # fractional time {n,d}: t = 1250.5 -> value 150.1 -> 150
    r = client.post(f"/api/projects/{pid}/evaluate",
                    json={"time": {"n": 2501, "d": 2}})
    lane = r.json()["lanes"]["L1|d"]
    assert lane["raw"] == {"n": 1501, "d": 10}
    assert lane["value"] == 150


def test_evaluate_half_up_at_exact_half():
    r = client.post("/api/projects", json={"name": "p", "cues": [
        cue("a", target=1, duration=2, start={"mode": "absolute", "at": 0})]})
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/evaluate", json={"time": 1})
    assert r.json()["lanes"]["L1|d"]["value"] == 1  # 0.5 rounds up


def test_evaluate_rejects_bad_time():
    r = client.post("/api/projects", json=GOOD)
    pid = r.json()["id"]
    r = client.post(f"/api/projects/{pid}/evaluate", json={"time": -3})
    assert r.status_code == 400


def test_404_for_unknown_project():
    assert client.get("/api/projects/nope").status_code == 404
    assert client.put("/api/projects/nope", json=GOOD).status_code == 404
