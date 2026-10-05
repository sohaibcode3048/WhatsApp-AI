"""Alembic environment configuration for database migrations."""

from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from backend.app.core.config import settings
from backend.app.models import Base, Customer, Product, Shop, User

# Alembic Config object
config = context.config

# Setup logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Model metadata for autogenerate
target_metadata = Base.metadata


def get_sync_url() -> str:
    """Derive synchronous connection string for Alembic runner."""
    url = settings.DATABASE_SYNC_URL
    if not url:
        url = settings.DATABASE_URL
    # Normalize async dialects to sync
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    url = url.replace("sqlite+aiosqlite:///", "sqlite:///")
    return url


def run_migrations_offline() -> None:
    """Run migrations in offline mode."""
    url = get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in online mode."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_sync_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True if "sqlite" in get_sync_url() else False,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
