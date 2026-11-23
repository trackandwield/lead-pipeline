import httpx

from pipeline.hubspot import HubSpot, chunks, contact_input, company_input
from pipeline.simulator import generate


def test_payloads_never_carry_hidden_truth():
    lead = generate(n=1)[0]
    for payload in (contact_input(lead), company_input(lead)):
        text = str(payload)
        assert "true_quality" not in text and "drifted" not in text


def test_contacts_upsert_by_email():
    lead = generate(n=1)[0]
    c = contact_input(lead)
    assert c["idProperty"] == "email" and c["id"] == lead.email


def test_chunks_respect_batch_limit():
    assert [len(c) for c in chunks(list(range(250)))] == [100, 100, 50]


def test_seed_maps_ids_and_associates(monkeypatch):
    leads = generate(n=3)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content or b"{}")
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/batch/read"):
            prop = body["idProperty"]
            return httpx.Response(200, json={"results": [
                {"id": f"hs-{i['id']}", "properties": {prop: i["id"]}} for i in body["inputs"]]})
        return httpx.Response(200, json={"results": []})

    client = httpx.Client(base_url="https://api.hubapi.com",
                          transport=httpx.MockTransport(handler))
    result = HubSpot("t", client=client).seed(leads)
    assert result == {"companies": 3, "contacts": 3, "associations": 3}
    assert ("POST", "/crm/v4/associations/contacts/companies/batch/associate/default") in calls
