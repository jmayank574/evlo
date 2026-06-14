"""Declarative base and shared column types.

Types are chosen to be Postgres-native in production (JSONB, native UUID) while
still creating cleanly on SQLite for fast model-shape tests:
  * ``JSONBType`` renders JSONB on Postgres, JSON elsewhere.
  * ``sqlalchemy.Uuid`` renders native uuid on Postgres, CHAR(32) elsewhere.
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# JSONB on Postgres, JSON fallback on other dialects (tests).
JSONBType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass
