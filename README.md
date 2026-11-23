# lead-pipeline

Lead pipeline across HubSpot, Salesforce and n8n: enrich, score with rules plus Claude, and route in under a minute. Sales outcomes retune the score thresholds through a closed control loop. Every step goes to an auditable customer ledger. Synthetic data, built with Claude Code.

> **Status:** early. The scoring model and the customer ledger design are drafted; code lands component by component.

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

## Run the checks

```powershell
uv sync
uv run pytest
```

## Setup

Copy `.env.example` to `.env` and fill it in. You need a HubSpot developer test account, a Salesforce Developer Edition org, a Slack workspace, n8n in Docker, and an Anthropic API key.

## Built with Claude Code

This repo is built with [Claude Code](https://claude.com/claude-code) as a pair. Design decisions and their reasons are kept in `docs/`.
