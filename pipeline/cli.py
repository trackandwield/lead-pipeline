"""Command line: python -m pipeline <command>."""
from __future__ import annotations

import argparse
import os
import random
from collections import Counter
from pathlib import Path
from statistics import mean

from pipeline import config
from pipeline.simulator import generate, read_leads, sample_outcome, write_jsonl

DEFAULT_SIM = config.DATA_DIR / "sim" / "leads.jsonl"


def cmd_sim(args) -> int:
    leads = generate(n=args.n, weeks=args.weeks, seed=args.seed,
                     drift_week=args.drift_week, drift_share=args.drift_share)
    rng = random.Random(args.seed + 1)
    outcomes = [sample_outcome(lead, rng) for lead in leads]
    out = Path(args.out)
    write_jsonl(leads, out)
    write_jsonl(outcomes, out.with_name("outcomes.jsonl"))
    drifted = [lead for lead in leads if lead.hidden["drifted"]]
    print(f"Wrote {len(leads)} leads and outcomes to {out.parent}")
    print(f"  sources: {dict(Counter(lead.source for lead in leads))}")
    print(f"  mean true quality: {mean(lead.hidden['true_quality'] for lead in leads):.2f}")
    if drifted:
        print(f"  drifted leads: {len(drifted)}, mean true quality "
              f"{mean(lead.hidden['true_quality'] for lead in drifted):.2f}")
    print(f"  SQL rate if every lead were an MQL: "
          f"{mean(o.became_sql for o in outcomes):.0%}")
    return 0


def _hubspot():
    from pipeline.hubspot import HubSpot
    config.load_env()
    return HubSpot(config.require_env("HUBSPOT_ACCESS_TOKEN")["HUBSPOT_ACCESS_TOKEN"])


def cmd_hubspot_setup(args) -> int:
    created = _hubspot().ensure_properties()
    print("Created: " + (", ".join(created) if created else "nothing new (already set up)"))
    return 0


def cmd_hubspot_seed(args) -> int:
    leads = read_leads(Path(args.file))[: args.limit]
    print(f"Seeding {len(leads)} leads to HubSpot (hidden ground truth is not sent)...")
    print(_hubspot().seed(leads))
    return 0


def _ledger_url(args) -> str:
    config.load_env()
    return args.url or os.environ.get("LEDGER_URL") or "http://127.0.0.1:8800"


def cmd_ledger_serve(args) -> int:
    import uvicorn
    uvicorn.run("pipeline.ledger.api:app_from_env", factory=True, host=args.host, port=args.port)
    return 0


def cmd_ledger_seed(args) -> int:
    from pipeline.ledger.client import LedgerClient
    from pipeline.ledger.seed import seed
    leads = read_leads(Path(args.file))[: args.limit]
    url = _ledger_url(args)
    print(f"Writing pre-scoring events for {len(leads)} leads to the ledger at {url}...")
    result = seed(leads, LedgerClient(url))
    print(f"  {result['events']} events across {result['accounts']} accounts")
    for t, n in sorted(result["by_type"].items()):
        print(f"  {t}: {n}")
    return 0


def cmd_ledger_verify(args) -> int:
    from pipeline.ledger.client import LedgerClient
    client = LedgerClient(_ledger_url(args))
    accounts = args.account or sorted({lead.company_id for lead in read_leads(Path(args.file))})
    broken = []
    for account in accounts:
        r = client.verify(account)
        if not r["ok"]:
            broken.append(r)
            print(f"[broken] {account}: {r['problem']} at {r['broken_at']}")
    print(f"Verified {len(accounts) - len(broken)} of {len(accounts)} account chains")
    return 1 if broken else 0


def cmd_intake_test(args) -> int:
    """Send a fake HubSpot form fill through n8n (or straight to the API with --direct)."""
    import httpx

    from pipeline.intake import sample_payload
    config.load_env()
    url = args.url or (f"{_ledger_url(args)}/intake/hubspot" if args.direct else
                       os.environ.get("N8N_INTAKE_WEBHOOK",
                                      "http://localhost:5678/webhook/hubspot-intake"))
    body = sample_payload(args.n)
    r = httpx.post(url, json=body, timeout=30,
                   headers={"X-Intake-Key": os.environ.get("INTAKE_SHARED_SECRET", "")})
    print(f"POST {url} -> {r.status_code}")
    print(r.text[:1000])
    return 0 if r.status_code < 300 else 1


def cmd_enrich_backfill(args) -> int:
    """Enrich every simulated account (cached accounts are skipped and cost nothing)."""
    from pipeline.ledger.client import LedgerClient
    client = LedgerClient(_ledger_url(args))
    accounts = sorted({lead.company_id for lead in read_leads(Path(args.file))[: args.limit]})
    cached = 0
    for account in accounts:
        cached += client.enrich(account, force=args.force)["cached"]
    print(f"Enriched {len(accounts) - cached} accounts, {cached} already cached")
    return cmd_enrich_costs(args)


def cmd_enrich_costs(args) -> int:
    from pipeline.ledger.client import LedgerClient
    c = LedgerClient(_ledger_url(args)).enrich_costs()
    print(f"Lookups: {c['lookups']} across {c['accounts']} accounts, "
          f"${c['cost_usd']:.2f} total, ${c['cost_per_account_usd']:.4f} per account, "
          f"{c['no_company_data']} with no company data")
    return 0


def cmd_check(args) -> int:
    """Check each connection in .env without printing any secret."""
    config.load_env()
    ok = True

    def report(name, fn, needed):
        nonlocal ok
        missing = [n for n in needed if not os.environ.get(n)]
        if missing:
            print(f"[skip] {name}: not in .env ({', '.join(missing)})")
            return
        try:
            print(f"[ok]   {name}: {fn()}")
        except Exception as exc:  # report and keep checking the rest
            ok = False
            print(f"[fail] {name}: {exc}")

    def hubspot():
        return _hubspot().ping()

    def salesforce():
        from pipeline.salesforce import Salesforce
        return Salesforce(os.environ["SF_DOMAIN"], os.environ["SF_CLIENT_ID"],
                          os.environ["SF_CLIENT_SECRET"]).ping()

    def ledger():
        from pipeline.ledger.client import LedgerClient
        r = LedgerClient(os.environ["LEDGER_URL"]).http.get("/health")
        r.raise_for_status()
        return "ok (API reachable)"

    report("HubSpot", hubspot, ["HUBSPOT_ACCESS_TOKEN"])
    report("Salesforce", salesforce, ["SF_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET"])
    report("Ledger", ledger, ["LEDGER_URL"])
    for name in ("ANTHROPIC_API_KEY", "SLACK_WEBHOOK_LEAD_ALERTS", "SLACK_WEBHOOK_PIPELINE_HEALTH"):
        print(f"[{'set' if os.environ.get(name) else 'skip'}]  {name}")
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m pipeline")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("sim", help="generate synthetic leads and outcomes")
    s.add_argument("--n", type=int, default=200)
    s.add_argument("--weeks", type=int, default=12)
    s.add_argument("--seed", type=int, default=7)
    s.add_argument("--drift-week", type=int, default=None)
    s.add_argument("--drift-share", type=float, default=0.4)
    s.add_argument("--out", default=str(DEFAULT_SIM))
    s.set_defaults(fn=cmd_sim)

    s = sub.add_parser("hubspot-setup", help="create the custom HubSpot properties")
    s.set_defaults(fn=cmd_hubspot_setup)

    s = sub.add_parser("hubspot-seed", help="load simulated leads into HubSpot")
    s.add_argument("--file", default=str(DEFAULT_SIM))
    s.add_argument("--limit", type=int, default=200)
    s.set_defaults(fn=cmd_hubspot_seed)

    s = sub.add_parser("ledger-serve", help="run the customer ledger API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8800)
    s.set_defaults(fn=cmd_ledger_serve)

    s = sub.add_parser("ledger-seed", help="write the simulated leads' first events")
    s.add_argument("--file", default=str(DEFAULT_SIM))
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--url", default=None)
    s.set_defaults(fn=cmd_ledger_seed)

    s = sub.add_parser("ledger-verify", help="check every account's hash chain")
    s.add_argument("--file", default=str(DEFAULT_SIM))
    s.add_argument("--account", action="append", help="one account (repeatable)")
    s.add_argument("--url", default=None)
    s.set_defaults(fn=cmd_ledger_verify)

    s = sub.add_parser("intake-test", help="send a fake HubSpot form fill through intake")
    s.add_argument("--n", type=int, default=1, help="which fake person (same n = same person)")
    s.add_argument("--direct", action="store_true", help="skip n8n, call the API")
    s.add_argument("--url", default=None)
    s.set_defaults(fn=cmd_intake_test)

    s = sub.add_parser("enrich-backfill", help="enrich every simulated account")
    s.add_argument("--file", default=str(DEFAULT_SIM))
    s.add_argument("--limit", type=int, default=200)
    s.add_argument("--force", action="store_true", help="skip the cache and look up again")
    s.add_argument("--url", default=None)
    s.set_defaults(fn=cmd_enrich_backfill)

    s = sub.add_parser("enrich-costs", help="show what enrichment has cost so far")
    s.add_argument("--url", default=None)
    s.set_defaults(fn=cmd_enrich_costs)

    s = sub.add_parser("check", help="test the HubSpot and Salesforce connections")
    s.set_defaults(fn=cmd_check)

    args = p.parse_args(argv)
    return args.fn(args)
