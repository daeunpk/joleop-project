import pytest

from app.models import RoleplayMission
from app.services.roleplay import MockRoleplayService, roleplay_runtime_context


def test_roleplay_runtime_context_makes_bird_mission_child_facing() -> None:
    mission = RoleplayMission(
        mission_id=1,
        book_id=1,
        chapter_number=1,
        title="Self intro",
        description="Popo is helping a trapped bird in Sunflower Meadow.",
        character_name="Popo the lion",
        opening_message="I heard a faint chirping sound. Can you help me find it?",
        player_goal="Encourage the child to express empathy and concern for the trapped bird.",
        model_answer="I want to help the little bird!",
        similar_answers=[],
        hint_sequence=[],
        required_turns=3,
    )

    context = roleplay_runtime_context(mission)

    assert context["player_goal"] == "Tell Popo you want to help the little bird."
    assert context["opening_message"] == (
        "I hear a tiny chirp near the bush. Will you help me check on the little bird?"
    )
    assert "Tell Popo you want to help the little bird." in context["situation"]


def test_roleplay_runtime_context_replaces_generic_direction_opening() -> None:
    mission = RoleplayMission(
        mission_id=2,
        book_id=1,
        chapter_number=1,
        title="Direction",
        description="Popo's friends need his help to find their way in Sunflower Meadow.",
        character_name="Friendly Hunter",
        opening_message="Hi! What should we do?",
        player_goal="Ask the friendly hunter for directions to get back to the group.",
        model_answer="Where is my friend Toto?",
        similar_answers=[],
        hint_sequence=[],
        required_turns=3,
    )

    context = roleplay_runtime_context(mission)

    assert context["opening_message"] == "Hello, little helper. Who are you looking for?"


def test_roleplay_runtime_context_replaces_generic_stuck_chair_opening() -> None:
    mission = RoleplayMission(
        mission_id=3,
        book_id=1,
        chapter_number=1,
        title="Escape",
        description="Popo's friends are trapped in a ballroom, and they need to escape safely.",
        character_name="Toto",
        opening_message="Hi! What should we do?",
        player_goal="Ask for help from Popo when you get stuck behind a chair.",
        model_answer="Popo! I'm stuck behind this chair, can you help me?",
        similar_answers=[],
        hint_sequence=[],
        required_turns=3,
    )

    context = roleplay_runtime_context(mission)

    assert context["opening_message"] == "I hear you behind the big chair. Are you stuck?"


@pytest.mark.asyncio
async def test_mock_roleplay_bird_mission_stays_on_bird_context() -> None:
    mission = RoleplayMission(
        mission_id=1,
        book_id=1,
        chapter_number=1,
        title="Self intro",
        description="Popo is helping a trapped bird in Sunflower Meadow.",
        character_name="Popo the lion",
        opening_message="I heard a faint chirping sound. Can you help me find it?",
        player_goal="Encourage the child to express empathy and concern for the trapped bird.",
        model_answer="I want to help the little bird!",
        similar_answers=[],
        hint_sequence=[],
        required_turns=3,
    )

    result = await MockRoleplayService().respond(
        mission=mission,
        transcript="I want to help the little bird!",
        turn=1,
    )

    assert result["text"] == "Yes, let's help the little bird together."
