from types import SimpleNamespace

from psycopg.pq import TransactionStatus

from cra_api import store_postgres


class FakeConnection:
    def __init__(self):
        self.closed = False
        self.info = SimpleNamespace(transaction_status=TransactionStatus.IDLE)

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

