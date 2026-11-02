from __future__ import annotations

from psycopg import AsyncConnection

from . import ingredient


async def search(conn: AsyncConnection, q: str | None, detectable: bool | None, limit: int) -> list[dict]:
    """Ingredients whose name or any alias contains `q`, detectable ones and prefix matches first."""
    cur = await conn.execute(
        f"""select {ingredient('i')} as item
            from ingredients i
            where (%(detectable)s::boolean is null or i.is_detectable = %(detectable)s)
              and (%(q)s::text is null
                   or i.name ilike '%%' || %(q)s || '%%'
                   or exists (select 1 from ingredient_aliases a
                              where a.ingredient_id = i.id and a.alias like '%%' || lower(%(q)s) || '%%'))
            order by (%(q)s::text is not null and lower(i.name) like lower(%(q)s) || '%%') desc,
                     i.is_detectable desc, i.name
            limit %(limit)s""",
        {"q": q or None, "detectable": detectable, "limit": limit})
    return [r["item"] for r in await cur.fetchall()]


async def existing_ids(conn: AsyncConnection, ids: set[int]) -> set[int]:
    if not ids:
        return set()
    cur = await conn.execute("select id from ingredients where id = any(%s)", (list(ids),))
    return {r["id"] for r in await cur.fetchall()}


async def ids_by_slug(conn: AsyncConnection, slugs: set[str]) -> dict[str, int]:
    cur = await conn.execute("select slug, id from ingredients where slug = any(%s)", (list(slugs),))
    return {r["slug"]: r["id"] for r in await cur.fetchall()}
