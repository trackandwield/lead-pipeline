"""Synthetic leads and outcomes for Lumenvale (a fictional observability SaaS).

Every lead carries a hidden ``true_quality`` between 0 and 1 that the scorer never
sees. Outcomes (SQL, won or lost) are drawn from it, so we can measure how well
the visible signals, and the scoring built on them, predict what sales does.

The drift switch adds a new lead source part-way through. Its leads look like
a decent fit on paper but convert poorly, which is the disturbance the closed
loop and the control chart have to catch.
"""
from __future__ import annotations

import json
import math
import random
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path

from faker import Faker

START_DATE = date(2026, 1, 5)  # a Monday; week 0 of the simulation

INDUSTRIES = {
    "core": ["b2b_saas", "fintech", "ecommerce", "healthtech"],
    "adjacent": ["media", "logistics", "gaming", "edtech"],
    "other": ["manufacturing", "government", "nonprofit", "retail"],
}
TITLES = {
    "economic_buyer": ["VP Engineering", "CTO", "Head of Platform", "VP Infrastructure"],
    "champion": ["SRE Lead", "Platform Engineering Manager", "DevOps Manager",
                 "Director of Site Reliability"],
    "user": ["Site Reliability Engineer", "Backend Engineer", "Platform Engineer",
             "DevOps Engineer"],
    "unrelated": ["Marketing Manager", "HR Generalist", "Account Executive",
                  "Graduate Student"],
}
FORM_TYPES = ["demo_request", "pricing_contact", "trial_signup", "webinar", "ebook",
              "newsletter"]
SOURCES = ["organic", "paid_search", "webinar_partner", "referral"]
DRIFT_SOURCE = "partner_list_b"

# Hidden "how good is this, really" weights. Deliberately not identical to
# scoring.yaml, so the scorer is a model of reality and not a copy of it.
_EMP_FIT = [(49, 0.1), (199, 0.7), (2000, 1.0), (10000, 0.7), (math.inf, 0.35)]
_IND_FIT = {"core": 1.0, "adjacent": 0.6, "other": 0.25}
_ROLE_FIT = {"economic_buyer": 1.0, "champion": 0.9, "user": 0.5, "unrelated": 0.05}
_INTENT_FIT = {"demo_request": 1.0, "pricing_contact": 0.8, "trial_signup": 0.75,
               "webinar": 0.4, "ebook": 0.3, "newsletter": 0.15}


@dataclass
class Lead:
    lead_id: str
    created_on: str
    week: int
    source: str
    first_name: str
    last_name: str
    email: str
    job_title: str
    company_id: str
    company_name: str
    domain: str
    employees: int
    industry: str
    tech: dict[str, bool]
    form_type: str
    pricing_page_visits: int
    docs_visits: int
    trial_sent_data: bool
    personal_email: bool
    student_or_job_seeker: bool
    # Ground truth the pipeline must never read. Kept apart so it is easy to strip.
    hidden: dict = field(default_factory=dict)

    def visible(self) -> dict:
        """Everything except the hidden ground truth."""
        d = asdict(self)
        d.pop("hidden")
        return d


@dataclass
class Outcome:
    lead_id: str
    became_sql: bool
    sql_after_days: int | None
    won: bool | None
    closed_after_days: int | None


def _industry_group(industry: str) -> str:
    return next(g for g, values in INDUSTRIES.items() if industry in values)


def _emp_fit(employees: int) -> float:
    return next(fit for upper, fit in _EMP_FIT if employees <= upper)


def true_quality(lead: Lead, role: str, rng: random.Random) -> float:
    q = (0.30 * _emp_fit(lead.employees)
         + 0.20 * _IND_FIT[_industry_group(lead.industry)]
         + 0.25 * _ROLE_FIT[role]
         + 0.25 * _INTENT_FIT[lead.form_type]
         + rng.gauss(0, 0.08))
    if lead.trial_sent_data:
        q += 0.08
    if lead.personal_email:
        q -= 0.10
    if lead.student_or_job_seeker:
        q -= 0.50
    return round(min(1.0, max(0.0, q)), 3)


def _slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())[:24] or "company"


def _employees(rng: random.Random) -> int:
    # Log-normal centred near 300, clipped to a sane range.
    return int(min(60000, max(3, rng.lognormvariate(math.log(300), 1.3))))


def _company(fake: Faker, rng: random.Random, n: int, *, looks_ideal: bool = False) -> dict:
    name = fake.unique.company()
    if looks_ideal:
        employees = rng.randint(250, 1800)
        industry = rng.choice(INDUSTRIES["core"])
        tech = {"kubernetes": True, "opentelemetry": True, "competitor_tool": True,
                "aws_or_gcp": True}
    else:
        employees = _employees(rng)
        group = rng.choices(["core", "adjacent", "other"], weights=[0.5, 0.3, 0.2])[0]
        industry = rng.choice(INDUSTRIES[group])
        cloudy = employees >= 50 and group != "other"
        tech = {
            "kubernetes": rng.random() < (0.6 if cloudy else 0.15),
            "opentelemetry": rng.random() < (0.35 if cloudy else 0.05),
            "competitor_tool": rng.random() < (0.45 if cloudy else 0.1),
            "aws_or_gcp": rng.random() < (0.85 if cloudy else 0.4),
        }
    return {"company_id": f"acc_{n:05d}", "company_name": name,
            "domain": f"{_slug(name)}.example.com", "employees": employees,
            "industry": industry, "tech": tech}


def generate(n: int = 200, weeks: int = 12, seed: int = 7,
             drift_week: int | None = None, drift_share: float = 0.4) -> list[Lead]:
    """Generate ``n`` leads spread evenly over ``weeks`` weeks.

    From ``drift_week`` on, ``drift_share`` of each week's leads come from
    ``partner_list_b``: ideal-looking companies and decent titles, but
    mid-funnel forms, and a hidden quality cut to 40%.
    """
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)
    leads: list[Lead] = []
    for i in range(n):
        week = min(weeks - 1, i * weeks // n)
        drifted = drift_week is not None and week >= drift_week and rng.random() < drift_share
        company = _company(fake, rng, i, looks_ideal=drifted)
        if drifted:
            role = rng.choice(["champion", "user"])
            form = rng.choice(["webinar", "ebook"])
            source = DRIFT_SOURCE
        else:
            role = rng.choices(list(TITLES), weights=[0.15, 0.25, 0.4, 0.2])[0]
            form = rng.choices(FORM_TYPES, weights=[0.15, 0.1, 0.2, 0.2, 0.2, 0.15])[0]
            source = rng.choice(SOURCES)
        first, last = fake.first_name(), fake.last_name()
        personal = rng.random() < 0.12
        student = role == "unrelated" and rng.random() < 0.3
        email_domain = "mail.example.com" if personal else company["domain"]
        lead = Lead(
            lead_id=f"lead_{i:05d}",
            created_on=(START_DATE + timedelta(days=week * 7 + rng.randint(0, 4))).isoformat(),
            week=week,
            source=source,
            first_name=first,
            last_name=last,
            email=f"{first}.{last}.{i}@{email_domain}".lower(),
            job_title=rng.choice(TITLES[role]),
            form_type=form,
            pricing_page_visits=rng.choice([0, 0, 1, 2, 3]) if form != "newsletter" else 0,
            docs_visits=rng.choice([0, 1, 2, 3, 5]),
            trial_sent_data=form == "trial_signup" and rng.random() < 0.5,
            personal_email=personal,
            student_or_job_seeker=student,
            **company,
        )
        q = true_quality(lead, role, rng)
        if drifted:
            q = round(q * 0.4, 3)
        lead.hidden = {"true_quality": q, "role": role, "drifted": drifted}
        leads.append(lead)
    return leads


def sample_outcome(lead: Lead, rng: random.Random) -> Outcome:
    """What sales does with a lead once it is handed over as an MQL."""
    q = lead.hidden["true_quality"]
    if rng.random() >= 0.05 + 0.80 * q:
        return Outcome(lead.lead_id, False, None, None, None)
    sql_days = rng.randint(1, 10)
    won = rng.random() < 0.10 + 0.50 * q
    return Outcome(lead.lead_id, True, sql_days, won, sql_days + rng.randint(20, 60))


def write_jsonl(rows, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row if isinstance(row, dict) else asdict(row)) + "\n")


def read_leads(path: Path) -> list[Lead]:
    with path.open(encoding="utf-8") as f:
        return [Lead(**json.loads(line)) for line in f if line.strip()]
