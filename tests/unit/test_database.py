"""Unit tests for database connection and session management."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_database_connection(db_session: AsyncSession):
    """Verify active database connectivity and scalar execution."""
    result = await db_session.execute(text("SELECT 1"))
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_database_session_rollback(db_session: AsyncSession):
    """Verify session rollback on failure leaves clean state."""
    await db_session.execute(text("SELECT 1"))
    await db_session.rollback()
    result = await db_session.execute(text("SELECT 2"))
    assert result.scalar() == 2
