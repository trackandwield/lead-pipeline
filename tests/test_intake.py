import json
from pathlib import Path

import pytest

from pipeline.intake import flatten, form_type, resolve_ids, sample_payload


def test_both_hubspot_shapes_flatten_the_same():
    flat = {"hs_object_id": "42", "email": "a@b.example.com"}
    legacy = {"vid": 42, "properties": {"email": {"value": "a@b.example.com"}}}
    assert flatten(flat) == flatten(legacy) == flat


@pytest.mark.parametrize("name, expected", [
    ("Lumenvale demo request", "demo_request"), ("Pricing page contact", "pricing_contact"),
    ("OTel migration guide", "ebook"), ("Something else", "other")])
def test_form_type_from_conversion_name(name, expected):
    assert form_type({"recent_conversion_event_name": name}) == expected


def test_account_resolution():
    assert resolve_ids({"ledger_account_id": "acc_00007"}, "1", "x@y.example.com")[0] == "acc_00007"
    assert resolve_ids({}, "1", "x@northpeak.example.com")[0] == "acc_dom_northpeakexamplecom"
    assert resolve_ids({}, "9", "x@gmail.com")[0] == "acc_hs_9"


def test_form_fill_writes_form_and_stage_once(api):
    body = sample_payload(1)
    first = api.post("/intake/hubspot", json=body)
    assert first.status_code == 201, first.text
    r = first.json()
    assert len(r["events"]) == 2 and not r["duplicate"]
    timeline = api.get(f"/customers/{r['account_id']}/timeline").json()["events"]
    assert [e["type"] for e in timeline] == ["form_submitted", "stage_changed"]
    assert timeline[0]["source"]["system"] == "hubspot"
    assert timeline[1]["refs"] == [timeline[0]["event_id"]]

    retry = api.post("/intake/hubspot", json=body).json()  # HubSpot retries the webhook
    assert retry["duplicate"] and retry["events"][0] == r["events"][0]

    later = dict(body, recent_conversion_date=str(int(body["recent_conversion_date"]) + 86400000),
                 recent_conversion_event_name="Pricing page contact")
    again = api.post("/intake/hubspot", json=later).json()  # a second, different form fill
    assert len(again["events"]) == 1
    types = [e["type"] for e in api.get(f"/customers/{r['account_id']}/timeline").json()["events"]]
    assert types == ["form_submitted", "stage_changed", "form_submitted"]
    assert api.get(f"/verify/{r['account_id']}").json()["ok"]


def test_known_seeded_person_is_not_made_known_twice(api):
    from pipeline.ledger.client import LedgerClient
    from pipeline.ledger.seed import seed
    from pipeline.simulator import generate
    lead = generate(n=1)[0]
    seed([lead], LedgerClient(client=api))
    body = dict(sample_payload(5), email=lead.email, ledger_account_id=lead.company_id,
                ledger_person_id=lead.lead_id.replace("lead_", "per_"))
    assert len(api.post("/intake/hubspot", json=body).json()["events"]) == 1


@pytest.mark.parametrize("change, words", [
    ({"email": "not-an-email"}, "valid email"), ({"hs_object_id": None}, "contact ID")])
def test_bad_webhooks_are_rejected(api, change, words):
    r = api.post("/intake/hubspot", json=dict(sample_payload(2), **change))
    assert r.status_code == 422 and words in r.text


def test_shared_secret(api, monkeypatch):
    monkeypatch.setenv("INTAKE_SHARED_SECRET", "s3cret")
    assert api.post("/intake/hubspot", json=sample_payload(3)).status_code == 401
    ok = api.post("/intake/hubspot", json=sample_payload(3), headers={"X-Intake-Key": "s3cret"})
    assert ok.status_code == 201


def test_n8n_workflow_calls_the_intake_endpoint():
    wf = json.loads(Path("n8n/workflows/lead-intake.json").read_text())
    nodes = {n["name"]: n for n in wf["nodes"]}
    hook = nodes["HubSpot form webhook"]["parameters"]
    assert hook["path"] == "hubspot-intake" and hook["responseMode"] == "responseNode"
    assert "/intake/hubspot" in nodes["Record in customer ledger"]["parameters"]["url"]
    for src, out in wf["connections"].items():
        assert src in nodes and all(c["node"] in nodes for c in out["main"][0])
