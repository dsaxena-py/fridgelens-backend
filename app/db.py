"""Postgres connection pool.

The backend connects with the database owner role, which bypasses Row Level Security, so every
query on a user-owned table must filter on the caller's user id. All such queries live in
app/repositories/, and every one of them takes `user_id` explicitly.

Connections run in autocommit mode: a single statement commits on its own, and anything that
must succeed or fail as a unit wraps its statements in `async with conn.transaction():`. That
way a write is committed before the response is sent.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(url: str, min_size: int, max_size: int) -> AsyncConnectionPool:
    # prepare_threshold=None: Supabase's transaction pooler (port 6543) does not support prepared statements.
    return AsyncConnectionPool(url, min_size=min_size, max_size=max_size, open=False,
                               kwargs={"row_factory": dict_row, "prepare_threshold": None, "autocommit": True})


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    pool: AsyncConnectionPool = request.app.state.pool
    async with pool.connection() as conn:
        yield conn
