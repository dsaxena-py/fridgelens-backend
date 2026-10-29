from __future__ import annotations

from datetime import date
from uuid import UUID

from psycopg import AsyncConnection

from . import ingredient, ingredient_ref

ITEM_SQL = f"""select inv.id, {ingredient('i')} as ingredient, inv.quantity, inv.unit::text as unit,
                      inv.source::text as source, inv.source_scan_id, inv.expires_at,
                      inv.expires_at - current_date as days_left, inv.added_at, inv.updated_at
               from inventory_items inv join ingredients i on i.id = inv.ingredient_id"""


async def list_items(conn: AsyncConnection, user_id: UUID) -> list[dict]:
    cur = await conn.execute(f"{ITEM_SQL} where inv.user_id = %s order by inv.expires_at nulls last, i.name",
                             (user_id,))
    return await cur.fetchall()


async def get_item(conn: AsyncConnection, user_id: UUID, item_id: UUID) -> dict | None:
    cur = await conn.execute(f"{ITEM_SQL} where inv.user_id = %s and inv.id = %s", (user_id, item_id))
    return await cur.fetchone()


async def upsert(conn: AsyncConnection, user_id: UUID, ingredient_id: int, source: str,
                 quantity: float | None = None, unit: str | None = None, expires_at: date | None = None,
                 source_scan_id: UUID | None = None) -> UUID:
    """Add an ingredient to the fridge, or refresh it if it is already there.

    The fridge holds one row per ingredient. A new row gets an expiry of today + the
    ingredient's shelf life unless one is given; an existing row keeps its expiry and quantity
    unless new values are given.
    """
    cur = await conn.execute(
        """insert into inventory_items (user_id, ingredient_id, quantity, unit, source, source_scan_id, expires_at)
           select %(user_id)s, i.id, %(quantity)s, %(unit)s::measure_unit, %(source)s::inventory_source, %(scan)s,
                  coalesce(%(expires_at)s::date, current_date + i.shelf_life_days)
           from ingredients i where i.id = %(ingredient_id)s
           on conflict (user_id, ingredient_id) do update
             set quantity = coalesce(excluded.quantity, inventory_items.quantity),
                 unit = coalesce(excluded.unit, inventory_items.unit),
                 expires_at = coalesce(%(expires_at)s::date, inventory_items.expires_at, excluded.expires_at),
                 source_scan_id = coalesce(excluded.source_scan_id, inventory_items.source_scan_id),
                 updated_at = now()
           returning id""",
        {"user_id": user_id, "ingredient_id": ingredient_id, "quantity": quantity, "unit": unit,
         "source": source, "scan": source_scan_id, "expires_at": expires_at})
    row = await cur.fetchone()
    if row is None:
        raise LookupError(f"ingredient {ingredient_id} does not exist")
    return row["id"]


async def update(conn: AsyncConnection, user_id: UUID, item_id: UUID, fields: dict) -> dict | None:
    allowed = {"quantity": "%(quantity)s", "unit": "%(unit)s::measure_unit", "expires_at": "%(expires_at)s::date"}
    sets = [f"{k} = {allowed[k]}" for k in fields if k in allowed]
    if sets:
        await conn.execute(
            f"update inventory_items set {', '.join(sets)} where user_id = %(user_id)s and id = %(id)s",
            {**fields, "user_id": user_id, "id": item_id})
    return await get_item(conn, user_id, item_id)


async def delete(conn: AsyncConnection, user_id: UUID, item_id: UUID) -> bool:
    cur = await conn.execute("delete from inventory_items where user_id = %s and id = %s", (user_id, item_id))
    return cur.rowcount > 0


async def remove_ingredients(conn: AsyncConnection, user_id: UUID, ingredient_ids: list[int]) -> list[dict]:
    cur = await conn.execute(
        f"""delete from inventory_items inv using ingredients i
            where inv.user_id = %s and inv.ingredient_id = any(%s) and i.id = inv.ingredient_id
            returning {ingredient_ref('i')} as ingredient""", (user_id, ingredient_ids))
    return [r["ingredient"] for r in await cur.fetchall()]


async def count(conn: AsyncConnection, user_id: UUID) -> int:
    cur = await conn.execute("select count(*) as n from inventory_items where user_id = %s", (user_id,))
    return (await cur.fetchone())["n"]
