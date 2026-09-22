from types import SimpleNamespace

import pytest

from ai.roleplay import RoleplaySession, judge_answer, start_roleplay_session
from app.models import RoleplayMission
from app.seed.import_ai_content import roleplay_mission_title
from app.services.evaluation import DescriptionEvaluationService
from app.services.roleplay import AIRoleplayService, MockRoleplayService, roleplay_runtime_context
from shared.models import RoleplayScenario


def test_word_guess_description_uses_blank_word() -> None:
    result = DescriptionEvaluationService().evaluate(
        instruction="Guess the word.",
        sentence=None,
        transcript="I see a red rope.",
        blank_word="rope",
        answer_sentence="rope",
    )

    assert result == {"score": 100, "passed": True, "feedback": "Great!"}


def test_description_returns_model_answer_feedback_for_mismatch() -> None:
    result = DescriptionEvaluationService().evaluate(
        instruction="Guess the word.",
        sentence=None,
        transcript="I see a flower.",
        blank_word="rope",
        answer_sentence="rope",
    )

    assert result["passed"] is False
    assert result["feedback"] == "모범 답안을 보고 다시 말해볼까요?"


def test_roleplay_context_preserves_chapter_scene_details() -> None:
    mission = RoleplayMission(
        mission_id=1,
        book_id=1,
        title="Find the bird",
        description="Popo and friends found a lost baby bird trapped in the bush.",
        character_name="Popo",
        opening_message="Can you tell me what's wrong?",
        player_goal="The child should express concern for the baby bird's safety.",
        model_answer="It's stuck in the thorns!",
        similar_answers=[],
        hint_sequence=[],
        required_turns=3,
    )

    context = roleplay_runtime_context(mission)

    assert "lost baby bird trapped in the bush" in context["situation"]
    assert context["situation"] != "You are Popo. You are stuck. Ask Popo for help."


def test_roleplay_context_treats_character_name_as_npc() -> None:
    mission = RoleplayMission(
        mission_id=2,
        book_id=1,
        title="Ask directions",
        description="Popo's friends need his help to find their way in Sunflower Meadow.",
        character_name="Friendly Hunter",
        opening_message="",
        player_goal="Ask the friendly hunter for directions to get back to the group.",
        model_answer="Where is my friend Toto?",
        similar_answers=[],
        hint_sequence=[],
        required_turns=3,
    )

    context = roleplay_runtime_context(mission)

    assert context["ai_character"] == "Friendly Hunter"
    assert context["child_role"] == "story helper"
    assert "You are a story helper" in context["situation"]
    assert "You are Friendly Hunter" not in context["situation"]


def test_roleplay_context_prefers_chapter_context_over_generic_scene() -> None:
    mission = RoleplayMission(
        mission_id=3,
        book_id=1,
        title="Leave safely",
        description=(
            "You notice a safe side door while music fills the ballroom. "
            "Story context: Popo stood proudly in Sunflower Meadow, looking at their newly built birdhouse."
        ),
        character_name="Popo",
        opening_message="",
        player_goal="Talk about the new birdhouse with Popo.",
        model_answer="The birdhouse looks wonderful!",
        similar_answers=[],
        hint_sequence=[],
        required_turns=3,
    )

    context = roleplay_runtime_context(mission)

    assert "newly built birdhouse" in context["situation"]
    assert "ballroom" not in context["situation"].lower()


def test_roleplay_import_title_uses_story_goal_instead_of_generic_topic() -> None:
    title = roleplay_mission_title(
        {
            "topic": "self_intro",
            "player_goal": "Ask Popo if you can help him untangle his mane.",
            "scene_description": "Popo's mane is tangled with thorns.",
        },
        lesson=3,
        index=1,
    )

    assert title == "Ask Popo if you can help him untangle his mane"


def test_roleplay_judge_rejects_too_short_unrelated_response() -> None:
    scenario = RoleplayScenario(
        scenario_id="direction-1",
        topic="direction",
        level=2,
        scene_description="Popo's friends need help finding their way.",
        character_name="Friendly Hunter",
        character_personality="Kind and helpful.",
        opening_line="Hi! What can I help you with?",
        max_turns=3,
        conversation_flow=[],
        player_goal="Ask the friendly hunter for directions to get back to the group.",
        model_answer="Where is my friend Toto?",
        similar_answers=["Can you show me where my friends are?"],
        hint_sequence=[],
    )

    passed, _ = judge_answer(scenario, "Hey")

    assert passed is False


def test_roleplay_opening_uses_direction_context_without_llm() -> None:
    scenario = RoleplayScenario(
        scenario_id="direction-1",
        topic="direction",
        level=2,
        scene_description="Popo's friends need his help to find their way in Sunflower Meadow.",
        character_name="Friendly Hunter",
        character_personality="Kind and helpful.",
        opening_line="",
        max_turns=3,
        conversation_flow=[],
        player_goal="Ask the friendly hunter for directions to get back to the group.",
        model_answer="Where is my friend Toto?",
        similar_answers=[],
        hint_sequence=[],
    )

    opening = start_roleplay_session(RoleplaySession(scenario))

    assert opening == "Hello, I am Friendly Hunter. Are you looking for someone?"


@pytest.mark.asyncio
async def test_roleplay_scores_similar_answers_and_returns_success() -> None:
    mission = RoleplayMission(
        mission_id=1,
        book_id=1,
        title="Help Hana",
        description="Encourage Hana.",
        character_name="Hana",
        opening_message="Can you help me?",
        player_goal="Encourage Hana to help her friends.",
        model_answer="I can help!",
        similar_answers=["Let me help you fix the decorations!"],
        hint_sequence=["Say you can help."],
        required_turns=1,
    )

    result = await MockRoleplayService().respond(
        mission=mission,
        transcript="Let me help you fix the decorations",
        turn=1,
    )

    assert result["score"] >= 90
    assert result["text"] == "Thank you! That helps a lot."


@pytest.mark.asyncio
async def test_roleplay_returns_hint_for_unrelated_answer() -> None:
    mission = RoleplayMission(
        mission_id=1,
        book_id=1,
        title="Help Hana",
        description="Encourage Hana.",
        character_name="Hana",
        opening_message="Can you help me?",
        model_answer="I can help!",
        similar_answers=[],
        hint_sequence=["Say you can help."],
        required_turns=1,
    )

    result = await MockRoleplayService().respond(
        mission=mission,
        transcript="I want pizza.",
        turn=1,
    )

    assert result["score"] < 70
    assert result["text"] == "Say you can help."


@pytest.mark.asyncio
async def test_ai_roleplay_falls_back_when_llm_returns_empty_text(monkeypatch: pytest.MonkeyPatch) -> None:
    mission = RoleplayMission(
        mission_id=1,
        book_id=1,
        title="Help Hana",
        description="Encourage Hana.",
        character_name="Hana",
        opening_message="Can you help me?",
        model_answer="I can help!",
        similar_answers=[],
        hint_sequence=["Say you can help."],
        required_turns=1,
    )
    service = AIRoleplayService(session=SimpleNamespace())

    async def restore_session(*, mission, session_id, history=None):
        return SimpleNamespace(goal_achieved=False, completed=False)

    monkeypatch.setattr(service, "_restore_session", restore_session)
    monkeypatch.setattr(
        "app.services.roleplay.process_roleplay_text_turn",
        lambda session, transcript: SimpleNamespace(ai_response=" ", hint_given=False),
    )

    result = await service.respond(
        mission=mission,
        session_id=10,
        transcript="I want pizza.",
        turn=1,
    )

    assert result["source"] == "fallback"
    assert result["text"] == "Say you can help."
