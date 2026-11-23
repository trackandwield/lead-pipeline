import os

import pytest
from fastapi.testclient import TestClient

from pipeline.ledger.api import create_app
from pipeline.ledger.store import MemoryStore

PG_URL = os.environ.get("LEDGER_TEST_DATABASE_URL")
BACKENDS = ["memory"] + (["postgres"] if PG_URL else [])


def _fresh_pg():
    import psycopg

    from pipeline.ledger.pg import PostgresStore
    with psycopg.connect(PG_URL, autocommit=True) as c:
        c.execute("DROP TABLE IF EXISTS ledger_event_pii, ledger_content_body, "
                  "ledger_events, ledger_content CASCADE")
    return PostgresStore(PG_URL)


@pytest.fixture(params=BACKENDS)
def store(request):
    if request.param == "memory":
        yield MemoryStore()
    else:
        s = _fresh_pg()
        yield s
        s.close()


@pytest.fixture
def api(store):
    return TestClient(create_app(store))
