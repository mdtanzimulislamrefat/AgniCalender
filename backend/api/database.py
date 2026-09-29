"""Per-request PostgreSQL connection shared by the data and auth routes."""
from typing import Annotated

import psycopg
from fastapi import Depends, HTTPException

from backend.storage.reports import connect


def get_connection():
    try:
        with connect() as connection:
            yield connection
    except RuntimeError:
        raise HTTPException(503, 'Database configuration is missing') from None


Connection = Annotated[psycopg.Connection, Depends(get_connection)]
