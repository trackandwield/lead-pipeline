from fastapi.testclient import TestClient

from pipeline.enrich import SimulatorProvider, costs
from pipeline.intake import sample_payload
from pipeline.ledger.api import create_app
from pipeline.ledger.client import LedgerClient
from pipeline.ledger.seed import seed
from pipeline.simulator import generate


def _provider(leads):
    p = SimulatorProvider(cost_per_lookup=0.05)
    for lead in leads:
        p.add(lead.visible())
    return p


def _app(store, leads=()):
    return TestClient(create_app(store, provider=_provider(leads)))


def test_simulated_account_gets_the_simulators_company(store):
    leads = generate(n=3, seed=3)
    api = _app(store, leads)
    seed(leads, LedgerClient(client=api))
    lead = leads[0]
    r = api.post(f"/enrich/{lead.company_id}")
    assert r.status_code == 201, r.text
    ev = r.json()["event"]
    f = ev["data"]["fields"]
    assert f["employees"] == {"value": lead.employees, "source": "simulator"}
    assert f["industry"]["value"] == lead.industry
    assert f["employee_band"]["source"] == "derived"
    assert ev["data"]["cost_usd"] == 0.05 and ev["data"]["match"] == "simulator company ID"
    # nothing from the hidden ground truth leaks into the ledger
    assert "true_quality" not in str(ev)


def test_cache_means_one_lookup_and_one_charge(store):
    leads = generate(n=1, seed=4)
    api = _app(store, leads)
    seed(leads, LedgerClient(client=api))
    acc = leads[0].company_id
    first = api.post(f"/enrich/{acc}").json()
    again = api.post(f"/enrich/{acc}").json()
    assert not first["cached"] and again["cached"]
    assert again["event"]["event_id"] == first["event"]["event_id"]
    forced = api.post(f"/enrich/{acc}", json={"force": True}).json()
    assert not forced["cached"]
    c = api.get("/enrich/costs").json()
    assert c["lookups"] == 2 and c["accounts"] == 1 and c["cost_usd"] == 0.1


def test_form_fill_then_enrich_uses_the_email_domain(store):
    api = _app(store)
    r = api.post("/intake/hubspot", json=sample_payload(5)).json()
    e = api.post(f"/enrich/{r['account_id']}", json={"ref": r["events"][0]}).json()["event"]
    assert e["data"]["domain"] == "northpeak.example.com"
    assert e["data"]["match"] == "synthetic"
    assert e["data"]["fields"]["employees"]["source"] == "simulator-synthetic"
    assert e["refs"] == [r["events"][0]]
    # same domain, same answer: synthetic data is seeded by the domain
    again = SimulatorProvider().lookup(account_id=None, domain="northpeak.example.com")[0]
    assert again["employees"] == e["data"]["fields"]["employees"]["value"]
    # a retry from n8n with the same ref writes nothing new
    retry = api.post(f"/enrich/{r['account_id']}", json={"ref": r["events"][0],
                                                          "force": True}).json()
    assert retry["event"]["event_id"] == e["event_id"]


def test_personal_email_records_the_gap_and_costs_nothing(store):
    api = _app(store)
    body = dict(sample_payload(6), email="someone+lead1@gmail.com")
    r = api.post("/intake/hubspot", json=body).json()
    assert r["account_id"].startswith("acc_hs_")
    e = api.post(f"/enrich/{r['account_id']}").json()["event"]
    assert e["data"]["fields"] == {} and e["data"]["missing"] == ["company"]
    assert e["data"]["cost_usd"] == 0
    assert e["summary"].startswith("No company data")


def test_unknown_account_without_domain_is_404(store):
    assert _app(store).post("/enrich/acc_nobody").status_code == 404


def test_chain_still_verifies_after_enrichment(store):
    leads = generate(n=2, seed=5)
    api = _app(store, leads)
    seed(leads, LedgerClient(client=api))
    for lead in leads:
        api.post(f"/enrich/{lead.company_id}")
        assert api.get(f"/verify/{lead.company_id}").json()["ok"]
    assert costs(store)["lookups"] == 2
