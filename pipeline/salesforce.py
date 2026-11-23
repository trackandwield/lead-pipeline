"""Salesforce access through the External Client App's client credentials flow.

Client credentials must go to the org's My Domain URL (SF_DOMAIN), not
login.salesforce.com.
"""
from __future__ import annotations

import httpx


class Salesforce:
    def __init__(self, domain: str, client_id: str, client_secret: str,
                 client: httpx.Client | None = None):
        self.domain = domain.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = client or httpx.Client(timeout=30)
        self.instance_url: str | None = None
        self.token: str | None = None

    def login(self) -> None:
        r = self.http.post(f"{self.domain}/services/oauth2/token", data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        if r.status_code != 200:
            raise RuntimeError(f"Salesforce login -> {r.status_code}: {r.text[:300]}")
        body = r.json()
        self.token, self.instance_url = body["access_token"], body["instance_url"]

    def get(self, path: str) -> httpx.Response:
        if not self.token:
            self.login()
        r = self.http.get(f"{self.instance_url}{path}",
                          headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code >= 400:
            raise RuntimeError(f"Salesforce GET {path} -> {r.status_code}: {r.text[:300]}")
        return r

    def latest_api_version(self) -> str:
        return self.get("/services/data/").json()[-1]["version"]

    def ping(self) -> str:
        version = self.latest_api_version()
        self.get(f"/services/data/v{version}/sobjects/Lead/describe")
        return f"ok (API v{version}, Lead object readable)"
