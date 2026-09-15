"""
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy import Engine, create_engine

from app.utils.config import get_database_config


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Get a SQLAlchemy engine for the Postgres database."""
    cfg = get_database_config()
    engine = create_engine(
        cfg.url,
        pool_pre_ping=True,
        future=True,
    )
    return engine