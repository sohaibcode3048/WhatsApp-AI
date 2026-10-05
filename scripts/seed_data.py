"""Database seed script — MODULE 26.

Creates a demo shop with an owner account, sample customers, and products
so the system can be tested end-to-end without manual data entry.

Usage:
    python scripts/seed_data.py

    # With a custom DATABASE_URL:
    DATABASE_URL=postgresql+asyncpg://user:pass@localhost/munshi python scripts/seed_data.py

The script is idempotent — running it twice will not create duplicate records
(it checks for existing shops by phone number before inserting).
"""

import asyncio
import sys
import os

# Make sure the project root is on the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from decimal import Decimal
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.security import hash_password
from backend.app.models.base import Base
from backend.app.models.shop import Shop
from backend.app.models.user import User, UserRole
from backend.app.models.customer import Customer
from backend.app.models.product import Product


# ---------------------------------------------------------------------------
# Seed data definitions
# ---------------------------------------------------------------------------

DEMO_SHOP = {
    "name": "Ahmed General Store",
    "phone_number": "+923001234567",
    "address": "Shop #12, Main Bazaar, Gulberg, Lahore",
    "timezone": "Asia/Karachi",
    "default_language": "roman_urdu",
    "currency": "PKR",
}

DEMO_OWNER = {
    "name": "Ahmed Sahib",
    "phone_number": "+923001234567",
    "role": UserRole.OWNER,
    "password": "Admin@12345",
}

DEMO_STAFF = {
    "name": "Zaid Staff",
    "phone_number": "+923007654321",
    "role": UserRole.STAFF,
    "password": "Staff@12345",
}

DEMO_CUSTOMERS = [
    {"name": "Ali Khan",    "phone_number": "+923009876543", "notes": "Regular customer"},
    {"name": "Sara Bibi",   "phone_number": "+923001112233", "notes": "Monthly account"},
    {"name": "Bilal Ahmed", "phone_number": "+923005554444", "notes": "Wholesale buyer"},
    {"name": "Kamran Baig", "phone_number": "+923008889999", "notes": ""},
    {"name": "Rehman Sb",   "phone_number": "+923003332211", "notes": "Nearby neighbour"},
]

DEMO_PRODUCTS = [
    {
        "name": "Coke 1.5L",      "sku": "COKE-1500", "price": Decimal("250.00"),
        "cost_price": Decimal("200.00"), "current_stock": 120, "low_stock_threshold": 20,
        "description": "Coca-Cola 1.5 litre bottle",
    },
    {
        "name": "Sprite 1.5L",    "sku": "SPRT-1500", "price": Decimal("240.00"),
        "cost_price": Decimal("195.00"), "current_stock": 80,  "low_stock_threshold": 15,
        "description": "Sprite 1.5 litre bottle",
    },
    {
        "name": "Pepsi 1.5L",     "sku": "PEPSI-1500","price": Decimal("240.00"),
        "cost_price": Decimal("195.00"), "current_stock": 60,  "low_stock_threshold": 15,
        "description": "Pepsi 1.5 litre bottle",
    },
    {
        "name": "Atta 10kg",      "sku": "ATTA-10KG", "price": Decimal("1200.00"),
        "cost_price": Decimal("1050.00"),"current_stock": 30, "low_stock_threshold": 5,
        "description": "Wheat flour 10 kilogram bag",
    },
    {
        "name": "Nestle Water 1.5L","sku":"NW-1500",  "price": Decimal("80.00"),
        "cost_price": Decimal("60.00"), "current_stock": 200,  "low_stock_threshold": 50,
        "description": "Nestle mineral water 1.5 litre",
    },
    {
        "name": "Doodh 1L",       "sku": "MILK-1L",   "price": Decimal("180.00"),
        "cost_price": Decimal("160.00"), "current_stock": 40,  "low_stock_threshold": 10,
        "description": "Fresh milk 1 litre",
    },
    {
        "name": "Cooking Oil 5L", "sku": "OIL-5L",    "price": Decimal("1800.00"),
        "cost_price": Decimal("1600.00"),"current_stock": 15, "low_stock_threshold": 3,
        "description": "Dalda cooking oil 5 litre tin",
    },
    {
        "name": "Chawal 5kg",     "sku": "RICE-5KG",  "price": Decimal("900.00"),
        "cost_price": Decimal("780.00"), "current_stock": 25,  "low_stock_threshold": 5,
        "description": "Basmati rice 5 kilogram bag",
    },
]

DEMO_SHOP_POLICIES = """
## Ahmed General Store — Policies

### Return Policy
Items can be returned within 7 days of purchase with receipt.
Perishable items (milk, fresh items) cannot be returned.
Damaged sealed products can be exchanged immediately.

### Delivery
Free delivery available within 2 km radius for orders above Rs. 500.
Delivery charges: Rs. 100 for orders under Rs. 500.
Delivery time: within 1 hour in the local area.

### Credit / Udhaar Policy
Credit is available for trusted regular customers only.
Maximum credit limit: Rs. 5,000 per customer by default.
Payment expected within 30 days.

### Working Hours
Open from 8:00 AM to 10:00 PM — 7 days a week.
Closed on major public holidays.

### Wholesale
Wholesale rates available for bulk orders (minimum 1 dozen).
Contact the owner directly for wholesale pricing.
"""


# ---------------------------------------------------------------------------
# Seeder
# ---------------------------------------------------------------------------

async def seed(session: AsyncSession) -> None:
    print("Seeding database...")

    # 1. Shop
    result = await session.execute(
        select(Shop).where(Shop.phone_number == DEMO_SHOP["phone_number"])
    )
    shop = result.scalar_one_or_none()
    if not shop:
        shop = Shop(**DEMO_SHOP)
        session.add(shop)
        await session.flush()
        print(f"  Created shop: {shop.name} (id={shop.id})")
    else:
        print(f"  Shop already exists: {shop.name} (id={shop.id})")

    # 2. Users
    for user_data in (DEMO_OWNER, DEMO_STAFF):
        result = await session.execute(
            select(User).where(
                User.shop_id == shop.id,
                User.phone_number == user_data["phone_number"],
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            user = User(
                shop_id=shop.id,
                name=user_data["name"],
                phone_number=user_data["phone_number"],
                role=user_data["role"],
                is_active=True,
                password_hash=hash_password(user_data["password"]),
            )
            session.add(user)
            print(f"  Created user: {user.name} [{user.role.value}]")
    await session.flush()

    # 3. Customers
    for cdata in DEMO_CUSTOMERS:
        result = await session.execute(
            select(Customer).where(
                Customer.shop_id == shop.id,
                Customer.phone_number == cdata["phone_number"],
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            c = Customer(shop_id=shop.id, is_active=True, **cdata)
            session.add(c)
            print(f"  Created customer: {c.name}")
    await session.flush()

    # 4. Products
    for pdata in DEMO_PRODUCTS:
        result = await session.execute(
            select(Product).where(
                Product.shop_id == shop.id,
                Product.sku == pdata["sku"],
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            p = Product(shop_id=shop.id, is_active=True, **pdata)
            session.add(p)
            print(f"  Created product: {p.name}")
    await session.flush()

    # 5. RAG — ingest shop policies
    try:
        from backend.app.rag.ingestion import rag_ingestion
        chunks = rag_ingestion.ingest_document(
            shop_id=str(shop.id),
            text=DEMO_SHOP_POLICIES,
            document_type="policy",
            source="seed_script",
        )
        print(f"  Ingested shop policies into RAG ({chunks} chunks)")
    except Exception as exc:
        print(f"  RAG ingestion skipped (ChromaDB not running?): {exc}")

    await session.commit()
    print("\nSeed complete!")
    print(f"\n  Shop ID  : {shop.id}")
    print(f"  Owner    : {DEMO_OWNER['phone_number']} / {DEMO_OWNER['password']}")
    print(f"  Staff    : {DEMO_STAFF['phone_number']} / {DEMO_STAFF['password']}")


async def main() -> None:
    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
