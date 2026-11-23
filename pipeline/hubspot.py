"""Small HubSpot CRM client: custom properties, and seeding contacts and companies.

Auth is a HubSpot service key (or a legacy private app token), sent as a Bearer token.
Only visible lead fields are sent. The simulator's hidden ground truth never leaves
this machine.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator

import httpx

from pipeline.simulator import Lead

BASE_URL = "https://api.hubapi.com"
GROUP = {"name": "lead_pipeline", "label": "Lead pipeline", "displayOrder": -1}
BATCH = 100  # HubSpot's batch limit

CONTACT_PROPERTIES = [
    {"name": "lead_score", "label": "Lead score", "type": "number", "fieldType": "number"},
    {"name": "lead_tier", "label": "Lead tier", "type": "enumeration", "fieldType": "select",
     "options": [{"label": t, "value": t.lower(), "displayOrder": i}
                 for i, t in enumerate(["Hot", "Warm", "Nurture"])]},
    {"name": "score_reason", "label": "Score reason", "type": "string", "fieldType": "textarea"},
    {"name": "score_version", "label": "Score version", "type": "string", "fieldType": "text"},
    {"name": "ledger_person_id", "label": "Ledger person ID", "type": "string",
     "fieldType": "text", "hasUniqueValue": True},
    {"name": "sim_source", "label": "Lead source (simulated)", "type": "string",
     "fieldType": "text"},
]
COMPANY_PROPERTIES = [
    {"name": "ledger_account_id", "label": "Ledger account ID", "type": "string",
     "fieldType": "text", "hasUniqueValue": True},
]


def chunks(items: list, size: int = BATCH) -> Iterator[list]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def company_input(lead: Lead) -> dict:
    return {"idProperty": "ledger_account_id", "id": lead.company_id, "properties": {
        "ledger_account_id": lead.company_id,
        "name": lead.company_name,
        "domain": lead.domain,
        "numberofemployees": str(lead.employees),
    }}


def contact_input(lead: Lead) -> dict:
    v = lead.visible()
    return {"idProperty": "email", "id": v["email"], "properties": {
        "email": v["email"],
        "firstname": v["first_name"],
        "lastname": v["last_name"],
        "jobtitle": v["job_title"],
        "company": v["company_name"],
        "lifecyclestage": "lead",
        "ledger_person_id": v["lead_id"].replace("lead_", "per_"),
        "sim_source": v["source"],
    }}


def _clean(inp: dict) -> dict:
    inp["properties"] = {k: v for k, v in inp["properties"].items() if v is not None}
    return inp


class HubSpot:
    def __init__(self, token: str, client: httpx.Client | None = None):
        self.client = client or httpx.Client(
            base_url=BASE_URL, timeout=30,
            headers={"Authorization": f"Bearer {token}"})

    def _call(self, method: str, path: str, **kw) -> httpx.Response:
        r = self.client.request(method, path, **kw)
        if r.status_code >= 400 and r.status_code != 409:
            raise RuntimeError(f"HubSpot {method} {path} -> {r.status_code}: {r.text[:300]}")
        return r

    def ping(self) -> str:
        self._call("GET", "/crm/v3/objects/contacts", params={"limit": 1})
        return "ok"

    def ensure_properties(self) -> list[str]:
        """Create the property group and custom properties. Safe to re-run."""
        created = []
        for obj, props in (("contacts", CONTACT_PROPERTIES), ("companies", COMPANY_PROPERTIES)):
            self._call("POST", f"/crm/v3/properties/{obj}/groups", json=GROUP)
            for prop in props:
                r = self._call("POST", f"/crm/v3/properties/{obj}",
                               json={**prop, "groupName": GROUP["name"]})
                if r.status_code != 409:
                    created.append(f"{obj}.{prop['name']}")
        return created

    def upsert(self, obj: str, inputs: Iterable[dict]) -> dict[str, str]:
        """Batch upsert by a unique property; returns our id -> HubSpot record id."""
        ids: dict[str, str] = {}
        for batch in chunks([_clean(i) for i in inputs]):
            id_prop = batch[0]["idProperty"]
            self._call("POST", f"/crm/v3/objects/{obj}/batch/upsert", json={"inputs": batch})
            # Read back by the same unique property: upsert results don't promise to echo it.
            r = self._call("POST", f"/crm/v3/objects/{obj}/batch/read", json={
                "idProperty": id_prop, "properties": [id_prop],
                "inputs": [{"id": i["id"]} for i in batch]})
            for res in r.json().get("results", []):
                ids[res["properties"][id_prop]] = res["id"]
        return ids

    def associate_contacts_to_companies(self, pairs: list[tuple[str, str]]) -> None:
        for batch in chunks(pairs):
            self._call("POST", "/crm/v4/associations/contacts/companies/batch/associate/default",
                       json={"inputs": [{"from": {"id": c}, "to": {"id": co}}
                                        for c, co in batch]})

    def seed(self, leads: list[Lead]) -> dict:
        companies = {lead.company_id: company_input(lead) for lead in leads}
        company_ids = self.upsert("companies", companies.values())
        contact_ids = self.upsert("contacts", (contact_input(lead) for lead in leads))
        pairs = [(contact_ids[lead.email], company_ids[lead.company_id]) for lead in leads
                 if lead.email in contact_ids and lead.company_id in company_ids]
        self.associate_contacts_to_companies(pairs)
        return {"companies": len(company_ids), "contacts": len(contact_ids),
                "associations": len(pairs)}
