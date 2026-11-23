"""What a writer sends to POST /events, and the checks every event must pass."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from pipeline.ledger.catalog import (CONTENT_KINDS, DECISION_TYPES, EVENT_TYPES, STAGES,
                                     SYSTEM_TYPES)


class Actor(BaseModel):
    kind: Literal["human", "system", "agent"]
    id: str
    name: str | None = None
    role: str | None = None
    version: str | None = None

    @model_validator(mode="after")
    def _human_has_name_and_role(self):
        if self.kind == "human" and not (self.name and self.role):
            raise ValueError("a human actor needs a name and a role")
        return self


class Source(BaseModel):
    system: str
    record_id: str | None = None
    url: str | None = None


class Approval(BaseModel):
    required: bool
    by: str | None = None
    at: datetime | None = None
    channel: str | None = None

    @model_validator(mode="after")
    def _approved_needs_who(self):
        if self.required and not self.by:
            raise ValueError("an approved change needs 'by'")
        return self


class EventIn(BaseModel):
    type: str
    summary: str = Field(min_length=1, max_length=300)
    actor: Actor
    occurred_at: datetime | None = None
    account_id: str | None = None
    person_id: str | None = None
    stage: str | None = None
    source: Source | None = None
    reason: str | None = None
    inputs: dict[str, Any] | None = None
    approval: Approval | None = None
    refs: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    pii: list[str] = Field(default_factory=list, description="keys in data that are personal")
    attachments: list[str] = Field(default_factory=list, description="content IDs")
    idempotency_key: str | None = Field(
        None, max_length=200, description="a retry with the same key returns the first event")

    @model_validator(mode="after")
    def _check(self):
        t = self.type
        if t not in EVENT_TYPES:
            raise ValueError(f"unknown event type '{t}'; add it to the catalog first")
        if t in SYSTEM_TYPES:
            if self.account_id or self.person_id:
                raise ValueError(f"'{t}' is a pipeline-level event and takes no account")
        elif not self.account_id:
            raise ValueError(f"'{t}' needs an account_id")
        if self.stage is not None and self.stage not in STAGES:
            raise ValueError(f"unknown stage '{self.stage}'")
        missing = [k for k in EVENT_TYPES[t][1] if k not in self.data]
        if missing:
            raise ValueError(f"'{t}' data is missing: {', '.join(missing)}")
        if t in DECISION_TYPES and self.actor.kind != "human" and not (self.reason and self.inputs):
            raise ValueError(f"an automated '{t}' must give a reason and its inputs")
        if t == "correction" and not self.refs:
            raise ValueError("a correction must ref the event it corrects")
        if t == "stage_changed":
            for k in ("from_stage", "to_stage"):
                if self.data[k] not in STAGES:
                    raise ValueError(f"unknown stage '{self.data[k]}' in {k}")
        stray = [k for k in self.pii if k not in self.data]
        if stray:
            raise ValueError(f"pii names fields not in data: {', '.join(stray)}")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            self.occurred_at = self.occurred_at.replace(tzinfo=timezone.utc)
        return self


class ContentIn(BaseModel):
    kind: Literal[CONTENT_KINDS]  # type: ignore[valid-type]
    account_id: str
    person_id: str | None = None
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ErasureIn(BaseModel):
    person_id: str
    requested_by: Actor
    reason: str = "privacy request"
