# Intake (component 4)

A HubSpot form fill reaches the customer ledger in about a second:

```
HubSpot form -> HubSpot workflow ("Send a webhook") -> Cloudflare tunnel -> n8n webhook
  -> POST /intake/hubspot (pipeline API) -> ledger: form_submitted (+ stage_changed on the first fill)
```

n8n does the orchestration. The rules (validation, which account a person belongs to, de-duplication) live in `pipeline/intake.py`, where they are tested. Later components add nodes to the same workflow: enrichment, scoring, the Slack alert.

## What intake writes

| Event | When | Notes |
| --- | --- | --- |
| `form_submitted` | Every form fill | Email and names are stored as personal data (masked, erasable). Source links to the HubSpot contact |
| `stage_changed` anonymous to known | The person's first form fill only | Refs the form event, gives the rule as its reason |

**Which account?** `ledger_account_id` from the company if HubSpot sends it (the seeded companies have it). Otherwise the email domain (`acc_dom_<domain>`). A personal email (Gmail and so on) gets an account of one (`acc_hs_<contact id>`).

**Retries.** HubSpot retries a webhook that fails. Each form fill has an idempotency key (`hubspot:<contact>:form:<time>`), so a retry returns the first event, marked `"duplicate": true`, and writes nothing new.

**Auth.** If `INTAKE_SHARED_SECRET` is set, the request must carry it in an `X-Intake-Key` header. HubSpot sends the header; n8n passes it through.

## Run it

1. Start the services: `docker compose up -d` (Postgres and n8n), then `uv run python -m pipeline ledger-serve` in its own window.
2. Open n8n at http://localhost:5678 and create the owner account (local only).
3. Import the workflow: **Workflows > Create > ... menu > Import from file**, pick `n8n/workflows/lead-intake.json`, then switch it to **Active**.
4. Test without HubSpot: `uv run python -m pipeline intake-test` sends a fake form fill through n8n. `--direct` skips n8n, and `--n 2` sends a different fake person.
5. Start the tunnel: `docker compose --profile tunnel up -d`, then `docker compose logs tunnel` and copy the `https://<words>.trycloudflare.com` URL. Put it in `.env` as `N8N_PUBLIC_URL` and run `docker compose up -d n8n` so n8n shows the right webhook URL. The URL changes whenever the tunnel restarts, so the HubSpot webhook URL has to be updated too.

If n8n can't reach the API (connection refused in the n8n execution), run `ledger-serve --host 0.0.0.0` and allow it on private networks only when Windows Firewall asks.

## HubSpot setup (test account)

1. **Form.** Marketing > Forms > Create: "Lumenvale demo request" with email, first name, last name, job title and company.
2. **Workflow.** Automation > Workflows > Create workflow > From scratch > Contact-based.
   - Trigger: **Form submission** on that form. Turn on re-enrollment so a second fill also triggers.
   - Action: **Send a webhook**. Method POST. URL `https://<tunnel>/webhook/hubspot-intake`.
   - Authentication: API key in a header named `X-Intake-Key`, value = your `INTAKE_SHARED_SECRET`.
   - Request body: customize it and send these properties under these names: `hs_object_id` (Record ID), `email`, `firstname`, `lastname`, `jobtitle`, `company`, `recent_conversion_event_name`, `recent_conversion_date`, `hs_analytics_source`, `ledger_person_id`.
   - Turn the workflow on.
3. Fill in the form from its share link and watch the n8n execution, then the customer's timeline at `http://127.0.0.1:8800/customers/<account_id>/timeline`.
