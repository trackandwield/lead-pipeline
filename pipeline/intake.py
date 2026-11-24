"""Intake: turn a HubSpot form fill into ledger events.

HubSpot workflow ("Send a webhook") -> n8n webhook -> POST /intake/hubspot here.
n8n does the orchestration; the rules live here, where they are tested.

Accepts both webhook shapes HubSpot sends:
  - a custom body of chosen properties: {"hs_object_id": "123", "email": "...", ...}
  - the "all properties" body: {"vid": 123, "properties": {"email": {"value": "..."}}}
"""
from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException

from pipeline.ledger.model import EventIn
from pipeline.ledger.store import LedgerStore

ACTOR = {"kind": "system", "id": "n8n-intake", "version": "1"}
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PERSONAL_DOMAINS = {"gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com",
                    "proton.me", "aol.com", "mail.example.com"}
FORM_WORDS = [("demo", "demo_request"), ("pricing", "pricing_contact"), ("trial", "trial_signup"),
              ("webinar", "webinar"), ("ebook", "ebook"), ("guide", "ebook"),
              ("newsletter", "newsletter")]


class IntakeError(ValueError):
    pass


def flatten(payload: dict) -> dict:
    """One flat dict of property -> value, whichever shape HubSpot sent."""
    props = payload.get("properties")
    if isinstance(props, dict):
        flat = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in props.items()}
        if payload.get("vid") is not None:
            flat.setdefault("hs_object_id", str(payload["vid"]))
        return flat
    return dict(payload)


def form_type(props: dict) -> str:
    if props.get("form_type"):
        return str(props["form_type"])
    name = str(props.get("recent_conversion_event_name") or "").lower()
    return next((t for word, t in FORM_WORDS if word in name), "other")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())[:40]


def resolve_ids(props: dict, contact_id: str, email: str) -> tuple[str, str, str]:
    """(account_id, person_id, how the account was found)."""
    person = props.get("ledger_person_id") or f"per_hs_{contact_id}"
    if props.get("ledger_account_id"):
        return props["ledger_account_id"], person, "ledger_account_id on the company"
    domain = email.rsplit("@", 1)[1].lower()
    if domain in PERSONAL_DOMAINS:
        return f"acc_hs_{contact_id}", person, "personal email: an account of one"
    return f"acc_dom_{_slug(domain)}", person, f"email domain {domain}"


def _when(props: dict) -> datetime:
    raw = props.get("recent_conversion_date") or props.get("submitted_at")
    if raw:
        try:
            if str(raw).isdigit():  # HubSpot sends epoch milliseconds
                return datetime.fromtimestamp(int(raw) / 1000, tz=timezone.utc)
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def intake(store: LedgerStore, payload: dict, portal_id: str | None = None) -> dict:
    props = flatten(payload)
    contact_id = str(props.get("hs_object_id") or props.get("contact_id") or "").strip()
    email = str(props.get("email") or "").strip().lower()
    if not contact_id:
        raise IntakeError("no HubSpot contact ID (hs_object_id) in the webhook")
    if not EMAIL.match(email):
        raise IntakeError(f"not a valid email: '{email}'")
    account, person, how = resolve_ids(props, contact_id, email)
    when = _when(props)
    ftype = form_type(props)
    url = (f"https://app.hubspot.com/contacts/{portal_id}/record/0-1/{contact_id}"
           if portal_id else None)
    source = {"system": "hubspot", "record_id": contact_id, **({"url": url} if url else {})}
    stamp = int(when.timestamp())
    form_key = f"hubspot:{contact_id}:form:{stamp}"
    duplicate = store.find_by_key(form_key) is not None

    form = store.append(EventIn(
        type="form_submitted", account_id=account, person_id=person, stage="known",
        actor=ACTOR, source=source, occurred_at=when, idempotency_key=form_key,
        summary=(f"{ftype.replace('_', ' ').capitalize()} form from "
                 f"{props.get('jobtitle') or 'unknown title'} at "
                 f"{props.get('company') or email.rsplit('@', 1)[1]}"),
        data={"form_type": ftype, "form_name": props.get("recent_conversion_event_name"),
              "utm_source": props.get("hs_analytics_source"),
              "job_title": props.get("jobtitle"), "company": props.get("company"),
              "hubspot_contact_id": contact_id, "account_match": how,
              "email": email, "first_name": props.get("firstname"),
              "last_name": props.get("lastname")},
        pii=["email", "first_name", "last_name"]))
    written = [form["event_id"]]

    # The first form fill makes someone known. Later fills don't change the stage.
    known_key = f"hubspot:{contact_id}:known"
    if store.find_by_key(known_key) is None and not _already_known(store, account, person):
        stage = store.append(EventIn(
            type="stage_changed", account_id=account, person_id=person, stage="known",
            actor=ACTOR, source=source, occurred_at=when, idempotency_key=known_key,
            summary="Stage anonymous to known: identified by a form fill",
            reason="A form fill gives a named contact",
            inputs={"rule": "first form_submitted -> known"},
            data={"from_stage": "anonymous", "to_stage": "known"},
            refs=[form["event_id"]]))
        written.append(stage["event_id"])
    return {"account_id": account, "person_id": person, "account_match": how,
            "duplicate": duplicate, "events": written}


def _already_known(store: LedgerStore, account: str, person: str) -> bool:
    return any(e["type"] == "stage_changed" and e["person_id"] == person
               for e in store.timeline(account))


def router(store: LedgerStore) -> APIRouter:
    r = APIRouter(tags=["intake"])

    @r.post("/intake/hubspot", status_code=201)
    def hubspot_intake(payload: dict, x_intake_key: str | None = Header(None)):
        """Called by the n8n intake workflow with HubSpot's webhook body."""
        secret = os.environ.get("INTAKE_SHARED_SECRET")
        if secret and x_intake_key != secret:
            raise HTTPException(401, "missing or wrong X-Intake-Key")
        try:
            return intake(store, payload, os.environ.get("HUBSPOT_PORTAL_ID"))
        except IntakeError as e:
            raise HTTPException(422, str(e)) from e

    return r


def sample_payload(n: int = 1) -> dict:
    """A fake HubSpot webhook body, for intake-test and the tests."""
    h = hashlib.sha256(str(n).encode()).hexdigest()[:6]
    return {"hs_object_id": str(900000 + n), "email": f"jordan.reyes.{h}@northpeak.example.com",
            "firstname": "Jordan", "lastname": "Reyes", "jobtitle": "SRE Lead",
            "company": "Northpeak Logistics",
            "recent_conversion_event_name": "Lumenvale demo request",
            "recent_conversion_date": str(int(datetime.now(timezone.utc).timestamp() * 1000)),
            "hs_analytics_source": "ORGANIC_SEARCH"}
