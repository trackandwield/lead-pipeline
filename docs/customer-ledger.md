# Customer ledger and context agent: design

Status: **v1 built** (component 3), 2026-11-23. The living doc "Customer journey context: what the ledger tracks" tracks which event types are live. Goal: a record that follows each customer from the first interaction through the whole lifecycle, which anyone at the company can audit. This repo builds it. Later portfolio projects (warehouse and attribution, customer journey agent, website analytics, journey tools) read it and add to it.

## In one paragraph

Every system that touches a customer writes a short, structured **event** to one append-only **ledger**: the form fill, the enrichment, the score and the reason for it, the routing, the Salesforce handoff, the SDR's decision, the deal, onboarding, tickets, renewal and churn. Events are never edited or deleted. A correction is a new event that points at the old one. A **context agent** reads the ledger. It keeps a short brief per customer and answers questions such as "why was this lead marked Hot?" with citations to event IDs. It also logs its own actions to the ledger. An **audit view** shows the full timeline to anyone at the company.

## Why a ledger and not CRM fields

- CRM fields hold the *current* value. The ledger holds *every change and why*, including the threshold version and model that produced a score.
- The customer's story is spread over HubSpot, Salesforce, n8n, Slack and the scoring service. The ledger is the one place it all lines up in time.
- Agents need an audit trail more than people do. When the loop moves a threshold or an agent drafts an email, the ledger shows who or what did it, the inputs, and who approved it.

## Lifecycle stages (shared by every project)

`anonymous → known → mql → sql → opportunity → customer_onboarding → customer_active → renewal → expansion | churned`, with `advocate` as a flag on an active customer. Moving between stages is itself an event (`stage_changed`), so the ledger also gives stage durations and funnel conversion.

## Event schema (v1)

| Field | Example | Notes |
| --- | --- | --- |
| `event_id` | `evt_01JD…` | ULID, so IDs sort by time |
| `occurred_at` / `recorded_at` | ISO 8601 | When it happened, and when the ledger learned of it |
| `account_id`, `person_id` | `acc_…`, `per_…` | Ledger IDs. The ID map links them to HubSpot, Salesforce and the anonymous web ID |
| `stage` | `mql` | Stage at the time of the event |
| `type` | `lead_scored` | From a fixed, versioned list (below) |
| `actor` | `{kind: agent, id: scoring-service, version: 0.3.1}` | `human`, `system` or `agent`. A human actor has a name and role |
| `source` | `{system: hubspot, record_id: 12345, url: …}` | A link back to the original record |
| `summary` | "Scored 78 (Hot): VP Eng at a 400-person fintech, demo request" | One plain-English line. This is what the audit view shows |
| `reason` | "Fit 52 + intent 26; thresholds v3 (Hot 70)" | Required for every automated decision |
| `inputs` | `{scoring_version: 3, model: …, prompt_version: …}` | What produced the decision, so it can be replayed |
| `approval` | `{required: true, by: per_…, at: …}` | For changes made in approval mode |
| `refs` | `[evt_…]` | Events this one corrects, answers or follows from |
| `data` | `{…}` | Type-specific payload, validated per type |
| `pii` | `[email, phone]` | Fields to mask in restricted views and to remove on an erasure request |
| `prev_hash`, `hash` | SHA-256 | A hash chain per account, so any edit after the fact is detectable |

**Starting event types (this repo):** `web_visit` (from project 13), `form_submitted`, `identity_linked`, `enriched`, `lead_scored`, `routed`, `slack_alert_sent`, `stage_changed`, `crm_synced`, `sdr_accepted`, `sdr_rejected`, `opportunity_created`, `closed_won`, `closed_lost`, `threshold_changed` (account-free, system level), `brief_updated`, `agent_answered`, `correction`.
**Added in catalog v2:** `drift_alert` (system level), the communications types below, and `pii_erased` (the erasure itself is logged).

Later projects add types (for example `onboarding_started`, `ticket_opened`, `usage_milestone`, `renewal_signed`, `churned`, `audience_joined`) by adding to the versioned list. No project changes an existing type.

## Communications and campaigns

Everything that touches the customer lands on the same timeline: `campaign_touched`, `email_sent`, `email_received`, `message_thread`, `meeting_held`, `commitment_made` (a promise found in an email or meeting, linked to the quote) and `content_indexed`.

The event holds a one-line summary. The full text (email body, thread, transcript, asset) goes to the **content store** (`POST /content`) and is attached to the event by ID. The event records the content's SHA-256, so the hash chain also proves the content wasn't changed.

**Search, not a separate RAG database (decision 2026-11-23).** Most questions are about one customer, and the agent pulls that customer's timeline first; for most accounts it fits in the context window. Postgres full-text search covers events and content now (`GET /search`). The database image already includes pgvector, so when a large account's content outgrows the context window, or for questions across customers, embeddings go into the same tables and `content_indexed` records which model indexed what.

## Rules

1. **Append-only.** No updates and no deletes. A correction is a `correction` event with `refs` pointing at what it corrects.
2. **Every automated decision explains itself.** `reason` and `inputs` are required for any event with an `agent` or `system` actor that makes a decision.
3. **Writers go through the API.** n8n, the scoring service and agents call `POST /events`. The API validates the schema and extends the hash chain. Nothing writes to the database directly.
4. **Identity is an event too.** Linking an anonymous visitor to a person, or two records to one account, writes `identity_linked`, and that link can be undone by a later correction.
5. **Erasure without losing the audit trail.** On a privacy request, PII fields are removed and the event skeleton stays. The hash covers a salted digest of the PII, not the raw values, so the chain still verifies afterwards.
6. **Reads are logged.** Questions put to the context agent, and exports, are logged as `agent_answered` or `ledger_exported`, so the audit trail also shows who looked.

## Parts this repo builds

| Part | What it is | Component |
| --- | --- | --- |
| Ledger store | Postgres 16 + pgvector in Docker (`docker-compose.yml`). Triggers refuse UPDATE, DELETE and TRUNCATE on events and content. Raw PII and content bodies sit in side tables, the only rows an erasure deletes. The same schema moves to Supabase for project 11 | 3 |
| Ledger API | FastAPI (`pipeline/ledger/`): `POST /events`, `GET /events/{id}`, `GET /customers/{id}/timeline`, `GET /verify/{account}`, `POST /content`, `GET /content/{id}`, `GET /search`, `POST /erasure`, `GET /catalog`. Writers can send an `idempotency_key` so retries don't duplicate events | 3 |
| Writers | Each workflow and service from component 4 on writes its events. The simulator writes post-sale events (onboarding, tickets, renewal or churn) so the demo shows a full lifecycle | 3 to 10 |
| Audit view | A web page per customer: a timeline grouped by stage, filters by actor kind and system, expandable "why" (reason + inputs), a PII mask for restricted roles, and export | 11 |
| Context agent | Claude with read-only tools (`get_timeline`, `search_events`, `get_event`). It keeps a brief per customer (stage, people, what was promised, open risks), answers questions with event citations, and says when the ledger doesn't hold the answer | 11 |
| CRM link | A "Customer ledger" link and the latest brief on the HubSpot contact and the Salesforce lead or account, so reps see it where they work | 11 |

## How we'll know it works

- Hash-chain verification passes for all 200 simulated customers, and fails on a deliberately tampered event.
- For 20 test questions with planted answers ("who approved the threshold change that made this lead Hot?"), the agent answers correctly with citations. For 5 questions the ledger can't answer, it says so.
- Someone new can explain a simulated customer's full history from the audit view in under 2 minutes.

## How other projects use it

| Project | Reads | Adds |
| --- | --- | --- |
| 13 Website behavior analytics | Nothing | `web_visit` events under an anonymous ID, linked to the person on form fill. That is the true first interaction |
| 8 Warehouse and attribution | The ledger loads into BigQuery as the touchpoints table for attribution and funnel models | `audience_joined` / `audience_left` when reverse ETL syncs |
| 11 Customer journey agent | The full pre-sale history at handoff. Its account agent grows out of the context agent | Post-sale types: onboarding, usage, tickets, goals, actions, renewal, advocacy |
| 14 Journey tools | Shared stage list and account IDs; won, lost and churn outcomes | Nothing (read-only analysis) |
| 10 TCO calculator | Account context for the rep | `tco_shared` when a rep sends a calculation |
