"""Migration runner script — MODULE 26.

Runs Alembic migrations programmatically.  Useful for Docker entrypoints
and CI/CD pipelines that cannot run the `alembic` CLI directly.

Usage:
    python scripts/run_migrations.py           # upgrade to head
    python scripts/run_migrations.py downgrade  # downgrade one revision
    python scripts/run_migrations.py current    # show current revision
    python scripts/run_migrations.py history    # show migration history
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alembic.config import Config
from alembic import command


def _get_alembic_config() -> Config:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(root, "alembic"))

    # Override the URL from env if provided
    db_url = os.environ.get("DATABASE_SYNC_URL") or os.environ.get("DATABASE_URL", "")
    # Convert async URL to sync for Alembic
    sync_url = (
        db_url
        .replace("postgresql+asyncpg://", "postgresql+psycopg2://")
        .replace("sqlite+aiosqlite://", "sqlite://")
    )
    if sync_url:
        cfg.set_main_option("sqlalchemy.url", sync_url)

    return cfg


def upgrade_head() -> None:
    print("Running: alembic upgrade head")
    cfg = _get_alembic_config()
    command.upgrade(cfg, "head")
    print("Migrations applied successfully.")


def downgrade_one() -> None:
    print("Running: alembic downgrade -1")
    cfg = _get_alembic_config()
    command.downgrade(cfg, "-1")
    print("Downgrade complete.")


def show_current() -> None:
    cfg = _get_alembic_config()
    command.current(cfg)


def show_history() -> None:
    cfg = _get_alembic_config()
    command.history(cfg, verbose=True)


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "upgrade"

    actions = {
        "upgrade": upgrade_head,
        "downgrade": downgrade_one,
        "current": show_current,
        "history": show_history,
    }

    fn = actions.get(action)
    if fn is None:
        print(f"Unknown action: '{action}'. Valid: {list(actions)}")
        sys.exit(1)

    fn()


if __name__ == "__main__":
    main()
