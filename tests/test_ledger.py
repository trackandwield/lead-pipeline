"""The ledger's promises: append-only, explained decisions, a verifiable hash chain,
erasure that keeps the audit trail, and content tied to the timeline."""
import pytest

from pipeline.ledger.catalog import STAGES

BOT = {"kind": "agent", "id": "scoring-service", "version": "0.3.1"}
SDR = {"kind": "human", "id": "per_sdr_1", "name": "Sam Ortiz", "role": "SDR"}


def form(account="acc_00001", **kw):
    ev = {"type": "form_submitted", "account_id": account, "person_id": "per_00001",
          "stage": "known", "actor": {"kind": "system", "id": "n8n-intake"},
          "summary": "Demo request from VP Engineering",
          "data": {"form_type": "demo_request", "email": "ana.diaz.1@acme.example.com"},
          "pii": ["email"]}
    ev.update(kw)
    return ev


def scored(account="acc_00001", **kw):
    ev = {"type": "lead_scored", "account_id": account, "person_id": "per_00001",
          "stage": "mql", "actor": BOT, "summary": "Scored 78 (Hot)",
          "reason": "Fit 52 + intent 26; thresholds v3 (Hot 70)",
          "inputs": {"scoring_version": 3}, "data": {"score": 78, "tier": "hot"}}
    ev.update(kw)
    return ev


def post(api, ev, code=201):
    r = api.post("/events", json=ev)
    assert r.status_code == code, r.text
    return r.json()


def test_events_chain_in_order(api):
    a = post(api, form())
    b = post(api, scored())
    assert (a["seq"], b["seq"]) == (1, 2)
    assert b["prev_hash"] == a["hash"]
    assert api.get("/verify/acc_00001").json()["ok"]


def test_accounts_have_separate_chains(api):
    post(api, form())
    other = post(api, form(account="acc_00002"))
    assert other["seq"] == 1


@pytest.mark.parametrize("bad, words", [
    ({"type": "made_up"}, "unknown event type"),
    ({"reason": None}, "reason and its inputs"),
    ({"actor": {"kind": "human", "id": "x"}}, "name and a role"),
    ({"stage": "lukewarm"}, "unknown stage"),
    ({"data": {"score": 78}}, "missing: tier"),
    ({"account_id": None}, "needs an account_id"),
    ({"pii": ["phone"]}, "not in data"),
])
def test_rejects_bad_events(api, bad, words):
    r = api.post("/events", json=scored(**bad))
    assert r.status_code == 422 and words in r.text


def test_human_decisions_need_no_machine_reason(api):
    post(api, {"type": "routed", "account_id": "acc_00001", "actor": SDR,
               "summary": "Took the lead myself", "data": {"owner": "per_sdr_1"}})


def test_pipeline_events_take_no_account(api):
    ev = {"type": "threshold_changed", "actor": {"kind": "agent", "id": "tuning-loop"},
          "summary": "Warm 45 to 48", "reason": "Warm MQL-to-SQL 0.13 < 0.15",
          "inputs": {"week": 7}, "data": {"tier": "warm", "old": 45, "new": 48},
          "approval": {"required": True, "by": "per_mops_1", "channel": "slack"}}
    assert post(api, ev)["chain"] == "_system"
    assert api.post("/events", json=dict(ev, account_id="acc_00001")).status_code == 422
    assert api.get("/verify/_system").json()["ok"]


def test_corrections_must_point_at_a_real_event(api):
    original = post(api, scored())
    fix = {"type": "correction", "account_id": "acc_00001", "actor": SDR,
           "summary": "Title was wrong; she is a student", "refs": ["evt_NOPE"]}
    assert api.post("/events", json=fix).status_code == 422
    assert post(api, dict(fix, refs=[original["event_id"]]))["refs"] == [original["event_id"]]


def test_retry_with_same_key_returns_first_event(api):
    a = post(api, form(idempotency_key="intake:hs-123"))
    b = post(api, form(idempotency_key="intake:hs-123"))
    assert a["event_id"] == b["event_id"]
    assert len(api.get("/customers/acc_00001/timeline").json()["events"]) == 1


def test_timeline_sorts_by_when_it_happened(api):
    post(api, scored(occurred_at="2026-01-07T15:10:00Z"))
    post(api, form(occurred_at="2026-01-07T15:00:00Z"))
    types = [e["type"] for e in api.get("/customers/acc_00001/timeline").json()["events"]]
    assert types == ["form_submitted", "lead_scored"]


def test_pii_is_kept_out_of_the_hashed_record(api, store):
    ev = post(api, form())
    assert ev["data"]["email"] == "ana.diaz.1@acme.example.com"
    raw = store._records(event_ids=[ev["event_id"]])[0]
    assert "email" not in raw["data"] and "email" in raw["pii_digests"]


def test_erasure_removes_pii_and_chain_still_verifies(api):
    ev = post(api, form())
    r = api.post("/erasure", json={"person_id": "per_00001", "requested_by": SDR}).json()
    assert r["events"] == 1 and r["accounts"] == ["acc_00001"]
    after = api.get(f"/events/{ev['event_id']}").json()
    assert "email" not in after["data"] and after["erased_fields"] == ["email"]
    timeline = api.get("/customers/acc_00001/timeline").json()["events"]
    assert timeline[-1]["type"] == "pii_erased"
    assert api.get("/verify/acc_00001").json()["ok"]


def test_content_is_attached_and_searchable(api):
    c = api.post("/content", json={
        "kind": "transcript", "account_id": "acc_00001", "person_id": "per_00001",
        "title": "Discovery call", "body": "We are replacing Datadog before renewal in March."})
    assert c.status_code == 201
    cid = c.json()["content_id"]
    ev = post(api, {"type": "meeting_held", "account_id": "acc_00001", "actor": SDR,
                    "summary": "Discovery call with VP Engineering",
                    "data": {"title": "Discovery call"}, "attachments": [cid]})
    assert ev["attachments"][0]["sha256"] == c.json()["sha256"]
    hits = api.get("/search", params={"q": "datadog", "account_id": "acc_00001"}).json()["hits"]
    assert [h["id"] for h in hits] == [cid]
    hits = api.get("/search", params={"q": "discovery call"}).json()["hits"]
    assert {h["kind"] for h in hits} == {"event", "content"}


def test_content_from_another_account_cannot_be_attached(api):
    cid = api.post("/content", json={"kind": "email", "account_id": "acc_00002",
                                     "title": "Hi", "body": "Hello"}).json()["content_id"]
    ev = {"type": "email_sent", "account_id": "acc_00001", "actor": SDR, "summary": "Sent",
          "data": {"thread_id": "t1", "subject": "Hi"}, "attachments": [cid]}
    assert api.post("/events", json=ev).status_code == 422


def test_catalog_lists_stages_and_types(api):
    cat = api.get("/catalog").json()
    assert tuple(cat["stages"]) == STAGES
    assert cat["types"]["lead_scored"]["decision"] is True
    assert {"meeting_held", "commitment_made", "drift_alert"} <= set(cat["types"])
