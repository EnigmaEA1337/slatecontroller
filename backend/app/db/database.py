"""Async SQLAlchemy engine + session factory.

Schema migrations are handled by Alembic (see `backend/alembic/`). `init_db()`
runs `alembic upgrade head` at startup, which is idempotent: it creates tables
on a fresh install and applies any pending migrations otherwise. Existing DBs
that pre-date Alembic adoption are detected and stamped at head (no schema
delta applied) so the user's data survives the switchover.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import structlog
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import BACKEND_DIR, get_settings

logger = structlog.get_logger(__name__)


class Base(DeclarativeBase):
    """Common base for all ORM models."""


def _ensure_sqlite_dir(db_url: str) -> None:
    """Create the parent directory of a SQLite database file if needed."""
    if "sqlite" not in db_url:
        return
    parts = db_url.split(":///")
    if len(parts) != 2:
        return
    path = Path(parts[1])
    path.parent.mkdir(parents=True, exist_ok=True)


def make_engine() -> AsyncEngine:
    settings = get_settings()
    _ensure_sqlite_dir(settings.db_url)
    return create_async_engine(settings.db_url, echo=False, future=True)


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


def _classify_db_state(sync_conn: object) -> str:
    """`fresh` (no tables) / `legacy` (our tables but no alembic) / `managed`."""
    insp = inspect(sync_conn)  # type: ignore[arg-type]
    tables = set(insp.get_table_names())
    if "alembic_version" in tables:
        return "managed"
    # If any of our domain tables exist, this is a pre-Alembic database.
    if tables & {"vpn_configs", "profiles", "wifi_ssids", "networks", "app_state"}:
        return "legacy"
    return "fresh"


async def init_db(engine: AsyncEngine) -> None:
    """Bring the DB schema in line with the codebase via Alembic.

    Cases:
      - `fresh`: brand-new DB → run `alembic upgrade head`, creates all tables.
      - `legacy`: tables exist but no alembic_version → `alembic stamp head`,
        marking the DB as already at the latest revision without changing
        anything. Future migrations will then apply normally.
      - `managed`: alembic_version already exists → `alembic upgrade head`
        (idempotent; applies any pending revisions).
    """
    # Alembic is sync; import here to keep startup cheap if we ever switch.
    from alembic.config import Config

    from alembic import command

    async with engine.begin() as conn:
        state = await conn.run_sync(_classify_db_state)

    alembic_cfg = Config(str(BACKEND_DIR / "alembic.ini"))

    if state == "legacy":
        await asyncio.to_thread(command.stamp, alembic_cfg, "head")
        logger.info("db.alembic.stamped_legacy", url=get_settings().db_url)
        return

    await asyncio.to_thread(command.upgrade, alembic_cfg, "head")
    logger.info(
        "db.alembic.upgraded",
        state_before=state,
        url=get_settings().db_url,
    )


async def session_dependency(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a session, auto-closed on exit."""
    async with session_factory() as session:
        yield session
