"""Repository for managing shop users (owners, staff, customers)."""

import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.user import User, UserRole
from backend.app.repositories.base_repository import TenantRepository


class UserRepository(TenantRepository[User]):
    """Data access layer for users with strict tenant isolation."""

    def __init__(self, session: AsyncSession):
        super().__init__(User, session)

    async def get_by_phone_and_shop(
        self,
        phone_number: str,
        shop_id: uuid.UUID,
    ) -> Optional[User]:
        """Find a user by phone number within a specific shop context."""
        result = await self.session.execute(
            select(User).where(
                User.shop_id == shop_id,
                User.phone_number == phone_number,
                User.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        shop_id: uuid.UUID,
        name: str,
        phone_number: str,
        role: UserRole = UserRole.CUSTOMER,
    ) -> User:
        """Register a new user inside a shop."""
        user = User(
            shop_id=shop_id,
            name=name,
            phone_number=phone_number,
            role=role,
            is_active=True,
        )
        self.session.add(user)
        await self.session.flush()
        return user
