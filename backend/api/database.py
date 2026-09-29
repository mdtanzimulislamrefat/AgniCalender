"""Per-request PostgreSQL connection shared by the data and auth routes.

The server enables a connection pool (enable_pool) so requests reuse open connections instead of
paying TCP, TLS and authentication on every call; tests and CLI tools connect per request.
"""
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from backend.storage import reports

_pool = None


def enable_pool(max_size=4):
    """Open the shared pool; the hosted database may close idle connections, so each is checked first."""
    global _pool
    _pool = ConnectionPool(
        reports.database_url(), min_size=1, max_size=max_size, name='agnicalendar', open=True,
        check=ConnectionPool.check_connection,
        kwargs={'row_factory': dict_row, 'connect_timeout': 5,
                'options': '-c statement_timeout=10000 -c timezone=UTC'})
    return _pool


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def get_connection():
    try:
        if _pool is not None:
            with _pool.connection() as connection:  # commits on success, rolls back on error
                yield connection
        else:
            with reports.connect() as connection:
                yield connection
    except RuntimeError:
        raise HTTPException(503, 'Database configuration is missing') from None


Connection = Annotated[psycopg.Connection, Depends(get_connection)]
