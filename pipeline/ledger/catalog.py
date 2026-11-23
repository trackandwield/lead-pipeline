"""The shared lifecycle stages and the versioned list of event types.

This list mirrors the event catalog in the living doc "Customer journey context:
what the ledger tracks". Types are only ever added, never changed or removed; adding
one bumps CATALOG_VERSION.
"""
from __future__ import annotations

SCHEMA_VERSION = 1
CATALOG_VERSION = 2  # 1 = design draft; 2 = + drift_alert, communications, pii_erased

STAGES = (
    "anonymous", "known", "mql", "sql", "opportunity",
    "customer_onboarding", "customer_active", "renewal", "expansion", "churned",
)

# Events about the pipeline as a whole, not one customer. They chain under this key.
SYSTEM_CHAIN = "_system"

# type -> (what it captures, data keys it must carry)
EVENT_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    # Pre-sale: lead pipeline
    "web_visit": ("Pages viewed under an anonymous ID", ("anonymous_id", "pages")),
    "form_submitted": ("Form type, page, UTM source", ("form_type",)),
    "identity_linked": ("Visitor or record joined to a person or account", ("link_from", "link_to")),
    "enriched": ("Firmographics and where each came from", ("fields",)),
    "lead_scored": ("Score, tier, subscores, threshold and model version", ("score", "tier")),
    "routed": ("Owner or queue assigned, and the rule that chose it", ("owner",)),
    "slack_alert_sent": ("Alert, channel and recipients", ("channel",)),
    "crm_synced": ("Which CRM record changed", ("system", "record_id")),
    "stage_changed": ("From and to stage, and what triggered it", ("from_stage", "to_stage")),
    "sdr_accepted": ("SDR took the lead", ()),
    "sdr_rejected": ("SDR turned it down, with a reason code", ("reason_code",)),
    "opportunity_created": ("Opportunity, amount, expected close", ("amount",)),
    "closed_won": ("Won, amount, days from first touch", ("amount",)),
    "closed_lost": ("Lost, reason, competitor if any", ("loss_reason",)),
    "threshold_changed": ("Old and new threshold and the rate that drove it", ("tier", "old", "new")),
    "drift_alert": ("Out-of-band rate on the control chart", ("tier", "rate")),
    # Communications and campaigns
    "campaign_touched": ("Campaign, channel, asset and response", ("campaign", "channel", "response")),
    "email_sent": ("Outbound email in a thread", ("thread_id", "subject")),
    "email_received": ("Inbound email in a thread", ("thread_id", "subject")),
    "message_thread": ("Slack or chat thread with the customer", ("channel",)),
    "meeting_held": ("Meeting, attendees, outcome", ("title",)),
    "commitment_made": ("A promise found in an email or meeting", ("commitment",)),
    "content_indexed": ("Content chunked and embedded for search", ("content_id", "model")),
    # Context agent and audit
    "brief_updated": ("The agent's new customer brief", ()),
    "agent_answered": ("Who asked what, the answer, the events cited", ("question",)),
    "ledger_exported": ("Who exported which timeline", ()),
    "correction": ("Corrects an earlier event", ()),
    "pii_erased": ("Personal data removed on a privacy request", ("person_id", "events")),
    # Post-sale and other projects
    "onboarding_started": ("Onboarding kicked off", ()),
    "usage_milestone": ("A product usage milestone", ("milestone",)),
    "ticket_opened": ("Support ticket", ("ticket_id",)),
    "renewal_signed": ("Renewal signed", ("amount",)),
    "churned": ("Customer left", ("churn_reason",)),
    "audience_joined": ("Joined a reverse ETL audience", ("audience",)),
    "audience_left": ("Left a reverse ETL audience", ("audience",)),
    "tco_shared": ("A rep sent a TCO calculation", ()),
}

# Decisions made by software must explain themselves (rule 2): reason + inputs.
DECISION_TYPES = frozenset({
    "identity_linked", "lead_scored", "routed", "stage_changed", "threshold_changed",
    "drift_alert", "commitment_made", "brief_updated", "agent_answered",
})

# Types that belong to the pipeline, not to a customer.
SYSTEM_TYPES = frozenset({"threshold_changed", "drift_alert"})

CONTENT_KINDS = ("email", "thread", "transcript", "asset", "note")
