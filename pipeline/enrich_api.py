"""HTTP routes for enrichment: POST /enrich/{account_id} and GET /enrich/costs."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from pipeline.enrich import Provider, costs, enrich
from pipeline.ledger.store import LedgerError, LedgerStore, NotFound


class EnrichIn(BaseModel):
    domain: str | None = None
    ref: str | None = None  # the event that triggered it, usually the form fill
    force: bool = False     # skip the cache and look up again


def router(store: LedgerStore, provider: Provider) -> APIRouter:
    r = APIRouter(tags=["enrichment"])

    @r.get("/enrich/costs")
    def enrichment_costs():
        """Lookups, total cost and cost per account, read from the `enriched` events."""
        return costs(store)

    @r.post("/enrich/{account_id}", status_code=201)
    def enrich_account(account_id: str, body: EnrichIn | None = None):
        """Called by n8n after intake. Returns the `enriched` event, or the cached one."""
        body = body or EnrichIn()
        try:
            return enrich(store, account_id, provider, domain=body.domain, ref=body.ref,
                          force=body.force)
        except NotFound as e:
            raise HTTPException(404, str(e)) from e
        except LedgerError as e:
            raise HTTPException(422, str(e)) from e

    return r
