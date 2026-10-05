"""Repository for managing customers with multi-tenant filtering."""

import uuid
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.customer import Customer
from backend.app.repositories.base_repository import TenantRepository


class CustomerRepository(TenantRepository[Customer]):
    """Data access layer for shop customers."""

    def __init__(self, session: AsyncSession):
        super().__init__(Customer, session)

    async def get_by_phone(
        self,
        phone_number: str,
        shop_id: uuid.UUID,
    ) -> Optional[Customer]:
        """Fetch customer by phone number within the tenant."""
        result = await self.session.execute(
            select(Customer).where(
                Customer.shop_id == shop_id,
                Customer.phone_number == phone_number,
                Customer.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def find_by_name(
        self,
        name_query: str,
        shop_id: uuid.UUID,
        limit: int = 10,
    ) -> List[Customer]:
        """Search customers by name (case-insensitive substring match) scoped to tenant."""
        clean_name = f"%{name_query.strip().lower()}%"
        result = await self.session.execute(
            select(Customer).where(
                Customer.shop_id == shop_id,
                func.lower(Customer.name).like(clean_name),
                Customer.is_active.is_(True),
            ).limit(limit)
        )
        return list(result.scalars().all())

    async def create(
        self,
        shop_id: uuid.UUID,
        name: str,
        phone_number: str,
        notes: Optional[str] = None,
    ) -> Customer:
        """Create and persist a new customer for a shop."""
        customer = Customer(
            shop_id=shop_id,
            name=name.strip(),
            phone_number=phone_number.strip(),
            notes=notes,
            is_active=True,
        )
        self.session.add(customer)
        await self.session.flush()
        return customer
