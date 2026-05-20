"""
Run the sequential story generation pipeline from a saved theme plan.

Usage:
    python -m scripts.generate_lessons --plan content_plan.example.json
    python -m scripts.generate_lessons --plan curriculum_plan.example.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from ai.story_generator import generate_lesson_if_quality_passes, generate_tts_for_lesson
from backend.database import SessionLocal, init_db
from backend.repositories import save_lesson
from backend.serializers import lesson_to_dict


def load_plan(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        plan = json.load(file)

    if "levels" in plan:
        required = ["book_id", "age", "protagonist", "levels"]
        missing = [key for key in required if key not in plan]
        if missing:
            raise ValueError(f"Missing required plan keys: {', '.join(missing)}")
        for batch in plan["levels"]:
            validate_batch(batch)
        return plan

    required = ["book_id", "level", "age", "protagonist", "themes"]
    missing = [key for key in required if key not in plan]
    if missing:
        raise ValueError(f"Missing required plan keys: {', '.join(missing)}")
    validate_batch(plan)
    return plan


def validate_batch(batch: dict[str, Any]) -> None:
    if batch["level"] not in [1, 2, 3]:
        raise ValueError("level must be 1, 2, or 3.")
    if not batch["themes"]:
        raise ValueError("themes must include at least one theme.")


async def maybe_save_to_database(lesson_data: dict, roleplay_scenarios: list) -> bool:
    if not SessionLocal:
        return False

    await init_db()
    async with SessionLocal() as session:
        await save_lesson(session, lesson_data, roleplay_scenarios)
    return True


async def run_plan(plan: dict[str, Any]) -> dict[str, Any]:
    if "levels" in plan:
        return await run_curriculum_plan(plan)

    return await run_single_level_plan(plan)


async def run_single_level_plan(plan: dict[str, Any]) -> dict[str, Any]:
    accepted = []
    rejected = []
    next_episode = int(plan.get("start_episode", 1))

    for index, theme in enumerate(plan["themes"], start=1):
        print(f"\n[{index}/{len(plan['themes'])}] theme={theme}")
        lesson, quality = await generate_lesson_if_quality_passes(
            book_id=plan["book_id"],
            episode=next_episode,
            level=int(plan["level"]),
            age=int(plan["age"]),
            theme=theme,
            protagonist=plan["protagonist"],
            min_score=int(plan.get("min_score", 80)),
            generate_images=bool(plan.get("generate_images", False)),
        )

        if not lesson:
            print(f"  REJECT: {quality.get('score', 0)}/100 - {quality.get('reason', '')}")
            rejected.append(quality)
            continue

        if plan.get("generate_tts", False):
            lesson = generate_tts_for_lesson(lesson)

        lesson_data = lesson_to_dict(lesson)
        lesson_data["theme"] = theme
        lesson_data["quality_score"] = quality["score"]
        lesson_data["quality_reason"] = quality.get("reason", "")

        saved_to_database = False
        if plan.get("save_to_database", True):
            saved_to_database = await maybe_save_to_database(lesson_data, lesson.roleplay_scenarios)

        accepted.append({
            "theme": theme,
            "score": quality["score"],
            "reason": quality.get("reason", ""),
            "lesson": lesson_data,
            "evaluation": quality.get("evaluation", {}),
            "saved_to_database": saved_to_database,
        })
        print(f"  PASS: {quality['score']}/100 -> {lesson.lesson_id}")
        next_episode += 1

    return {
        "book_id": plan["book_id"],
        "level": plan["level"],
        "min_score": plan.get("min_score", 80),
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted": accepted,
        "rejected": rejected,
    }


async def run_curriculum_plan(plan: dict[str, Any]) -> dict[str, Any]:
    all_accepted = []
    all_rejected = []
    level_results = []

    for batch in plan["levels"]:
        batch_plan = {
            **batch,
            "book_id": plan["book_id"],
            "age": batch.get("age", plan["age"]),
            "protagonist": batch.get("protagonist", plan["protagonist"]),
            "min_score": batch.get("min_score", plan.get("min_score", 80)),
            "generate_images": batch.get("generate_images", plan.get("generate_images", False)),
            "generate_tts": batch.get("generate_tts", plan.get("generate_tts", False)),
            "save_to_database": batch.get("save_to_database", plan.get("save_to_database", True)),
            "max_total_attempts_multiplier": batch.get(
                "max_total_attempts_multiplier",
                plan.get("max_total_attempts_multiplier", 3),
            ),
        }
        result = await run_target_level_plan(batch_plan)
        level_results.append(result)
        all_accepted.extend(result["accepted"])
        all_rejected.extend(result["rejected"])

    return {
        "book_id": plan["book_id"],
        "accepted_count": len(all_accepted),
        "rejected_count": len(all_rejected),
        "levels": level_results,
        "accepted": all_accepted,
        "rejected": all_rejected,
    }


async def run_target_level_plan(plan: dict[str, Any]) -> dict[str, Any]:
    accepted = []
    rejected = []
    target_lessons = int(plan.get("target_lessons", len(plan["themes"])))
    next_episode = int(plan.get("start_episode", 1))
    max_attempts = max(
        len(plan["themes"]),
        target_lessons * int(plan.get("max_total_attempts_multiplier", 3)),
    )

    attempt = 0
    while len(accepted) < target_lessons and attempt < max_attempts:
        theme = plan["themes"][attempt % len(plan["themes"])]
        display_index = len(accepted) + 1
        attempt += 1
        print(
            f"\n[level {plan['level']}] lesson {display_index}/{target_lessons} "
            f"attempt {attempt}/{max_attempts} theme={theme}"
        )

        lesson, quality = await generate_lesson_if_quality_passes(
            book_id=plan["book_id"],
            episode=next_episode,
            level=int(plan["level"]),
            age=int(plan["age"]),
            theme=theme,
            protagonist=plan["protagonist"],
            min_score=int(plan.get("min_score", 80)),
            generate_images=bool(plan.get("generate_images", False)),
        )

        if not lesson:
            print(f"  REJECT: {quality.get('score', 0)}/100 - {quality.get('reason', '')}")
            rejected.append(quality)
            continue

        if plan.get("generate_tts", False):
            lesson = generate_tts_for_lesson(lesson)

        lesson_data = lesson_to_dict(lesson)
        lesson_data["theme"] = theme
        lesson_data["quality_score"] = quality["score"]
        lesson_data["quality_reason"] = quality.get("reason", "")

        saved_to_database = False
        if plan.get("save_to_database", True):
            saved_to_database = await maybe_save_to_database(lesson_data, lesson.roleplay_scenarios)

        accepted.append({
            "level": plan["level"],
            "theme": theme,
            "score": quality["score"],
            "reason": quality.get("reason", ""),
            "lesson": lesson_data,
            "evaluation": quality.get("evaluation", {}),
            "saved_to_database": saved_to_database,
        })
        print(f"  PASS: {quality['score']}/100 -> {lesson.lesson_id}")
        next_episode += 1

    return {
        "book_id": plan["book_id"],
        "level": plan["level"],
        "target_lessons": target_lessons,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted": accepted,
        "rejected": rejected,
    }


def write_output(result: dict[str, Any], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False, indent=2)


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="Generate judged story lessons from a theme list.")
    parser.add_argument("--plan", default="content_plan.example.json", help="Path to a JSON content plan.")
    parser.add_argument("--output", help="Override output JSON path.")
    args = parser.parse_args()

    plan_path = Path(args.plan)
    plan = load_plan(plan_path)
    result = await run_plan(plan)

    output_path = Path(args.output or plan.get("output_path", "outputs/generated_lessons.json"))
    write_output(result, output_path)

    print("\nDone.")
    print(f"Accepted: {result['accepted_count']}")
    print(f"Rejected: {result['rejected_count']}")
    print(f"Output: {output_path}")


def main() -> None:
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
