"""Repository for managing shops (tenants)."""

import uuid
from typing import Optional
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.shop import Shop
from backend.app.repositories.base_repository import BaseRepository


class ShopRepository(BaseRepository[Shop]):
    """Data access layer for shops."""

    def __init__(self, session: AsyncSession):
        super().__init__(Shop, session)

    async def get_by_phone(self, phone_number: str) -> Optional[Shop]:
        """Lookup shop by registered WhatsApp phone number."""
        result = await self.session.execute(
            select(Shop).where(Shop.phone_number == phone_number)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        name: str,
        phone_number: str,
        address: Optional[str] = None,
        default_language: str = "roman_urdu",
        currency: str = "PKR",
        settings: Optional[dict] = None,
    ) -> Shop:
        """Create and persist a new shop tenant."""
        shop = Shop(
            name=name,
            phone_number=phone_number,
            address=address,
            default_language=default_language,
            currency=currency,
            settings=settings or {},
        )
        self.session.add(shop)
        await self.session.flush()
        return shop
