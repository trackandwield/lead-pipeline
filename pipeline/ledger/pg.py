"""Postgres backend. Run Postgres with docker compose (see docker-compose.yml).

Events and content metadata are append-only: database triggers refuse UPDATE,
DELETE and TRUNCATE on them. Raw PII and content bodies live in side tables, which
are the only rows an erasure may delete.
"""
from __future__ import annotations

import threading

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from pipeline.ledger import chain as ch
from pipeline.ledger.store import LedgerStore

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_events (
    event_id    text PRIMARY KEY,
    chain       text NOT NULL,
    seq         integer NOT NULL,
    account_id  text,
    person_id   text,
    type        text NOT NULL,
    occurred_at timestamptz NOT NULL,
    summary     text NOT NULL,
    record      jsonb NOT NULL,
    hash        text NOT NULL,
    idempotency_key text UNIQUE,
    summary_tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', summary)) STORED,
    UNIQUE (chain, seq)
);
CREATE INDEX IF NOT EXISTS ledger_events_account ON ledger_events (account_id, occurred_at);
CREATE INDEX IF NOT EXISTS ledger_events_person ON ledger_events (person_id);
CREATE INDEX IF NOT EXISTS ledger_events_tsv ON ledger_events USING gin (summary_tsv);

CREATE TABLE IF NOT EXISTS ledger_event_pii (
    event_id text NOT NULL REFERENCES ledger_events (event_id),
    field    text NOT NULL,
    value    jsonb NOT NULL,
    PRIMARY KEY (event_id, field)
);

CREATE TABLE IF NOT EXISTS ledger_content (
    content_id text PRIMARY KEY,
    account_id text NOT NULL,
    person_id  text,
    kind       text NOT NULL,
    title      text NOT NULL,
    sha256     text NOT NULL,
    created_at timestamptz NOT NULL,
    record     jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_content_account ON ledger_content (account_id);

CREATE TABLE IF NOT EXISTS ledger_content_body (
    content_id text PRIMARY KEY REFERENCES ledger_content (content_id),
    body       text NOT NULL,
    body_tsv   tsvector GENERATED ALWAYS AS (to_tsvector('english', body)) STORED
);
CREATE INDEX IF NOT EXISTS ledger_content_body_tsv ON ledger_content_body USING gin (body_tsv);

CREATE OR REPLACE FUNCTION ledger_append_only() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'the customer ledger is append-only: % on % is not allowed',
        TG_OP, TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS ledger_events_append_only ON ledger_events;
CREATE TRIGGER ledger_events_append_only BEFORE UPDATE OR DELETE ON ledger_events
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
DROP TRIGGER IF EXISTS ledger_events_no_truncate ON ledger_events;
CREATE TRIGGER ledger_events_no_truncate BEFORE TRUNCATE ON ledger_events
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();
DROP TRIGGER IF EXISTS ledger_content_append_only ON ledger_content;
CREATE TRIGGER ledger_content_append_only BEFORE UPDATE OR DELETE ON ledger_content
    FOR EACH ROW EXECUTE FUNCTION ledger_append_only();
DROP TRIGGER IF EXISTS ledger_content_no_truncate ON ledger_content;
CREATE TRIGGER ledger_content_no_truncate BEFORE TRUNCATE ON ledger_content
    FOR EACH STATEMENT EXECUTE FUNCTION ledger_append_only();
"""

class PostgresStore(LedgerStore):
    def __init__(self, url: str):
        self.conn = psycopg.connect(url, row_factory=dict_row)
        self.lock = threading.Lock()  # one connection, one statement at a time
        with self.lock, self.conn.transaction():
            # Concurrent first starts would race on CREATE ... IF NOT EXISTS.
            self.conn.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_schema'))")
            self.conn.execute(SCHEMA)

    def close(self):
        self.conn.close()

    def _q(self, sql: str, params=()) -> list[dict]:
        with self.lock, self.conn.transaction():
            return self.conn.execute(sql, params).fetchall()

    def _append_locked(self, chain, build):
        with self.lock, self.conn.transaction():
            c = self.conn
            c.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (chain,))
            head = c.execute("SELECT seq, hash FROM ledger_events WHERE chain = %s "
                             "ORDER BY seq DESC LIMIT 1", (chain,)).fetchone()
            rec, pii = build(*(head["seq"], head["hash"]) if head else (0, ch.GENESIS))
            c.execute(
                "INSERT INTO ledger_events (event_id, chain, seq, account_id, person_id, type, "
                "occurred_at, summary, record, hash, idempotency_key) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (rec["event_id"], chain, rec["seq"], rec["account_id"], rec["person_id"],
                 rec["type"], rec["occurred_at"], rec["summary"], Jsonb(rec), rec["hash"],
                 rec["idempotency_key"]))
            for field, value in pii.items():
                c.execute("INSERT INTO ledger_event_pii (event_id, field, value) "
                          "VALUES (%s,%s,%s)", (rec["event_id"], field, Jsonb(value)))
            return rec

    def _records(self, *, chain=None, account_id=None, person_id=None, event_ids=None,
                 type=None):
        if event_ids is not None:
            rows = self._q("SELECT record FROM ledger_events WHERE event_id = ANY(%s)",
                           (event_ids,))
        else:
            where, params = [], []
            for col, val in (("chain", chain), ("account_id", account_id),
                             ("person_id", person_id), ("type", type)):
                if val is not None:
                    where.append(f"{col} = %s")
                    params.append(val)
            rows = self._q("SELECT record FROM ledger_events"
                           + (" WHERE " + " AND ".join(where) if where else "")
                           + " ORDER BY chain, seq", params)
        return [r["record"] for r in rows]

    def _pii(self, event_ids):
        if not event_ids:
            return {}
        out: dict[str, dict] = {}
        for r in self._q("SELECT event_id, field, value FROM ledger_event_pii "
                         "WHERE event_id = ANY(%s)", (event_ids,)):
            out.setdefault(r["event_id"], {})[r["field"]] = r["value"]
        return out

    def _by_key(self, key):
        rows = self._q("SELECT record FROM ledger_events WHERE idempotency_key = %s", (key,))
        return rows[0]["record"] if rows else None

    def _erase(self, person_id):
        with self.lock, self.conn.transaction():
            c = self.conn
            ids = [r["event_id"] for r in c.execute(
                "DELETE FROM ledger_event_pii p USING ledger_events e "
                "WHERE p.event_id = e.event_id AND e.person_id = %s RETURNING p.event_id",
                (person_id,)).fetchall()]
            bodies = c.execute(
                "DELETE FROM ledger_content_body b USING ledger_content m "
                "WHERE b.content_id = m.content_id AND m.person_id = %s", (person_id,)).rowcount
            recs = [r["record"] for r in c.execute(
                "SELECT record FROM ledger_events WHERE event_id = ANY(%s)",
                (sorted(set(ids)),)).fetchall()]
        return recs, bodies

    def _insert_content(self, record, body):
        with self.lock, self.conn.transaction():
            self.conn.execute(
                "INSERT INTO ledger_content (content_id, account_id, person_id, kind, title, "
                "sha256, created_at, record) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (record["content_id"], record["account_id"], record["person_id"], record["kind"],
                 record["title"], record["sha256"], record["created_at"], Jsonb(record)))
            self.conn.execute("INSERT INTO ledger_content_body (content_id, body) VALUES (%s,%s)",
                              (record["content_id"], body))

    def _content(self, content_ids):
        rows = self._q("SELECT m.record, b.body FROM ledger_content m LEFT JOIN "
                       "ledger_content_body b USING (content_id) WHERE m.content_id = ANY(%s)",
                       (content_ids,))
        return {r["record"]["content_id"]: dict(r["record"], body=r["body"]) for r in rows}

    def search(self, q, account_id=None, limit=20):
        rows = self._q(
            """
            SELECT * FROM (
              SELECT 'event' AS kind, event_id AS id, account_id, summary AS title,
                     occurred_at, ts_rank(summary_tsv, query) AS rank
              FROM ledger_events, websearch_to_tsquery('english', %(q)s) query
              WHERE summary_tsv @@ query AND (%(acc)s::text IS NULL OR account_id = %(acc)s)
              UNION ALL
              SELECT 'content', m.content_id, m.account_id, m.title, m.created_at,
                     ts_rank(b.body_tsv, query)
              FROM ledger_content m JOIN ledger_content_body b USING (content_id),
                   websearch_to_tsquery('english', %(q)s) query
              WHERE (b.body_tsv @@ query OR to_tsvector('english', m.title) @@ query)
                AND (%(acc)s::text IS NULL OR m.account_id = %(acc)s)
            ) hits ORDER BY rank DESC, occurred_at DESC LIMIT %(limit)s
            """, {"q": q, "acc": account_id, "limit": limit})
        return [{k: (v.isoformat() if k == "occurred_at" else v) for k, v in r.items()
                 if k != "rank"} for r in rows]
