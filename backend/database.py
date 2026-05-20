"""
Database setup.

DATABASE_URL이 설정되어 있으면 PostgreSQL을 사용한다.
예: postgresql+asyncpg://postgres:postgres@localhost:5432/lion
설정되어 있지 않으면 앱은 기존처럼 메모리 저장소로 동작한다.
"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from shared.settings import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_async_engine(DATABASE_URL, echo=False) if DATABASE_URL else None
SessionLocal = async_sessionmaker(engine, expire_on_commit=False) if engine else None


async def init_db() -> None:
    if not engine:
        return

    import backend.db_models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    if not SessionLocal:
        raise RuntimeError("DATABASE_URL is not configured.")

    async with SessionLocal() as session:
        yield session
