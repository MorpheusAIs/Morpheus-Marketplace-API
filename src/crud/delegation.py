from sqlalchemy.orm import Session
from sqlalchemy.future import select
from typing import List, Optional

from ..db import models
from ..schemas import delegation as delegation_schemas

def get_delegation(db: Session, delegation_id: int) -> Optional[models.Delegation]:
    """Gets a specific delegation by its ID."""
    return db.get(models.Delegation, delegation_id)

def get_delegations_by_user(db: Session, user_id: int, skip: int = 0, limit: int = 100) -> List[models.Delegation]:
    """Gets all delegations for a specific user."""
    return db.execute(
        select(models.Delegation)
        .where(models.Delegation.user_id == user_id)
        .offset(skip)
        .limit(limit)
    ).scalars().all()

def get_active_delegation_by_user(db: Session, user_id: int) -> Optional[models.Delegation]:
    """Gets the currently active delegation for a user (assuming only one active at a time)."""
    return db.execute(
        select(models.Delegation)
        .where(models.Delegation.user_id == user_id, models.Delegation.is_active == True)
        .limit(1)
    ).scalars().first()

def create_user_delegation(db: Session, delegation: delegation_schemas.DelegationCreate, user_id: int) -> models.Delegation:
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
    db.commit()
    db.refresh(db_delegation)
    return db_delegation

def update_delegation(db: Session, db_delegation: models.Delegation, delegation_update: delegation_schemas.DelegationUpdate) -> models.Delegation:
    """Updates a delegation (e.g., sets it inactive)."""
    update_data = delegation_update.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_delegation, key, value)
    db.add(db_delegation)
    db.commit()
    db.refresh(db_delegation)
    return db_delegation

def set_delegation_inactive(db: Session, db_delegation: models.Delegation) -> models.Delegation:
    """Helper to specifically mark a delegation as inactive."""
    db_delegation.is_active = False
    db.add(db_delegation)
    db.commit()
    db.refresh(db_delegation)
    return db_delegation

def delete_delegation(db: Session, db_delegation: models.Delegation):
    """Deletes a delegation."""
    db.delete(db_delegation)
    db.commit() 