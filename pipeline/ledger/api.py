"""The ledger API. Every writer (n8n, the scoring service, agents) goes through it.

Run: python -m pipeline ledger-serve   (docs at http://localhost:8800/docs)
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from pipeline.ledger.catalog import (CATALOG_VERSION, CONTENT_KINDS, DECISION_TYPES,
                                     EVENT_TYPES, SCHEMA_VERSION, STAGES, SYSTEM_TYPES)
from pipeline.ledger.model import ContentIn, ErasureIn, EventIn
from pipeline.ledger.store import LedgerError, LedgerStore, NotFound


def create_app(store: LedgerStore) -> FastAPI:
    app = FastAPI(title="Lumenvale customer ledger", version="0.1.0",
                  description="Append-only, hash-chained timeline of every customer interaction.")

    def _call(fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except LedgerError as e:
            raise HTTPException(422, str(e)) from e
        except NotFound as e:
            raise HTTPException(404, f"not found: {e}") from e

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/catalog")
    def catalog():
        return {"schema_version": SCHEMA_VERSION, "catalog_version": CATALOG_VERSION,
                "stages": STAGES, "content_kinds": CONTENT_KINDS,
                "types": {t: {"captures": what, "requires": list(req),
                              "decision": t in DECISION_TYPES, "system": t in SYSTEM_TYPES}
                          for t, (what, req) in EVENT_TYPES.items()}}

    @app.post("/events", status_code=201)
    def post_event(ev: EventIn):
        return _call(store.append, ev)

    @app.get("/events/{event_id}")
    def get_event(event_id: str):
        return _call(store.get, event_id)

    @app.get("/customers/{account_id}/timeline")
    def timeline(account_id: str, include_system: bool = False):
        events = store.timeline(account_id, include_system=include_system)
        if not events:
            raise HTTPException(404, f"no events for {account_id}")
        return {"account_id": account_id, "events": events}

    @app.get("/verify/{account_id}")
    def verify(account_id: str):
        """Check one account's hash chain. Use '_system' for pipeline-level events."""
        return store.verify(None if account_id == "_system" else account_id)

    @app.post("/content", status_code=201)
    def post_content(c: ContentIn):
        return store.put_content(c)

    @app.get("/content/{content_id}")
    def get_content(content_id: str):
        return _call(store.get_content, content_id)

    @app.get("/search")
    def search(q: str = Query(min_length=2), account_id: str | None = None,
               limit: int = Query(20, ge=1, le=100)):
        return {"q": q, "hits": store.search(q, account_id=account_id, limit=limit)}

    @app.post("/erasure")
    def erasure(req: ErasureIn):
        return _call(store.erase_person, req)

    return app


def app_from_env() -> FastAPI:
    import os

    from pipeline import config
    config.load_env()
    url = os.environ.get("LEDGER_DATABASE_URL")
    if url:
        from pipeline.ledger.pg import PostgresStore
        return create_app(PostgresStore(url))
    from pipeline.ledger.store import MemoryStore
    print("LEDGER_DATABASE_URL not set: using an in-memory ledger (lost on exit)")
    return create_app(MemoryStore())
