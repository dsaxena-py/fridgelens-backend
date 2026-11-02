from __future__ import annotations

from fastapi import APIRouter, Query

from ..deps import Conn, User
from ..repositories import ingredients
from ..schemas import Ingredient

router = APIRouter(prefix="/ingredients", tags=["ingredients"])


@router.get("", response_model=list[Ingredient])
async def search_ingredients(user: User, conn: Conn, q: str | None = Query(default=None, max_length=50),
                             detectable: bool | None = None, limit: int = Query(default=50, ge=1, le=100)):
    """Search by name or alias ("red onion", "lettuce"), e.g. for adding items the camera missed."""
    return await ingredients.search(conn, q, detectable, limit)
