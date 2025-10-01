from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Optional

from ..db import models
from ..schemas import delegation as delegation_schemas

async def get_delegation(db: AsyncSession, delegation_id: int) -> Optional[models.Delegation]:
    """Gets a specific delegation by its ID."""
    return await db.get(models.Delegation, delegation_id)

async def get_delegations_by_user(db: AsyncSession, user_id: int, skip: int = 0, limit: int = 100) -> List[models.Delegation]:
    """Gets all delegations for a specific user."""
    result = await db.execute(
        select(models.Delegation)
        .where(models.Delegation.user_id == user_id)
        .offset(skip)
        .limit(limit)
    )
    return result.scalars().all()

async def get_active_delegation_by_user(db: AsyncSession, user_id: int) -> Optional[models.Delegation]:
    """Gets the currently active delegation for a user (assuming only one active at a time)."""
    result = await db.execute(
        select(models.Delegation)
        .where(models.Delegation.user_id == user_id, models.Delegation.is_active == True)
        .limit(1)
    )
    return result.scalars().first()

async def create_user_delegation(db: AsyncSession, delegation: delegation_schemas.DelegationCreate, user_id: int) -> models.Delegation:
    """Creates a new delegation for a user."""
    # Potentially deactivate existing delegations for the user first if only one active is allowed
    # existing_active = get_active_delegation_by_user(db, user_id)
    # if existing_active:
    #     existing_active.is_active = False
    #     db.add(existing_active)

    db_delegation = models.Delegation(
        **delegation.model_dump(),
        user_id=user_id
    )
    db.add(db_delegation)
    await db.commit()
    await db.refresh(db_delegation)
    return db_delegation

async def update_delegation(db: AsyncSession, db_delegation: models.Delegation, delegation_update: delegation_schemas.DelegationUpdate) -> models.Delegation:
    """Updates a delegation (e.g., sets it inactive)."""
    update_data = delegation_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_delegation, key, value)
    db.add(db_delegation)
    await db.commit()
    await db.refresh(db_delegation)
    return db_delegation

async def set_delegation_inactive(db: AsyncSession, db_delegation: models.Delegation) -> models.Delegation:
    """Helper to specifically mark a delegation as inactive."""
    db_delegation.is_active = False
    db.add(db_delegation)
    await db.commit()
    await db.refresh(db_delegation)
    return db_delegation

async def delete_delegation(db: AsyncSession, db_delegation: models.Delegation):
    """Deletes a delegation."""
    await db.delete(db_delegation)
    await db.commit() 