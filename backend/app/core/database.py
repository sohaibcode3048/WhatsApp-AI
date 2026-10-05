"""SQLAlchemy 2.x asynchronous database connection and session management."""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.core.config import settings


def get_engine() -> AsyncEngine:
    """Create async SQLAlchemy engine compatible with PostgreSQL and SQLite."""
    connect_args = {}
    if "sqlite" in settings.DATABASE_URL:
        # SQLite specific configuration
        connect_args["check_same_thread"] = False
        return create_async_engine(
            settings.DATABASE_URL,
            echo=settings.DB_ECHO,
            connect_args=connect_args,
        )
    return create_async_engine(
        settings.DATABASE_URL,
        echo=settings.DB_ECHO,
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
    )


engine: AsyncEngine = get_engine()

# Asynchronous session factory
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that yields an asynchronous database session with automatic rollback on error."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
