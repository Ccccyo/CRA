from types import SimpleNamespace

import psycopg
from psycopg.pq import TransactionStatus

from cra_api import store_postgres


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.autocommit = True
        self.statements = []
        self.fail_healthcheck = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

    def cursor(self):
        connection = self

        class Cursor:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def execute(self, statement):
                if statement == "SELECT 1" and connection.fail_healthcheck:
                    raise psycopg.OperationalError("idle TLS socket was closed")
                connection.statements.append(statement)

        return Cursor()

    def rollback(self):
        pass

    def close(self):
        self.closed = True


def test_connection_pool_reuses_idle_connection(monkeypatch):
    created = []

    def connect(*args, **kwargs):
        connection = FakeConnection()
        created.append(connection)
        return connection

    monkeypatch.setattr(store_postgres.psycopg, "connect", connect)
    pool = store_postgres._ConnectionPool("postgresql://example", max_size=2)

    first = pool.acquire()
    pool.release(first)
    second = pool.acquire()

    assert second is first
    assert len(created) == 1
    assert first.autocommit is False
    assert len(first.statements) == 4


def test_connection_pool_discards_half_open_idle_connection(monkeypatch):
    created = []

    def connect(*args, **kwargs):
        connection = FakeConnection()
        created.append(connection)
        return connection

    monkeypatch.setattr(store_postgres.psycopg, "connect", connect)
    pool = store_postgres._ConnectionPool("postgresql://example", max_size=1)

    stale = pool.acquire()
    pool.release(stale)
    stale.fail_healthcheck = True

    replacement = pool.acquire()

    assert replacement is not stale
    assert stale.closed is True
    assert len(created) == 2

