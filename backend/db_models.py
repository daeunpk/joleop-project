"""
SQLAlchemy 테이블 모델.

초기 단계에서는 lesson 전체 payload를 JSON으로 저장한다.
필요해지면 pages, scenes, scenario를 개별 테이블로 정규화하면 된다.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class LessonRecord(Base):
    __tablename__ = "lessons"

    lesson_id: Mapped[str] = mapped_column(String, primary_key=True)
    book_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    episode: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class RoleplayScenarioRecord(Base):
    __tablename__ = "roleplay_scenarios"

    scenario_id: Mapped[str] = mapped_column(String, primary_key=True)
    lesson_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    level: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
