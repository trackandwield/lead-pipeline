"""Backfill the ledger with what the simulator knows about each lead before scoring.

Per lead: earlier anonymous web visits, the campaign that brought them in, the form
fill, the identity link from visitor to person, and the move from anonymous to known.
Later components (scoring, routing, the Salesforce handoff) write the events after
that. Only visible fields are written: the hidden ground truth never enters the ledger.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from pipeline.ledger.client import LedgerClient
from pipeline.simulator import Lead

ACTOR = {"kind": "system", "id": "simulator", "version": "1"}
UTM = {"organic": "google", "paid_search": "google_ads", "webinar_partner": "partner_webinar",
       "referral": "customer_referral", "partner_list_b": "partner_list_b"}
CAMPAIGNS = {
    "webinar": ("Kubernetes observability live webinar", "webinar", "registered"),
    "ebook": ("OpenTelemetry migration guide", "content_syndication", "downloaded"),
}
FORM_PAGES = {"demo_request": "/demo", "pricing_contact": "/pricing/contact",
              "trial_signup": "/trial", "webinar": "/webinars/k8s-observability",
              "ebook": "/guides/opentelemetry-migration", "newsletter": "/newsletter"}


def person_id(lead: Lead) -> str:
    return lead.lead_id.replace("lead_", "per_")


def anonymous_id(lead: Lead) -> str:
    return "anon_" + hashlib.sha256(lead.lead_id.encode()).hexdigest()[:12]


def lead_events(lead: Lead) -> list[dict]:
    """The pre-scoring events for one lead, oldest first. refs use list positions."""
    v = lead.visible()
    form_at = datetime.fromisoformat(v["created_on"]).replace(
        hour=15, tzinfo=timezone.utc) + timedelta(minutes=int(v["lead_id"][-2:]))
    base = {"account_id": v["company_id"], "actor": ACTOR,
            "source": {"system": "simulator", "record_id": v["lead_id"]}}
    pid, anon = person_id(lead), anonymous_id(lead)
    events: list[dict] = []

    pages = ["/pricing"] * v["pricing_page_visits"] + ["/docs/kubernetes"] * v["docs_visits"]
    if pages:
        events.append(dict(base, type="web_visit", stage="anonymous",
                           occurred_at=(form_at - timedelta(days=2)).isoformat(),
                           summary=f"Anonymous visitor viewed {len(pages)} pages "
                                   f"({', '.join(sorted(set(pages)))})",
                           data={"anonymous_id": anon, "pages": pages}))
    if v["form_type"] in CAMPAIGNS:
        name, channel, response = CAMPAIGNS[v["form_type"]]
        events.append(dict(base, type="campaign_touched", person_id=pid,
                           stage="anonymous", occurred_at=form_at.isoformat(),
                           summary=f"{response.capitalize()}: {name}",
                           data={"campaign": name, "channel": channel, "response": response,
                                 "utm_source": UTM[v["source"]]}))
    form_idx = len(events)
    events.append(dict(base, type="form_submitted", person_id=pid, stage="known",
                       occurred_at=form_at.isoformat(),
                       summary=f"{v['form_type'].replace('_', ' ').capitalize()} form from "
                               f"{v['job_title']} at {v['company_name']}",
                       data={"form_type": v["form_type"], "page": FORM_PAGES[v["form_type"]],
                             "utm_source": UTM[v["source"]], "job_title": v["job_title"],
                             "email": v["email"], "first_name": v["first_name"],
                             "last_name": v["last_name"]},
                       pii=["email", "first_name", "last_name"]))
    if pages:
        events.append(dict(base, type="identity_linked", person_id=pid, stage="known",
                           occurred_at=(form_at + timedelta(seconds=5)).isoformat(),
                           summary=f"Linked earlier anonymous visits to {pid}",
                           reason="Same first-party cookie on the visits and the form fill",
                           inputs={"method": "cookie", "matcher_version": "sim-1"},
                           data={"link_from": anon, "link_to": pid}, refs=[0, form_idx]))
    events.append(dict(base, type="stage_changed", person_id=pid, stage="known",
                       occurred_at=(form_at + timedelta(seconds=6)).isoformat(),
                       summary="Stage anonymous to known: identified by a form fill",
                       reason="A form fill gives a named contact",
                       inputs={"rule": "form_submitted -> known"},
                       data={"from_stage": "anonymous", "to_stage": "known"},
                       refs=[form_idx]))
    return events


def seed(leads: list[Lead], client: LedgerClient) -> dict:
    counts: dict[str, int] = {}
    for lead in leads:
        written: list[str] = []
        for ev in lead_events(lead):
            ev = dict(ev, refs=[written[i] for i in ev.get("refs", [])],
                      idempotency_key=f"sim:{lead.lead_id}:{ev['type']}")
            written.append(client.post_event(ev)["event_id"])
            counts[ev["type"]] = counts.get(ev["type"], 0) + 1
    return {"accounts": len({lead.company_id for lead in leads}),
            "events": sum(counts.values()), "by_type": counts}
