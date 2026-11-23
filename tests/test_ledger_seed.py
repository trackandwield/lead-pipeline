from fastapi.testclient import TestClient

from pipeline.ledger.api import create_app
from pipeline.ledger.client import LedgerClient
from pipeline.ledger.seed import lead_events, seed
from pipeline.simulator import generate


def test_seed_writes_verifiable_timelines_without_hidden_truth(store):
    leads = generate(n=30, seed=5)
    client = LedgerClient(client=TestClient(create_app(store)))
    result = seed(leads, client)
    assert result["accounts"] == 30
    assert result["by_type"]["form_submitted"] == 30
    assert result["by_type"]["stage_changed"] == 30
    for lead in leads:
        assert client.verify(lead.company_id)["ok"]
        timeline = client.timeline(lead.company_id)
        assert "true_quality" not in str(timeline) and "drifted" not in str(timeline)
        assert timeline[-1]["data"]["to_stage"] == "known"
    again = seed(leads, client)  # a rerun returns the same events, no duplicates
    assert again["events"] == result["events"]
    assert len(client.timeline(leads[0].company_id)) == len(lead_events(leads[0]))


def test_identity_link_only_when_there_were_visits():
    for lead in generate(n=40):
        types = [e["type"] for e in lead_events(lead)]
        visited = lead.pricing_page_visits + lead.docs_visits > 0
        assert ("web_visit" in types) == visited == ("identity_linked" in types)
