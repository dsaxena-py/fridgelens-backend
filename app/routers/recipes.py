from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status

from ..deps import Conn, User
from ..repositories import inventory, recipes
from ..schemas import CookedOut, CookedRequest, CookingEntry, MealType, RecipeDetail, Recommendations, RecipeSummary

router = APIRouter(tags=["recipes"])


@router.get("/recommendations", response_model=Recommendations)
async def get_recommendations(user: User, conn: Conn, limit: int = Query(default=10, ge=1, le=50),
                              scan_id: UUID | None = None):
    """Recipes ranked by how much of each the fridge covers, after diet and allergen filters.

    Pass `scan_id` when asking right after a scan, so the run is linked to it for evaluation.
    """
    result = await recipes.recommend(conn, user.id, limit, scan_id)
    return {**result, "inventory_count": await inventory.count(conn, user.id)}


@router.get("/recipes", response_model=list[RecipeSummary])
async def list_recipes(user: User, conn: Conn, q: str | None = Query(default=None, max_length=50),
                       meal_type: MealType | None = None, limit: int = Query(default=20, ge=1, le=100),
                       offset: int = Query(default=0, ge=0)):
    return await recipes.list_recipes(conn, q, meal_type, limit, offset)


@router.get("/recipes/saved", response_model=list[RecipeSummary])
async def list_saved(user: User, conn: Conn):
    return await recipes.list_saved(conn, user.id)


@router.get("/recipes/history", response_model=list[CookingEntry])
async def cooking_history(user: User, conn: Conn, limit: int = Query(default=20, ge=1, le=100)):
    return await recipes.history(conn, user.id, limit)


@router.get("/recipes/{recipe_id}", response_model=RecipeDetail)
async def get_recipe(recipe_id: int, user: User, conn: Conn):
    """Full recipe, with `in_fridge` on each ingredient for the current user."""
    detail = await recipes.get_detail(conn, user.id, recipe_id)
    if detail is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recipe not found")
    return detail


async def _require_recipe(conn, recipe_id: int) -> None:
    if not await recipes.exists(conn, recipe_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "recipe not found")


@router.put("/recipes/{recipe_id}/save", status_code=status.HTTP_204_NO_CONTENT)
async def save_recipe(recipe_id: int, user: User, conn: Conn):
    await _require_recipe(conn, recipe_id)
    await recipes.save(conn, user.id, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/recipes/{recipe_id}/save", status_code=status.HTTP_204_NO_CONTENT)
async def unsave_recipe(recipe_id: int, user: User, conn: Conn):
    await recipes.unsave(conn, user.id, recipe_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/recipes/{recipe_id}/cooked", response_model=CookedOut, status_code=status.HTTP_201_CREATED)
async def cooked(recipe_id: int, body: CookedRequest, user: User, conn: Conn):
    """Log that the user cooked this; optionally take its core ingredients out of the fridge."""
    await _require_recipe(conn, recipe_id)
    async with conn.transaction():
        entry = await recipes.record_cooked(conn, user.id, recipe_id, body.rating)
        removed = []
        if body.remove_used:
            removed = await inventory.remove_ingredients(conn, user.id, await recipes.core_ingredient_ids(conn, recipe_id))
    return {**entry, "removed_from_fridge": removed}
