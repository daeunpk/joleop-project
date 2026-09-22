import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from app.db.session import AsyncSessionLocal
from app.models import Book, Difficulty, RoleplayMission
from app.seed.import_ai_content import load_json, lesson_number, roleplay_mission_title

WORKSPACE_DIR = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUTS_DIR = WORKSPACE_DIR / "outputs" / "run_20260903_114105_819434"

ROLEPLAY_IMPORTS = [
    {
        "difficulty": Difficulty.BEGINNER,
        "roleplay_file": "qwen_judged_lessons_accepted_text_roleplays.json",
    },
    {
        "difficulty": Difficulty.INTERMEDIATE,
        "roleplay_file": "qwen_judged_lessons_level2_accepted_text_roleplays.json",
    },
    {
        "difficulty": Difficulty.ADVANCED,
        "roleplay_file": "qwen_judged_lessons_level3_accepted_text_roleplays.json",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Update only roleplay_missions from generated roleplay JSON files."
    )
    parser.add_argument(
        "--outputs-dir",
        default=str(DEFAULT_OUTPUTS_DIR),
        help="Directory containing qwen judged roleplay JSON files.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Commit changes. Without this flag, prints a dry-run summary and rolls back.",
    )
    parser.add_argument(
        "--difficulty",
        choices=[difficulty.value for difficulty in Difficulty],
        help="Limit update to one difficulty.",
    )
    return parser.parse_args()


def scenario_payload(scenario: dict[str, Any], *, lesson: int, index: int) -> dict[str, Any]:
    return {
        "title": roleplay_mission_title(scenario, lesson=lesson, index=index),
        "description": scenario.get("scene_description") or "",
        "character_name": scenario.get("character_name") or "Friend",
        "character_image_url": scenario.get("character_image_url"),
        "opening_message": (
            scenario.get("opening_message")
            or scenario.get("opening_line")
            or "Hi! What should we do?"
        ),
        "player_goal": scenario.get("player_goal") or scenario.get("mission_goal"),
        "model_answer": scenario.get("model_answer"),
        "similar_answers": scenario.get("similar_answers") or [],
        "hint_sequence": scenario.get("hint_sequence") or [
            hint
            for hint in (
                scenario.get("hint_1"),
                scenario.get("hint_2"),
                scenario.get("hint_3"),
            )
            if hint
        ],
        "required_turns": max(3, int(scenario.get("max_turns") or 3)),
    }


def changed_fields(mission: RoleplayMission, payload: dict[str, Any]) -> list[str]:
    changed = []
    for field, value in payload.items():
        if getattr(mission, field) != value:
            changed.append(field)
    return changed


async def main() -> None:
    args = parse_args()
    outputs_dir = Path(args.outputs_dir)
    selected_difficulty = Difficulty(args.difficulty) if args.difficulty else None
    summaries = []

    async with AsyncSessionLocal() as session:
        for item in ROLEPLAY_IMPORTS:
            if selected_difficulty and item["difficulty"] != selected_difficulty:
                continue
            lessons = load_json(outputs_dir / item["roleplay_file"])
            story_title = next((lesson.get("story_title") for lesson in lessons if lesson.get("story_title")), None)
            if story_title:
                titled_book_result = await session.execute(
                    select(Book)
                    .where(
                        Book.difficulty == item["difficulty"],
                        Book.title == story_title,
                    )
                    .order_by(Book.display_order, Book.book_id)
                )
                book = titled_book_result.scalars().first()
            else:
                book = None

            if book is None:
                book_result = await session.execute(
                    select(Book)
                    .where(Book.difficulty == item["difficulty"])
                    .order_by(Book.display_order, Book.book_id)
                )
                book = book_result.scalars().first()
            if book is None:
                raise RuntimeError(
                    f"No book found for difficulty={item['difficulty'].value}"
                )

            summary = {
                "difficulty": item["difficulty"].value,
                "bookId": book.book_id,
                "bookTitle": book.title,
                "sourceStoryTitle": story_title,
                "file": item["roleplay_file"],
                "updated": 0,
                "created": 0,
                "unchanged": 0,
                "chapters": [],
            }

            for fallback, lesson in enumerate(lessons, start=1):
                chapter_number = lesson_number(lesson, fallback)
                scenarios = lesson.get("roleplay_scenarios") or []
                existing_missions = list((
                    await session.execute(
                        select(RoleplayMission)
                        .where(
                            RoleplayMission.book_id == book.book_id,
                            RoleplayMission.chapter_number == chapter_number,
                        )
                        .order_by(RoleplayMission.mission_id)
                    )
                ).scalars().all())

                chapter_summary = {"chapter": chapter_number, "missions": []}
                for index, scenario in enumerate(scenarios, start=1):
                    payload = scenario_payload(scenario, lesson=chapter_number, index=index)
                    if index <= len(existing_missions):
                        mission = existing_missions[index - 1]
                        fields = changed_fields(mission, payload)
                        for field, value in payload.items():
                            setattr(mission, field, value)
                        action = "updated" if fields else "unchanged"
                        summary[action] += 1
                    else:
                        mission = RoleplayMission(
                            book_id=book.book_id,
                            chapter_number=chapter_number,
                            **payload,
                        )
                        session.add(mission)
                        fields = sorted(payload)
                        action = "created"
                        summary["created"] += 1

                    chapter_summary["missions"].append(
                        {
                            "index": index,
                            "action": action,
                            "missionId": mission.mission_id,
                            "title": payload["title"],
                            "changedFields": fields,
                        }
                    )
                summary["chapters"].append(chapter_summary)
            summaries.append(summary)

        await session.flush()
        if args.apply:
            await session.commit()
        else:
            await session.rollback()

    print(json.dumps({"applied": args.apply, "results": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
