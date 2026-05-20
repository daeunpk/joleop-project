from shared.models import Lesson


def lesson_to_dict(lesson: Lesson) -> dict:
    return {
        "lesson_id": lesson.lesson_id,
        "book_id": lesson.book_id,
        "level": lesson.level,
        "episode": lesson.episode,
        "pages": [
            {
                "page_number": p.page_number,
                "text": p.text,
                "image_prompt": p.image_prompt,
                "image_path": p.image_path,
                "audio_path": p.audio_path,
            }
            for p in lesson.pages
        ],
        "description_scenes": [
            {
                "scene_number": s.scene_number,
                "image_path": s.image_path,
                "desc_type": s.desc_type.value,
                "blank_word": s.blank_word,
                "answer_sentence": s.answer_sentence,
                "guide_hint": s.guide_hint,
            }
            for s in lesson.description_scenes
        ],
        "roleplay_scenarios": [
            {
                "scenario_id": r.scenario_id,
                "topic": r.topic.value,
                "level": r.level,
                "scene_description": r.scene_description,
                "character_name": r.character_name,
                "player_goal": r.player_goal,
                "hint_sequence": r.hint_sequence,
            }
            for r in lesson.roleplay_scenarios
        ],
    }
