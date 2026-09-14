"""Smoke test: verify Python can connect to PostgreSQL."""
from __future__ import annotations

import sys

from sqlalchemy import create_engine, text

from app.utils.config import get_database_config


def main() -> int:
    cfg = get_database_config()
    print(f"Connecting to {cfg.host}:{cfg.port}/{cfg.db} as {cfg.user}...")

    engine = create_engine(cfg.url, pool_pre_ping=True)

    with engine.connect() as conn:
        version = conn.execute(text("SELECT version();")).scalar_one()
        current_db = conn.execute(text("SELECT current_database();")).scalar_one()
        current_user = conn.execute(text("SELECT current_user;")).scalar_one()

    print(f"  PostgreSQL: {version}")
    print(f"  Database:   {current_db}")
    print(f"  User:       {current_user}")
    print("Connection OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())