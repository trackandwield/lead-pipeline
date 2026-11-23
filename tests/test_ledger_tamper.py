"""Any edit after the fact is detected, and Postgres refuses edits outright."""
import pytest

from tests.test_ledger import SDR, form, post, scored


def test_memory_tamper_is_detected(api, store):
    if type(store).__name__ != "MemoryStore":
        pytest.skip("memory backend only")
    first = post(api, form())
    post(api, scored())
    store.events[first["event_id"]]["summary"] = "Something else"
    r = api.get("/verify/acc_00001").json()
    assert not r["ok"] and r["broken_at"] == first["event_id"]


def test_changed_pii_is_detected(api, store):
    if type(store).__name__ != "MemoryStore":
        pytest.skip("memory backend only")
    ev = post(api, form())
    store.pii[ev["event_id"]]["email"] = "someone.else@example.com"
    assert "personal field" in api.get("/verify/acc_00001").json()["problem"]


def test_changed_content_is_detected(api, store):
    if type(store).__name__ != "MemoryStore":
        pytest.skip("memory backend only")
    cid = api.post("/content", json={"kind": "email", "account_id": "acc_00001",
                                     "title": "Pricing", "body": "20% off"}).json()["content_id"]
    post(api, {"type": "email_sent", "account_id": "acc_00001", "actor": SDR,
               "summary": "Sent pricing", "data": {"thread_id": "t1", "subject": "Pricing"},
               "attachments": [cid]})
    store.bodies[cid] = "50% off"
    assert "content" in api.get("/verify/acc_00001").json()["problem"]


def test_postgres_refuses_edits_and_detects_superuser_tamper(api, store):
    if type(store).__name__ != "PostgresStore":
        pytest.skip("postgres backend only")
    import psycopg
    first = post(api, form())
    post(api, scored())
    c = store.conn
    for sql in ("UPDATE ledger_events SET summary = 'x'", "DELETE FROM ledger_events",
                "TRUNCATE ledger_events CASCADE"):
        with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
            with c.transaction():
                c.execute(sql)
    # Someone with superuser rights turns the guard off and edits the stored record.
    with c.transaction():
        c.execute("ALTER TABLE ledger_events DISABLE TRIGGER ledger_events_append_only")
        c.execute("UPDATE ledger_events SET record = jsonb_set(record, '{summary}', "
                  "'\"edited\"') WHERE event_id = %s", (first["event_id"],))
        c.execute("ALTER TABLE ledger_events ENABLE TRIGGER ledger_events_append_only")
    r = api.get("/verify/acc_00001").json()
    assert not r["ok"] and r["broken_at"] == first["event_id"]
