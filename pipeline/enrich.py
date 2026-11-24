"""Enrichment (component 5): add company data to an account and record where it came from.

n8n calls POST /enrich/{account_id} right after intake. The result is one `enriched`
event in the customer ledger, with every field, the provider it came from, and what
the lookup cost. The ledger doubles as the cache: an account enriched by the same
provider in the last ENRICH_CACHE_DAYS days is not looked up (or paid for) again.

Providers are swappable. The default reads the simulator's companies, so it is free,
works offline, and lets later components measure scoring against the simulator's
hidden ground truth. A paid API (Apollo, Clearbit, Clay) is one more Provider class.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from pipeline.intake import PERSONAL_DOMAINS
from pipeline.ledger.model import EventIn
from pipeline.ledger.store import LedgerStore, NotFound
from pipeline.simulator import INDUSTRIES

ACTOR = {"kind": "system", "id": "enrichment", "version": "1"}
TECH_NAMES = {"kubernetes": "Kubernetes", "opentelemetry": "OpenTelemetry",
              "competitor_tool": "Competing observability tool", "aws_or_gcp": "AWS or GCP"}
BANDS = [(49, "1-49"), (199, "50-199"), (999, "200-999"), (4999, "1000-4999"),
         (math.inf, "5000+")]
FUNDING = [(49, ["Bootstrapped", "Seed"]), (199, ["Seed", "Series A"]),
           (999, ["Series A", "Series B", "Series C"]),
           (4999, ["Series C", "Late stage", "Public"]), (math.inf, ["Public", "Private equity"])]


class Provider(Protocol):
    name: str
    version: str
    cost_per_lookup: float

    def lookup(self, *, account_id: str | None, domain: str | None) -> tuple[dict | None, str]:
        """(company record or None, how it matched)."""


def _band(employees: int) -> str:
    return next(b for upper, b in BANDS if employees <= upper)


def _group(industry: str) -> str:
    return next((g for g, values in INDUSTRIES.items() if industry in values), "other")


def _seeded_rng(text: str) -> random.Random:
    return random.Random(int(hashlib.sha256(text.encode()).hexdigest()[:16], 16))


def _funding(domain: str, employees: int) -> str:
    options = next(o for upper, o in FUNDING if employees <= upper)
    return _seeded_rng("funding:" + domain).choice(options)


class SimulatorProvider:
    """Companies from the simulator's leads file (visible fields only).

    A domain the simulator doesn't know (a real test fill from your own domain) gets
    synthetic data seeded by the domain, so the same domain always gets the same
    answer, and the event says it is synthetic.
    """
    name = "simulator"
    version = "1"

    def __init__(self, leads_file: Path | None = None, cost_per_lookup: float | None = None):
        self.cost_per_lookup = (cost_per_lookup if cost_per_lookup is not None else
                                float(os.environ.get("ENRICH_COST_PER_LOOKUP", "0.05")))
        self.by_id: dict[str, dict] = {}
        self.by_domain: dict[str, dict] = {}
        if leads_file and leads_file.exists():
            with leads_file.open(encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self.add(json.loads(line))

    def add(self, lead: dict) -> None:
        company = {k: lead[k] for k in ("company_id", "company_name", "domain", "employees",
                                        "industry", "tech")}
        self.by_id[company["company_id"]] = company
        self.by_domain[company["domain"]] = company

    def lookup(self, *, account_id=None, domain=None):
        if account_id in self.by_id:
            return self.by_id[account_id], "simulator company ID"
        if domain in self.by_domain:
            return self.by_domain[domain], "simulator domain"
        if not domain:
            return None, "no domain to look up"
        rng = _seeded_rng(domain)
        employees = int(min(60000, max(3, rng.lognormvariate(math.log(300), 1.3))))
        group = rng.choices(["core", "adjacent", "other"], weights=[0.5, 0.3, 0.2])[0]
        cloudy = employees >= 50 and group != "other"
        return {"company_name": None, "domain": domain, "employees": employees,
                "industry": rng.choice(INDUSTRIES[group]),
                "tech": {"kubernetes": rng.random() < (0.6 if cloudy else 0.15),
                         "opentelemetry": rng.random() < (0.35 if cloudy else 0.05),
                         "competitor_tool": rng.random() < (0.45 if cloudy else 0.1),
                         "aws_or_gcp": rng.random() < (0.85 if cloudy else 0.4)}}, "synthetic"


def default_provider() -> Provider:
    from pipeline import config
    name = os.environ.get("ENRICH_PROVIDER", "simulator")
    if name != "simulator":
        raise ValueError(f"unknown ENRICH_PROVIDER '{name}' (only 'simulator' so far)")
    return SimulatorProvider(Path(os.environ.get("ENRICH_LEADS_FILE",
                                                 config.DATA_DIR / "sim" / "leads.jsonl")))


def _fields(company: dict, source: str) -> dict:
    """Each field as {value, source}, so a reader can see where every value came from."""
    tech = sorted(TECH_NAMES[k] for k, on in company["tech"].items() if on)
    values = {"company_name": company.get("company_name"), "domain": company["domain"],
              "employees": company["employees"], "employee_band": _band(company["employees"]),
              "industry": company["industry"], "industry_group": _group(company["industry"]),
              "funding_stage": _funding(company["domain"], company["employees"]),
              "tech_stack": tech}
    # Bands and groups are computed from other fields. The simulator has no funding
    # data, so funding is made up from the size and domain and labelled as such.
    sources = {"employee_band": "derived", "industry_group": "derived",
               "funding_stage": "synthetic"}
    return {k: {"value": v, "source": sources.get(k, source)}
            for k, v in values.items() if v is not None}


def _domain_from_timeline(events: list[dict]) -> str | None:
    for e in reversed(events):
        email = e["data"].get("email") if e["type"] == "form_submitted" else None
        if email and "@" in email:
            return email.rsplit("@", 1)[1].lower()
    return None


def _cached(events: list[dict], provider: Provider, days: int) -> dict | None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    for e in reversed(events):
        if (e["type"] == "enriched" and e["data"].get("provider") == provider.name
                and datetime.fromisoformat(e["recorded_at"].replace("Z", "+00:00")) >= cutoff):
            return e
    return None


def enrich(store: LedgerStore, account_id: str, provider: Provider, *, domain: str | None = None,
           ref: str | None = None, force: bool = False, cache_days: int | None = None) -> dict:
    cache_days = cache_days if cache_days is not None else int(
        os.environ.get("ENRICH_CACHE_DAYS", "30"))
    events = store.timeline(account_id)
    if not force:
        hit = _cached(events, provider, cache_days)
        if hit:
            return {"account_id": account_id, "cached": True, "event": hit}
    domain = (domain or _domain_from_timeline(events) or "").lower() or None
    known = account_id in getattr(provider, "by_id", {})
    if not events and not known and not domain:
        raise NotFound(f"no events for {account_id} and no domain given")

    person = next((e["person_id"] for e in reversed(events) if e.get("person_id")), None)
    if domain in PERSONAL_DOMAINS and not known:
        company, how, cost = None, "personal email: no company to look up", 0.0
    else:
        company, how = provider.lookup(account_id=account_id, domain=domain)
        cost = provider.cost_per_lookup

    if company:
        source = provider.name + ("-synthetic" if how == "synthetic" else "")
        fields = _fields(company, source)
        missing = [k for k in ("company_name",) if k not in fields]
        summary = (f"Enriched: {company['employees']} employees, {company['industry']}, "
                   f"{fields['funding_stage']['value']} ({source})")
    else:
        fields, missing = {}, ["company"]
        summary = f"No company data: {how}"

    ev = store.append(EventIn(
        type="enriched", account_id=account_id, person_id=person, actor=ACTOR,
        source={"system": provider.name}, summary=summary[:300],
        refs=[ref] if ref else [],
        idempotency_key=f"enrich:{account_id}:{provider.name}:{ref}" if ref else None,
        data={"fields": fields, "missing": missing, "provider": provider.name,
              "provider_version": provider.version, "match": how, "domain": domain,
              "cost_usd": round(cost, 4)}))
    return {"account_id": account_id, "cached": False, "event": ev}


def costs(store: LedgerStore) -> dict:
    """What enrichment has cost so far, from the `enriched` events themselves."""
    evs = store.events_of_type("enriched")
    total = round(sum(e["data"].get("cost_usd", 0) for e in evs), 4)
    accounts = {e["account_id"] for e in evs}
    by_provider: dict[str, dict] = {}
    for e in evs:
        p = by_provider.setdefault(e["data"].get("provider", "?"), {"lookups": 0, "cost_usd": 0.0})
        p["lookups"] += 1
        p["cost_usd"] = round(p["cost_usd"] + e["data"].get("cost_usd", 0), 4)
    return {"lookups": len(evs), "accounts": len(accounts), "cost_usd": total,
            "cost_per_account_usd": round(total / len(accounts), 4) if accounts else 0.0,
            "no_company_data": sum(1 for e in evs if not e["data"]["fields"]),
            "by_provider": by_provider}
