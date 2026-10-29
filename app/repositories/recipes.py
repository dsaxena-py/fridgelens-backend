from __future__ import annotations

import time
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from . import ingredient_ref, recipe_summary


async def recommend(conn: AsyncConnection, user_id: UUID, limit: int, scan_id: UUID | None) -> dict:
    """Rank recipes for the user's fridge (recommend_recipes) and log the run for evaluation."""
    t0 = time.perf_counter()
    cur = await conn.execute(
        f"""select rr.ord as rank, {recipe_summary()} as recipe, rr.match_score, rr.weighted_score,
                   rr.missing_count, rr.missing_ingredients
            from recommend_recipes(%s, %s) with ordinality
                 as rr(recipe_id, title, match_score, weighted_score, missing_count, missing_ingredients, ord)
            join recipes r on r.id = rr.recipe_id
            left join recipe_nutrition n on n.recipe_id = r.id
            order by rr.ord""", (user_id, limit))
    items = await cur.fetchall()
    latency_ms = round((time.perf_counter() - t0) * 1000)

    async with conn.transaction():
        cur = await conn.execute(
            """insert into recommendation_runs (user_id, scan_id, params, latency_ms)
               select %s, s.id, %s, %s
               from (select 1) one left join scans s on s.id = %s and s.user_id = %s
               returning id""",
            (user_id, Jsonb({"limit": limit, "score": "weighted_match", "alpha": 1.0}), latency_ms, scan_id, user_id))
        run_id = (await cur.fetchone())["id"]
        if items:
            async with conn.cursor() as c:
                await c.executemany(
                    """insert into recommendation_items
                         (run_id, rank, recipe_id, match_score, weighted_score, final_score, missing_count)
                       values (%s, %s, %s, %s, %s, %s, %s)""",
                    [(run_id, it["rank"], it["recipe"]["id"], it["match_score"], it["weighted_score"],
                      it["weighted_score"], it["missing_count"]) for it in items])
    return {"run_id": run_id, "items": items}


async def list_recipes(conn: AsyncConnection, q: str | None, meal_type: str | None, limit: int, offset: int) -> list[dict]:
    cur = await conn.execute(
        f"""select {recipe_summary()} as recipe
            from recipes r left join recipe_nutrition n on n.recipe_id = r.id
            where r.is_published
              and (%(meal)s::meal_type is null or r.meal_type = %(meal)s::meal_type)
              and (%(q)s::text is null or r.title ilike '%%' || %(q)s || '%%'
                   or exists (select 1 from recipe_ingredients ri join ingredients i on i.id = ri.ingredient_id
                              where ri.recipe_id = r.id and i.name ilike '%%' || %(q)s || '%%'))
            order by r.title limit %(limit)s offset %(offset)s""",
        {"q": q or None, "meal": meal_type, "limit": limit, "offset": offset})
    return [r["recipe"] for r in await cur.fetchall()]


async def get_detail(conn: AsyncConnection, user_id: UUID, recipe_id: int) -> dict | None:
    cur = await conn.execute(
        f"""select {recipe_summary()} as recipe,
                   (select jsonb_agg(jsonb_build_object(
                        'ingredient', {ingredient_ref('i')}, 'quantity', ri.quantity, 'unit', ri.unit, 'grams', ri.grams,
                        'role', ri.role, 'note', ri.note, 'is_pantry_staple', i.is_pantry_staple,
                        'in_fridge', exists (select 1 from inventory_items inv
                                             where inv.user_id = %(user_id)s and inv.ingredient_id = i.id))
                      order by array_position(array['core', 'supporting', 'garnish']::ingredient_role[], ri.role), i.name)
                    from recipe_ingredients ri join ingredients i on i.id = ri.ingredient_id
                    where ri.recipe_id = r.id) as ingredients,
                   (select jsonb_agg(s.instruction order by s.step_number) from recipe_steps s where s.recipe_id = r.id) as steps,
                   exists (select 1 from saved_recipes sr where sr.user_id = %(user_id)s and sr.recipe_id = r.id) as saved
            from recipes r left join recipe_nutrition n on n.recipe_id = r.id
            where r.id = %(id)s and r.is_published""", {"user_id": user_id, "id": recipe_id})
    row = await cur.fetchone()
    if row is None:
        return None
    return {**row["recipe"], "ingredients": row["ingredients"] or [], "steps": row["steps"] or [], "saved": row["saved"]}


async def exists(conn: AsyncConnection, recipe_id: int) -> bool:
    cur = await conn.execute("select 1 from recipes where id = %s and is_published", (recipe_id,))
    return await cur.fetchone() is not None


async def save(conn: AsyncConnection, user_id: UUID, recipe_id: int) -> None:
    await conn.execute("insert into saved_recipes (user_id, recipe_id) values (%s, %s) on conflict do nothing",
                       (user_id, recipe_id))


async def unsave(conn: AsyncConnection, user_id: UUID, recipe_id: int) -> None:
    await conn.execute("delete from saved_recipes where user_id = %s and recipe_id = %s", (user_id, recipe_id))


async def list_saved(conn: AsyncConnection, user_id: UUID) -> list[dict]:
    cur = await conn.execute(
        f"""select {recipe_summary()} as recipe
            from saved_recipes sr join recipes r on r.id = sr.recipe_id left join recipe_nutrition n on n.recipe_id = r.id
            where sr.user_id = %s order by sr.saved_at desc""", (user_id,))
    return [r["recipe"] for r in await cur.fetchall()]


async def record_cooked(conn: AsyncConnection, user_id: UUID, recipe_id: int, rating: int | None) -> dict:
    cur = await conn.execute(
        "insert into cooking_history (user_id, recipe_id, rating) values (%s, %s, %s) returning id, cooked_at",
        (user_id, recipe_id, rating))
    return await cur.fetchone()


async def core_ingredient_ids(conn: AsyncConnection, recipe_id: int) -> list[int]:
    """Core, non-staple ingredients: what cooking the recipe uses up."""
    cur = await conn.execute(
        """select ri.ingredient_id from recipe_ingredients ri join ingredients i on i.id = ri.ingredient_id
           where ri.recipe_id = %s and ri.role = 'core' and not i.is_pantry_staple""", (recipe_id,))
    return [r["ingredient_id"] for r in await cur.fetchall()]


async def history(conn: AsyncConnection, user_id: UUID, limit: int) -> list[dict]:
    cur = await conn.execute(
        f"""select ch.id, {recipe_summary()} as recipe, ch.cooked_at, ch.rating
            from cooking_history ch join recipes r on r.id = ch.recipe_id left join recipe_nutrition n on n.recipe_id = r.id
            where ch.user_id = %s order by ch.cooked_at desc limit %s""", (user_id, limit))
    return await cur.fetchall()
