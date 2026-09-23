from app.models import RoleplayMission
from app.services.roleplay import roleplay_runtime_context


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
