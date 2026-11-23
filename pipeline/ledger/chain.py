"""IDs, canonical JSON, PII digests and the per-chain SHA-256 hash.

Each event's hash covers every field except the hash itself, including the previous
event's hash, so editing any earlier event breaks every hash after it.

PII is never hashed raw. Each PII field is replaced by a salted digest before
hashing, and the digests are stored with the event. Erasing the raw values later
leaves the digests, so the chain still verifies (rule 5).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time

GENESIS = "0" * 64
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_DEV_SALT = "dev-only-salt-change-me"


def new_id(prefix: str, ms: int | None = None) -> str:
    """A ULID with a prefix: 48-bit millisecond time + 80 random bits, so IDs sort by time."""
    ms = int(time.time() * 1000) if ms is None else ms
    n = (ms << 80) | secrets.randbits(80)
    chars = []
    for _ in range(26):
        n, r = divmod(n, 32)
        chars.append(_CROCKFORD[r])
    return f"{prefix}_{''.join(reversed(chars))}"


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=str).encode()


def pii_salt() -> bytes:
    return os.environ.get("LEDGER_PII_SALT", _DEV_SALT).encode()


def pii_digest(value, salt: bytes | None = None) -> str:
    return hmac.new(salt or pii_salt(), canonical(value), hashlib.sha256).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


HASHED_FIELDS = (
    "event_id", "chain", "seq", "occurred_at", "recorded_at", "account_id", "person_id",
    "stage", "type", "actor", "source", "summary", "reason", "inputs", "approval", "refs",
    "data", "pii_digests", "attachments", "schema_version", "catalog_version",
    "idempotency_key", "prev_hash",
)


def event_hash(record: dict) -> str:
    """Hash of a stored event record. ``data`` must already have PII fields removed."""
    return hashlib.sha256(canonical({k: record.get(k) for k in HASHED_FIELDS})).hexdigest()


def verify_chain(records: list[dict], pii: dict[str, dict] | None = None) -> dict:
    """Check a chain in seq order. ``pii`` maps event_id -> its raw PII still on file.

    Returns {"ok", "events", "broken_at", "problem"}.
    """
    pii = pii or {}
    prev = GENESIS
    for i, rec in enumerate(records):
        problem = None
        if rec["seq"] != i + 1:
            problem = f"sequence gap: expected {i + 1}, found {rec['seq']}"
        elif rec["prev_hash"] != prev:
            problem = "prev_hash does not match the event before it"
        elif event_hash(rec) != rec["hash"]:
            problem = "hash does not match the event's contents"
        else:
            for field, value in pii.get(rec["event_id"], {}).items():
                if pii_digest(value) != rec["pii_digests"].get(field):
                    problem = f"personal field '{field}' was changed"
        if problem:
            return {"ok": False, "events": len(records), "broken_at": rec["event_id"],
                    "problem": problem}
        prev = rec["hash"]
    return {"ok": True, "events": len(records), "broken_at": None, "problem": None}
