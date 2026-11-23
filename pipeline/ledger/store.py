"""Append-only ledger storage.

``LedgerStore`` holds the rules shared by every backend: validation against existing
events and content, PII splitting, and extending the hash chain. ``MemoryStore`` is
for tests and quick demos; ``PostgresStore`` (pipeline.ledger.pg) is the real one.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pipeline.ledger import chain as ch
from pipeline.ledger.catalog import CATALOG_VERSION, SCHEMA_VERSION, SYSTEM_CHAIN
from pipeline.ledger.model import ContentIn, ErasureIn, EventIn


class LedgerError(ValueError):
    """A write the ledger refuses (unknown ref, wrong account, ...)."""


class NotFound(LookupError):
    pass


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _now() -> datetime:
    return datetime.now(timezone.utc)


class LedgerStore:
    # ---- backend hooks -------------------------------------------------------------
    def _append_locked(self, chain: str, build) -> dict:
        """Lock ``chain``, call build(last_seq, last_hash) -> (record, pii), store, return record."""
        raise NotImplementedError

    def _records(self, *, chain: str | None = None, account_id: str | None = None,
                 person_id: str | None = None, event_ids: list[str] | None = None) -> list[dict]:
        raise NotImplementedError

    def _pii(self, event_ids: list[str]) -> dict[str, dict]:
        raise NotImplementedError

    def _by_key(self, key: str) -> dict | None:
        raise NotImplementedError

    def _erase(self, person_id: str) -> tuple[list[dict], int]:
        """Delete raw PII for this person. Returns (affected records, content bodies removed)."""
        raise NotImplementedError

    def _insert_content(self, record: dict, body: str) -> None:
        raise NotImplementedError

    def _content(self, content_ids: list[str]) -> dict[str, dict]:
        """content_id -> record with 'body' (None once erased)."""
        raise NotImplementedError

    def search(self, q: str, account_id: str | None = None, limit: int = 20) -> list[dict]:
        raise NotImplementedError

    # ---- writes --------------------------------------------------------------------
    def append(self, ev: EventIn) -> dict:
        if ev.idempotency_key:
            prior = self._by_key(ev.idempotency_key)
            if prior:
                return self._view([prior])[0]
        chain = ev.account_id or SYSTEM_CHAIN
        if ev.refs:
            found = {r["event_id"] for r in self._records(event_ids=ev.refs)}
            unknown = [r for r in ev.refs if r not in found]
            if unknown:
                raise LedgerError(f"refs not in the ledger: {', '.join(unknown)}")
        attachments = []
        if ev.attachments:
            content = self._content(ev.attachments)
            for cid in ev.attachments:
                c = content.get(cid)
                if not c:
                    raise LedgerError(f"attachment {cid} is not in the content store")
                if c["account_id"] != ev.account_id:
                    raise LedgerError(f"attachment {cid} belongs to another account")
                attachments.append({"content_id": cid, "sha256": c["sha256"]})
        pii = {k: ev.data[k] for k in ev.pii}
        data = {k: v for k, v in ev.data.items() if k not in pii}
        now = _now()

        def build(last_seq: int, last_hash: str) -> tuple[dict, dict]:
            rec = {
                "event_id": ch.new_id("evt"),
                "chain": chain,
                "seq": last_seq + 1,
                "occurred_at": _iso(ev.occurred_at or now),
                "recorded_at": _iso(now),
                "account_id": ev.account_id,
                "person_id": ev.person_id,
                "stage": ev.stage,
                "type": ev.type,
                "actor": ev.actor.model_dump(exclude_none=True),
                "source": ev.source.model_dump(exclude_none=True) if ev.source else None,
                "summary": ev.summary,
                "reason": ev.reason,
                "inputs": ev.inputs,
                "approval": ev.approval.model_dump(mode="json", exclude_none=True)
                if ev.approval else None,
                "refs": ev.refs,
                "data": data,
                "pii_digests": {k: ch.pii_digest(v) for k, v in pii.items()},
                "attachments": attachments,
                "schema_version": SCHEMA_VERSION,
                "catalog_version": CATALOG_VERSION,
                "idempotency_key": ev.idempotency_key,
                "prev_hash": last_hash,
            }
            rec["hash"] = ch.event_hash(rec)
            return rec, pii

        return self._view([self._append_locked(chain, build)])[0]

    def put_content(self, c: ContentIn) -> dict:
        rec = {"content_id": ch.new_id("cnt"), "account_id": c.account_id,
               "person_id": c.person_id, "kind": c.kind, "title": c.title,
               "sha256": ch.sha256_text(c.body), "created_at": _iso(_now()),
               "metadata": c.metadata}
        self._insert_content(rec, c.body)
        return rec

    def erase_person(self, req: ErasureIn) -> dict:
        affected, bodies = self._erase(req.person_id)
        by_account: dict[str, int] = {}
        for r in affected:
            if r["account_id"]:
                by_account[r["account_id"]] = by_account.get(r["account_id"], 0) + 1
        for account, n in sorted(by_account.items()):
            self.append(EventIn(
                type="pii_erased", account_id=account, person_id=req.person_id,
                actor=req.requested_by, reason=req.reason,
                summary=f"Personal data erased for {req.person_id} on {n} events",
                data={"person_id": req.person_id, "events": n}))
        return {"person_id": req.person_id, "events": len(affected),
                "content_bodies": bodies, "accounts": sorted(by_account)}

    # ---- reads ---------------------------------------------------------------------
    def _view(self, records: list[dict]) -> list[dict]:
        """Records as readers see them: raw PII put back where it still exists."""
        pii = self._pii([r["event_id"] for r in records if r["pii_digests"]])
        out = []
        for r in records:
            v = dict(r, data=dict(r["data"]))
            held = pii.get(r["event_id"], {})
            v["data"].update(held)
            v["erased_fields"] = sorted(set(r["pii_digests"]) - set(held))
            out.append(v)
        return out

    def get(self, event_id: str) -> dict:
        recs = self._records(event_ids=[event_id])
        if not recs:
            raise NotFound(event_id)
        return self._view(recs)[0]

    def timeline(self, account_id: str, include_system: bool = False) -> list[dict]:
        recs = self._records(account_id=account_id)
        if include_system:
            recs += self._records(chain=SYSTEM_CHAIN)
        recs.sort(key=lambda r: (r["occurred_at"], r["event_id"]))
        return self._view(recs)

    def get_content(self, content_id: str) -> dict:
        c = self._content([content_id]).get(content_id)
        if not c:
            raise NotFound(content_id)
        return c

    def verify(self, account_id: str | None) -> dict:
        chain = account_id or SYSTEM_CHAIN
        recs = self._records(chain=chain)
        recs.sort(key=lambda r: r["seq"])
        result = ch.verify_chain(recs, self._pii([r["event_id"] for r in recs]))
        result["chain"] = chain
        if not result["ok"]:
            return result
        ids = [a["content_id"] for r in recs for a in r["attachments"]]
        content = self._content(ids) if ids else {}
        for r in recs:
            for a in r["attachments"]:
                body = content[a["content_id"]]["body"]
                if body is not None and ch.sha256_text(body) != a["sha256"]:
                    return dict(result, ok=False, broken_at=r["event_id"],
                                problem=f"attached content {a['content_id']} was changed")
        return result


class MemoryStore(LedgerStore):
    def __init__(self):
        self.events: dict[str, dict] = {}
        self.heads: dict[str, tuple[int, str]] = {}
        self.pii: dict[str, dict] = {}
        self.content: dict[str, dict] = {}
        self.bodies: dict[str, str] = {}

    def _append_locked(self, chain, build):
        rec, pii = build(*self.heads.get(chain, (0, ch.GENESIS)))
        self.events[rec["event_id"]] = rec
        self.heads[chain] = (rec["seq"], rec["hash"])
        if pii:
            self.pii[rec["event_id"]] = pii
        return rec

    def _records(self, *, chain=None, account_id=None, person_id=None, event_ids=None):
        if event_ids is not None:
            return [self.events[i] for i in event_ids if i in self.events]
        return [r for r in self.events.values()
                if (chain is None or r["chain"] == chain)
                and (account_id is None or r["account_id"] == account_id)
                and (person_id is None or r["person_id"] == person_id)]

    def _pii(self, event_ids):
        return {i: dict(self.pii[i]) for i in event_ids if i in self.pii}

    def _by_key(self, key):
        return next((r for r in self.events.values() if r["idempotency_key"] == key), None)

    def _erase(self, person_id):
        affected = [r for r in self._records(person_id=person_id) if r["event_id"] in self.pii]
        for r in affected:
            del self.pii[r["event_id"]]
        gone = [cid for cid, c in self.content.items()
                if c["person_id"] == person_id and cid in self.bodies]
        for cid in gone:
            del self.bodies[cid]
        return affected, len(gone)

    def _insert_content(self, record, body):
        self.content[record["content_id"]] = record
        self.bodies[record["content_id"]] = body

    def _content(self, content_ids):
        return {i: dict(self.content[i], body=self.bodies.get(i))
                for i in content_ids if i in self.content}

    def search(self, q, account_id=None, limit=20):
        words = q.lower().split()
        hits = []
        for r in self.events.values():
            if (account_id is None or r["account_id"] == account_id) and \
                    all(w in r["summary"].lower() for w in words):
                hits.append({"kind": "event", "id": r["event_id"], "account_id": r["account_id"],
                             "title": r["summary"], "occurred_at": r["occurred_at"]})
        for cid, c in self.content.items():
            text = f"{c['title']} {self.bodies.get(cid) or ''}".lower()
            if (account_id is None or c["account_id"] == account_id) and \
                    all(w in text for w in words):
                hits.append({"kind": "content", "id": cid, "account_id": c["account_id"],
                             "title": c["title"], "occurred_at": c["created_at"]})
        hits.sort(key=lambda h: h["occurred_at"], reverse=True)
        return hits[:limit]
