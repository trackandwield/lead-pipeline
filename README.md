# lead-pipeline

Lead pipeline across HubSpot, Salesforce and n8n: enrich, score with rules plus Claude, and route in under a minute. Sales outcomes retune the score thresholds through a closed control loop. Every step goes to an auditable customer ledger. Synthetic data, built with Claude Code.

> **Status:** early. The simulator, the HubSpot and Salesforce sandbox, the customer ledger and intake are built; the rest lands component by component.

## What it does

1. **Intake.** A HubSpot form fill triggers an n8n workflow that validates and de-duplicates the lead.
2. **Enrich and score.** Company data is added. Rules score fit and intent, and Claude judges the buyer role. Every score comes with a one-line reason.
3. **Route.** Hot leads reach Slack in under a minute. MQLs hand off to Salesforce, and outcomes sync back.
4. **Closed loop.** A weekly controller compares each tier's MQL-to-SQL rate to a target band and moves the thresholds in small, capped steps. It runs in approval mode (Slack buttons) or autonomous mode. A control chart flags drift.
5. **Customer ledger.** Every step, human or agent, writes who, what, when and why to an append-only, hash-chained timeline per customer. A context agent answers questions about any customer with citations to those events.

The seller, **Lumenvale**, is fictional, and all leads are synthetic.

## Docs

- [Scoring spec](docs/scoring-spec.md): ICP, points, tiers and the loop's setpoints (machine-readable copy: [`scoring.yaml`](scoring.yaml))
- [Customer ledger design](docs/customer-ledger.md): event schema, rules, and how other projects plug in
- [Intake](docs/intake.md): HubSpot form to n8n to the ledger, and how to set it up

## Setup

Copy `.env.example` to `.env` and fill it in. You need a HubSpot developer test account with a service key, a Salesforce Developer Edition org with an External Client App, a Slack workspace, n8n in Docker, and an Anthropic API key.

```powershell
uv sync
uv run pytest                                  # offline tests
uv run python -m pipeline check                # test the HubSpot and Salesforce connections
uv run python -m pipeline sim                  # 200 synthetic leads + outcomes into data/sim/
uv run python -m pipeline sim --drift-week 6   # same, with the bad lead source from week 6
uv run python -m pipeline hubspot-setup        # create the custom HubSpot properties
uv run python -m pipeline hubspot-seed         # load the simulated leads into HubSpot
```

### Customer ledger

Needs Docker Desktop. Set `LEDGER_DATABASE_URL`, `LEDGER_URL` and `LEDGER_PII_SALT` in `.env` (see `.env.example`).

```powershell
docker compose up -d                           # Postgres for the ledger, on port 5433
uv run python -m pipeline ledger-serve         # the ledger API; leave it running (docs: http://127.0.0.1:8800/docs)
# in a second terminal:
uv run python -m pipeline ledger-seed          # each simulated lead's first events
uv run python -m pipeline ledger-verify        # check every account's hash chain
```

### Intake (HubSpot form fill to the ledger)

`docker compose up -d` also starts n8n at http://localhost:5678. Import `n8n/workflows/lead-intake.json`, activate it, and run `uv run python -m pipeline intake-test` to send a fake form fill through it. HubSpot and Cloudflare tunnel setup: [docs/intake.md](docs/intake.md).

Open `http://127.0.0.1:8800/customers/acc_00007/timeline` to see one customer's timeline. To run the ledger tests against Postgres as well as in memory, set `LEDGER_TEST_DATABASE_URL` to a scratch database (the tests drop and recreate its tables).

## The simulator

Each lead has a hidden `true_quality` (0 to 1) that the scorer never sees, and sales outcomes are drawn from it. That makes it possible to measure how well the visible signals predict what sales does. `--drift-week` adds a `partner_list_b` source whose leads look like a good fit on paper but convert poorly: the disturbance the closed loop and the control chart must catch. Only visible fields are sent to HubSpot.

## Built with Claude Code

This repo is built with [Claude Code](https://claude.com/claude-code) as a pair. Design decisions and their reasons are kept in `docs/`.
