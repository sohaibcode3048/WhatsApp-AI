"""Base repository providing generic CRUD helpers with tenant safety."""

import uuid
from typing import Generic, List, Optional, Type, TypeVar
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.base import Base

ModelType = TypeVar("ModelType", bound=Base)


class BaseRepository(Generic[ModelType]):
    """Generic repository providing baseline queries."""

    def __init__(self, model: Type[ModelType], session: AsyncSession):
        self.model = model
        self.session = session

    async def get_by_id(self, entity_id: uuid.UUID) -> Optional[ModelType]:
        """Fetch entity by primary key."""
        result = await self.session.execute(
            select(self.model).where(self.model.id == entity_id)
        )
        return result.scalar_one_or_none()


class TenantRepository(BaseRepository[ModelType]):
    """Base repository enforcing strict shop_id tenant isolation on all queries."""

    async def get_by_id_tenant(self, entity_id: uuid.UUID, shop_id: uuid.UUID) -> Optional[ModelType]:
        """Fetch entity by primary key ensuring strict tenant ownership."""
        result = await self.session.execute(
            select(self.model).where(
                self.model.id == entity_id,
                self.model.shop_id == shop_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_tenant(
        self,
        shop_id: uuid.UUID,
        skip: int = 0,
        limit: int = 50,
        only_active: bool = True,
    ) -> List[ModelType]:
        """List entities for a specific shop with pagination."""
        query = select(self.model).where(self.model.shop_id == shop_id)
        if only_active and hasattr(self.model, "is_active"):
            query = query.where(self.model.is_active.is_(True))
        query = query.offset(skip).limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())
