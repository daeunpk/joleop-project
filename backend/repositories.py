"""
Persistence helpers for lesson and roleplay data.
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.db_models import LessonRecord, RoleplayScenarioRecord
from shared.models import RoleplayScenario, RoleplayTopic


async def save_lesson(
    session: AsyncSession,
    lesson_data: dict,
    scenarios: list[RoleplayScenario],
) -> None:
    lesson = LessonRecord(
        lesson_id=lesson_data["lesson_id"],
        book_id=lesson_data["book_id"],
        level=lesson_data["level"],
        episode=lesson_data["episode"],
        data=lesson_data,
    )
    await session.merge(lesson)

    for scenario in scenarios:
        record = RoleplayScenarioRecord(
            scenario_id=scenario.scenario_id,
            lesson_id=lesson_data["lesson_id"],
            level=scenario.level,
            data=roleplay_scenario_to_dict(scenario),
        )
        await session.merge(record)

    await session.commit()


async def get_lesson(session: AsyncSession, lesson_id: str) -> Optional[dict]:
    result = await session.execute(
        select(LessonRecord).where(LessonRecord.lesson_id == lesson_id)
    )
    record = result.scalar_one_or_none()
    return record.data if record else None


async def get_roleplay_scenario(
    session: AsyncSession,
    scenario_id: str,
) -> Optional[RoleplayScenario]:
    result = await session.execute(
        select(RoleplayScenarioRecord).where(
            RoleplayScenarioRecord.scenario_id == scenario_id
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        return None
    return roleplay_scenario_from_dict(record.data)


def roleplay_scenario_to_dict(scenario: RoleplayScenario) -> dict:
    return {
        "scenario_id": scenario.scenario_id,
        "topic": scenario.topic.value,
        "level": scenario.level,
        "scene_description": scenario.scene_description,
        "character_name": scenario.character_name,
        "player_goal": scenario.player_goal,
        "model_answer": scenario.model_answer,
        "hint_sequence": scenario.hint_sequence,
    }


def roleplay_scenario_from_dict(data: dict) -> RoleplayScenario:
    return RoleplayScenario(
        scenario_id=data["scenario_id"],
        topic=RoleplayTopic(data["topic"]),
        level=data["level"],
        scene_description=data["scene_description"],
        character_name=data["character_name"],
        player_goal=data["player_goal"],
        model_answer=data["model_answer"],
        hint_sequence=data.get("hint_sequence", []),
    )
