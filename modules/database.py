"""
database.py — PostgreSQL persistence for app settings (ATTOM key, etc.)

Table: app_settings
  key        VARCHAR PRIMARY KEY
  value      TEXT NOT NULL
  updated_at TIMESTAMP
"""
from sqlalchemy import create_engine, text
from config import settings

engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)


def init_db() -> None:
    """Create the app_settings table if it doesn't exist."""
    with engine.connect() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key        VARCHAR PRIMARY KEY,
                value      TEXT    NOT NULL,
                updated_at TIMESTAMP DEFAULT NOW()
            )
        """))
        conn.commit()


def get_setting(key: str) -> str | None:
    """Return the stored value for *key*, or None if not found."""
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT value FROM app_settings WHERE key = :key"),
            {"key": key},
        ).fetchone()
        return row[0] if row else None


def upsert_setting(key: str, value: str) -> None:
    """Insert or update a key-value pair."""
    with engine.connect() as conn:
        conn.execute(text("""
            INSERT INTO app_settings (key, value, updated_at)
            VALUES (:key, :value, NOW())
            ON CONFLICT (key) DO UPDATE
                SET value      = EXCLUDED.value,
                    updated_at = NOW()
        """), {"key": key, "value": value})
        conn.commit()
