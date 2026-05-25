"""Alembic environment for the Slate Controller backend.

We use the synchronous SQLite driver here (alembic itself doesn't speak
asyncio). The app runs `sqlite+aiosqlite:///...` at runtime; alembic strips
the `+aiosqlite` suffix to get a plain `sqlite:///...` URL pointing at the
same file.

`target_metadata` is wired to `app.db.database.Base.metadata`, and all ORM
models are imported below so they register on it — required for autogenerate.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure `app.*` is importable when running `alembic` from backend/.
BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.config import get_settings  # noqa: E402
from app.db.database import Base  # noqa: E402
from app.db import models  # noqa: E402, F401  - registers tables on Base.metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _sync_db_url() -> str:
    """Translate the app's async URL to the sync form alembic needs.

    Resolves SQLite relative paths the same way SQLAlchemy/aiosqlite does at
    runtime: against the current working directory. Both the uvicorn process
    and `alembic` CLI run from BACKEND_DIR (`/app` in the container, the
    backend/ folder on host), so the resolved path matches what the app
    actually opens.
    """
    url = get_settings().db_url
    url = url.replace("sqlite+aiosqlite://", "sqlite://")
    prefix = "sqlite:///./"
    if url.startswith(prefix):
        relative = url[len(prefix):]
        absolute = Path(relative).resolve()
        absolute.parent.mkdir(parents=True, exist_ok=True)
        url = f"sqlite:///{absolute}"
    return url


# Inject the URL into the alembic config so engine_from_config can pick it up.
config.set_main_option("sqlalchemy.url", _sync_db_url())

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=_sync_db_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # required for SQLite ALTER TABLE
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _sync_db_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # required for SQLite ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
