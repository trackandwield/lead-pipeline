"""A small client for the ledger API, used by the seeder and later by every writer."""
from __future__ import annotations

import httpx

DEFAULT_URL = "http://127.0.0.1:8800"


class LedgerClient:
    def __init__(self, base_url: str = DEFAULT_URL, client: httpx.Client | None = None):
        self.http = client or httpx.Client(base_url=base_url, timeout=30)

    def _ok(self, r: httpx.Response) -> dict:
        if r.status_code >= 400:
            raise RuntimeError(f"ledger {r.request.method} {r.request.url.path} -> "
                               f"{r.status_code}: {r.text[:400]}")
        return r.json()

    def post_event(self, event: dict) -> dict:
        return self._ok(self.http.post("/events", json=event))

    def post_content(self, content: dict) -> dict:
        return self._ok(self.http.post("/content", json=content))

    def timeline(self, account_id: str, include_system: bool = False) -> list[dict]:
        return self._ok(self.http.get(f"/customers/{account_id}/timeline",
                                      params={"include_system": include_system}))["events"]

    def verify(self, account_id: str) -> dict:
        return self._ok(self.http.get(f"/verify/{account_id}"))
