"""Repository for managing products and stock levels."""

from decimal import Decimal
import uuid
from typing import List, Optional
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.product import Product
from backend.app.repositories.base_repository import TenantRepository


class ProductRepository(TenantRepository[Product]):
    """Data access layer for shop products and inventory."""

    def __init__(self, session: AsyncSession):
        super().__init__(Product, session)

    async def get_by_sku(self, sku: str, shop_id: uuid.UUID) -> Optional[Product]:
        """Fetch product by SKU within tenant."""
        result = await self.session.execute(
            select(Product).where(
                Product.shop_id == shop_id,
                Product.sku == sku,
                Product.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def find_by_name(
        self,
        name_query: str,
        shop_id: uuid.UUID,
        limit: int = 10,
    ) -> List[Product]:
        """Search products by name substring scoped to tenant."""
        clean_name = f"%{name_query.strip().lower()}%"
        result = await self.session.execute(
            select(Product).where(
                Product.shop_id == shop_id,
                func.lower(Product.name).like(clean_name),
                Product.is_active.is_(True),
            ).limit(limit)
        )
        return list(result.scalars().all())

    async def list_low_stock(self, shop_id: uuid.UUID) -> List[Product]:
        """Retrieve all active products where current stock is at or below threshold."""
        result = await self.session.execute(
            select(Product).where(
                Product.shop_id == shop_id,
                Product.current_stock <= Product.low_stock_threshold,
                Product.is_active.is_(True),
            )
        )
        return list(result.scalars().all())

    async def get_low_stock_products(self, shop_id: uuid.UUID) -> List["Product"]:
        """Alias for list_low_stock — used by stock tools."""
        return await self.list_low_stock(shop_id)

    async def create(
        self,
        shop_id: uuid.UUID,
        name: str,
        price: Decimal,
        cost_price: Optional[Decimal] = None,
        current_stock: int = 0,
        low_stock_threshold: int = 5,
        sku: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Product:
        """Create and persist a product in the shop catalog."""
        product = Product(
            shop_id=shop_id,
            name=name.strip(),
            price=price,
            cost_price=cost_price,
            current_stock=current_stock,
            low_stock_threshold=low_stock_threshold,
            sku=sku.strip() if sku else None,
            description=description,
            is_active=True,
        )
        self.session.add(product)
        await self.session.flush()
        return product
