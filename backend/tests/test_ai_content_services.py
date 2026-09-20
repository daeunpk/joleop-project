import pytest

from ai.roleplay import RoleplaySession, judge_answer, start_roleplay_session
from app.models import RoleplayMission
from app.services.evaluation import DescriptionEvaluationService
from app.services.roleplay import MockRoleplayService, roleplay_runtime_context
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
