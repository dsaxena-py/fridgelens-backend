from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Response, status

from ..deps import Conn, User
from ..repositories import inventory
from ..schemas import InventoryCreate, InventoryItem, InventoryUpdate

router = APIRouter(prefix="/inventory", tags=["fridge"])


@router.get("", response_model=list[InventoryItem])
async def list_inventory(user: User, conn: Conn):
    """The virtual fridge, soonest-expiring first."""
    return await inventory.list_items(conn, user.id)


@router.post("", response_model=InventoryItem, status_code=status.HTTP_201_CREATED)
async def add_item(body: InventoryCreate, user: User, conn: Conn):
    """Add an ingredient by hand. If it is already in the fridge, the given fields are updated."""
    try:
        item_id = await inventory.upsert(conn, user.id, body.ingredient_id, "manual", body.quantity, body.unit,
                                         body.expires_at)
    except LookupError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    return await inventory.get_item(conn, user.id, item_id)


@router.patch("/{item_id}", response_model=InventoryItem)
async def update_item(item_id: UUID, body: InventoryUpdate, user: User, conn: Conn):
    item = await inventory.update(conn, user.id, item_id, body.model_dump(exclude_unset=True))
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    return item


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item(item_id: UUID, user: User, conn: Conn):
    if not await inventory.delete(conn, user.id, item_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "item not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
