# Enrichment (component 5)

Right after intake, n8n asks the pipeline API to enrich the account. The answer is one `enriched` event in the customer ledger:

```
HubSpot form -> n8n: Record in customer ledger -> Enrich the account -> Reply to HubSpot
```

| Field | Example | Source |
| --- | --- | --- |
| `company_name`, `domain` | Serrano Inc, serranoinc.example.com | provider |
| `employees` | 69 | provider |
| `employee_band` | 50-199 | derived from employees |
| `industry` | logistics | provider |
| `industry_group` | adjacent (core / adjacent / other, as in the scoring spec) | derived from industry |
| `funding_stage` | Series A | synthetic: the simulator has no funding data |
| `tech_stack` | Kubernetes, AWS or GCP | provider |

Every field is stored as `{"value": ..., "source": ...}`, so anyone reading the ledger can see where a value came from. The event also carries `provider`, `provider_version`, `match` (how the company was found), `domain`, `missing` and `cost_usd`, and refs the form fill that triggered it.

## Rules

- **Which company?** A simulated account (`acc_00042`) is looked up by its ID. Anything else by the email domain of its latest form fill, or a `domain` sent in the request.
- **Personal email.** Gmail and the like have no company to look up: the event records `missing: ["company"]` and costs nothing. Scoring treats that as missing data, not as a bad lead.
- **Unknown domain.** The simulator provider makes up data seeded by the domain (the same domain always gets the same answer) and marks every field `simulator-synthetic`. A real provider would return nothing instead, and the event would record the gap.
- **Cache.** If the account was enriched by the same provider in the last `ENRICH_CACHE_DAYS` (30) days, the API returns that event with `"cached": true` and writes and charges nothing. `"force": true` looks up again.
- **Retries.** n8n sends the form fill's event ID as `ref`; a retry with the same ref returns the first event.
- **Cost.** Each lookup records `cost_usd` (`ENRICH_COST_PER_LOOKUP`, a stand-in price until a paid provider is used). `GET /enrich/costs` adds them up: lookups, total, cost per account, and how many had no company data. That is the number behind "cost per qualified lead" in the case study.

## Providers

`pipeline/enrich.py` defines a `Provider` with a `name`, `version`, `cost_per_lookup` and `lookup()`. The only one so far is `SimulatorProvider`, which reads the companies from `data/sim/leads.jsonl` (visible fields only, never the hidden ground truth). It is free, works offline, and lets later components check scoring against what the simulator knows. A paid API (Apollo, Clearbit, Clay) is one more class and an `ENRICH_PROVIDER` value; nothing else changes.

## Run it

1. Pull, restart `ledger-serve --host 0.0.0.0`, and re-import `n8n/workflows/lead-intake.json` in n8n (it now has an **Enrich the account** node), then Publish.
2. `uv run python -m pipeline intake-test --n 3` sends a fake form fill; the timeline now ends with an `enriched` event.
3. `uv run python -m pipeline enrich-backfill` enriches all 200 simulated accounts (about $10 at the stand-in price; a second run is all cache hits).
4. `uv run python -m pipeline enrich-costs` shows the running total.
