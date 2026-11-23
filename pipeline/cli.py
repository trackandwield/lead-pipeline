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

    report("HubSpot", hubspot, ["HUBSPOT_ACCESS_TOKEN"])
    report("Salesforce", salesforce, ["SF_DOMAIN", "SF_CLIENT_ID", "SF_CLIENT_SECRET"])
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

    s = sub.add_parser("check", help="test the HubSpot and Salesforce connections")
    s.set_defaults(fn=cmd_check)

    args = p.parse_args(argv)
    return args.fn(args)
