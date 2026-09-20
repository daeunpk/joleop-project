from difflib import SequenceMatcher
import logging
from pathlib import Path
import re
import sys
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

WORKSPACE_DIR = Path(__file__).resolve().parents[3]
if str(WORKSPACE_DIR) not in sys.path:
    sys.path.append(str(WORKSPACE_DIR))

try:
    from ai.roleplay import RoleplaySession, process_roleplay_text_turn, start_roleplay_session
except ModuleNotFoundError as exc:
    RoleplaySession = None
    process_roleplay_text_turn = None
    start_roleplay_session = None
    AI_ROLEPLAY_IMPORT_ERROR = exc
else:
    AI_ROLEPLAY_IMPORT_ERROR = None
from app.models import RoleplayMessage, RoleplayMission
from app.services.evaluation import normalize_story_names

MIN_ROLEPLAY_TURNS = 3
logger = logging.getLogger(__name__)


def roleplay_runtime_context(mission: RoleplayMission) -> dict:
    child_role = "story helper"
    model_answer = mission.model_answer or ""
    addressed_name = _addressed_character(model_answer)
    ai_character = addressed_name or mission.character_name or "your story friend"
    player_goal = mission.player_goal or mission.description
    opening_message = mission.opening_message or ""
    situation = _child_facing_situation(
        child_role=child_role,
        ai_character=ai_character,
        player_goal=player_goal or "",
        model_answer=model_answer,
        scene_description=mission.description or "",
    )
    if addressed_name and not opening_message:
        opening_message = "Hi! What can I help you with?"

    return {
        "ai_character": ai_character,
        "child_role": child_role,
        "situation": situation,
        "opening_message": opening_message or "Hi! What should we do?",
        "player_goal": player_goal,
        "required_turns": max(MIN_ROLEPLAY_TURNS, mission.required_turns or 0),
    }


def clean_roleplay_transcript(mission: RoleplayMission, transcript: str) -> str:
    context = roleplay_runtime_context(mission)
    cleaned = normalize_story_names(transcript.strip())
    for name in ("Popo", "Toto", "Pipi", "Gigi", "Momo"):
        cleaned = re.sub(rf"\b{name.lower()}\b", name, cleaned, flags=re.I)
    if context["ai_character"].lower() != "popo":
        return cleaned
    cleaned = re.sub(r"^\s*(purple|people|polo|papa|po po)\b", "Popo", cleaned, flags=re.I)
    cleaned = re.sub(r"\bpurple\b(?=[,.!?]?\s+(i|i'm|im|can|please|help)\b)", "Popo", cleaned, flags=re.I)
    return cleaned


def _addressed_character(text: str) -> str | None:
    match = re.match(r"\s*([A-Z][A-Za-z]{1,20})[!,]", text)
    if not match:
        return None
    name = match.group(1)
    if name.lower() in {"hi", "hello", "hey", "yes", "no", "okay"}:
        return None
    return name


def _child_facing_situation(
    *,
    child_role: str,
    ai_character: str,
    player_goal: str,
    model_answer: str,
    scene_description: str,
) -> str:
    clean_scene = _clean_scene_description(scene_description)
    lowered = f"{player_goal} {model_answer} {clean_scene}".lower()
    if "stuck behind" in lowered and "chair" in lowered:
        return f"You are a {child_role}. Someone is stuck behind a chair. Ask {ai_character} for help."
    if "safe side door" in lowered or "side door" in lowered:
        return "You are with your story friend in a crowded ballroom. You see a safe side door. Tell your friend how to leave safely."
    if clean_scene:
        if player_goal and player_goal.strip().lower().startswith(("ask ", "tell ", "say ", "help ")):
            return f"You are a {child_role}. {clean_scene} {player_goal.strip()}"
        if "stuck" in lowered or "trapped" in lowered or "help" in lowered:
            return f"You are a {child_role}. {clean_scene} Talk to {ai_character} and help with the story problem."
        return f"You are a {child_role}. {clean_scene}"
    return f"You are a {child_role}. Talk to {ai_character} in this story scene."


def _clean_scene_description(scene_description: str) -> str:
    scene = re.sub(r"\s*Story context:\s*.*$", "", scene_description.strip(), flags=re.I)
    return re.sub(r"\s+", " ", scene).strip()


def _closing_response(text: str, *, fallback: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return fallback

    question_index = cleaned.find("?")
    if question_index < 0:
        return cleaned

    before_question = cleaned[:question_index]
    sentence_starts = [
        before_question.rfind("."),
        before_question.rfind("!"),
    ]
    cut_at = max(sentence_starts)
    if cut_at >= 0:
        closing = before_question[:cut_at + 1].strip()
    else:
        closing = ""
    return closing or fallback


class RoleplayService:
    async def respond(
        self,
        *,
        mission: RoleplayMission,
        session_id: int = 0,
        transcript: str,
        turn: int,
        history: list[dict] | None = None,
    ) -> dict:
        raise NotImplementedError


class AIRoleplayService(RoleplayService):
    def __init__(self, *, session: AsyncSession) -> None:
        self.session = session

    async def respond(
        self,
        *,
        mission: RoleplayMission,
        session_id: int = 0,
        transcript: str,
        turn: int,
        history: list[dict] | None = None,
    ) -> dict:
        transcript = transcript.strip()
        context = roleplay_runtime_context(mission)
        if not transcript:
            hints = mission.hint_sequence or ["Can you say it one more time?"]
            return {
                "speaker": context["ai_character"].upper(),
                "text": hints[min(max(turn - 1, 0), len(hints) - 1)],
                "score": 0,
            }

        try:
            ai_session = await self._restore_session(mission=mission, session_id=session_id, history=history)
            result = process_roleplay_text_turn(ai_session, transcript)
        except Exception as exc:
            logger.warning(
                "Roleplay LLM failed; using fallback response. mission_id=%s session_id=%s turn=%s error=%s",
                mission.mission_id,
                session_id,
                turn,
                exc,
            )
            return await MockRoleplayService().respond(
                mission=mission,
                session_id=session_id,
                transcript=transcript,
                turn=turn,
            )

        score = 90 if ai_session.goal_achieved else 55
        if result.hint_given:
            score = min(score, 65)
        if ai_session.completed and not ai_session.goal_achieved:
            score = min(score, 45)
        character_text = result.ai_response
        if turn >= context["required_turns"]:
            character_text = _closing_response(
                character_text,
                fallback="Great job. We did it together!",
            )
        return {
            "speaker": context["ai_character"].upper(),
            "text": character_text,
            "score": score,
            "source": "llm",
        }

    async def _restore_session(
        self,
        *,
        mission: RoleplayMission,
        session_id: int,
        history: list[dict] | None = None,
    ) -> Any:
        if RoleplaySession is None or start_roleplay_session is None or process_roleplay_text_turn is None:
            raise RuntimeError(f"AI roleplay module is unavailable: {AI_ROLEPLAY_IMPORT_ERROR}")
        context = roleplay_runtime_context(mission)
        scenario = SimpleNamespace(
            scenario_id=str(mission.mission_id),
            topic="roleplay",
            level=self._level_for_mission(mission),
            scene_description=context["situation"],
            character_name=context["ai_character"],
            character_personality=(
                f"{context['ai_character']} stays in the story scene, talks directly to "
                f"{context['child_role']}, answers warmly, and asks short helpful questions."
            ),
            opening_line=context["opening_message"],
            max_turns=context["required_turns"],
            conversation_flow=[],
            player_goal=context["player_goal"],
            model_answer=mission.model_answer or "",
            similar_answers=mission.similar_answers or [],
            hint_sequence=mission.hint_sequence or [],
        )
        ai_session = RoleplaySession(scenario)
        start_roleplay_session(ai_session)
        messages = list((
            await self.session.execute(
                select(RoleplayMessage)
                .where(
                    RoleplayMessage.session_id == session_id,
                    RoleplayMessage.mission_id == mission.mission_id,
                )
                .order_by(RoleplayMessage.turn, RoleplayMessage.message_id)
            )
        ).scalars().all())
        for message in messages[-context["required_turns"]:]:
            if message.user_transcript.strip():
                ai_session.conversation_history.append({
                    "role": "user",
                    "content": message.user_transcript.strip(),
                })
            if message.character_response.strip():
                ai_session.conversation_history.append({
                    "role": "assistant",
                    "content": message.character_response.strip(),
                })
            ai_session.turn_count = max(ai_session.turn_count, message.turn)
            ai_session.goal_achieved = ai_session.goal_achieved or (message.score or 0) >= 70
        for index, message in enumerate(history or [], start=1):
            user_text = str(message.get("user") or "").strip()
            npc_text = str(message.get("npc") or "").strip()
            if user_text:
                ai_session.conversation_history.append({
                    "role": "user",
                    "content": user_text,
                })
            if npc_text:
                ai_session.conversation_history.append({
                    "role": "assistant",
                    "content": npc_text,
                })
            ai_session.turn_count = max(ai_session.turn_count, index)
        ai_session.completed = ai_session.turn_count >= ai_session.max_turns
        ai_session.passed = ai_session.completed and ai_session.goal_achieved
        return ai_session

    @staticmethod
    def _level_for_mission(mission: RoleplayMission) -> int:
        if mission.required_turns <= 1:
            return 1
        return 2 if mission.required_turns == 2 else 3


class MockRoleplayService(RoleplayService):
    async def respond(
        self,
        *,
        mission: RoleplayMission,
        session_id: int = 0,
        transcript: str,
        turn: int,
        history: list[dict] | None = None,
    ) -> dict:
        context = roleplay_runtime_context(mission)
        score = self._score_response(mission=mission, transcript=transcript)
        text = self._response_for_turn(
            mission=mission,
            transcript=transcript,
            turn=turn,
            score=score,
        )
        return {
            "speaker": context["ai_character"].upper(),
            "text": text,
            "score": score,
            "source": "fallback",
        }

    def _response_for_turn(
        self,
        *,
        mission: RoleplayMission,
        transcript: str,
        turn: int,
        score: int,
    ) -> str:
        context = roleplay_runtime_context(mission)
        child_role = context["child_role"]
        lowered = f"{context['situation']} {mission.model_answer or ''}".lower()

        if "stuck behind" in lowered and "chair" in lowered:
            if turn <= 1:
                if score >= 50:
                    return f"Oh no, {child_role}! I can help. Can you move a little?"
                return f"I'm here, {child_role}. Are you stuck behind the chair?"
            if turn == 2:
                return "Hold my hand. I will pull slowly."
            return f"Great, {child_role}. I can move the chair now. You can get out!"

        if "stuck" in lowered or "trapped" in lowered:
            if turn <= 1:
                return f"Oh no, {child_role}! I can help. Where are you stuck?"
            if turn == 2:
                return "Let's move slowly and stay together."
            return "We did it. You are safe now!"

        if "direction" in lowered or "find their way" in lowered or "where" in lowered:
            if turn <= 1:
                return "Yes, I can help. Who are you looking for?"
            if turn == 2:
                return "Look near the sunflowers. Your friends may be that way."
            return "Great asking. Let's follow the path together."

        if score >= 70:
            return "Thank you! That helps a lot."

        hints = mission.hint_sequence or ["Can you say it another way?"]
        return hints[min(turn - 1, len(hints) - 1)]

    def _score_response(self, *, mission: RoleplayMission, transcript: str) -> int:
        normalized_transcript = self._normalize(transcript)
        if not normalized_transcript:
            return 0
        unsafe_words = {"hit", "kill", "hate", "shut up"}
        if any(word in normalized_transcript for word in unsafe_words):
            return 20

        candidates = [
            mission.model_answer,
            *(mission.similar_answers or []),
            mission.player_goal,
        ]
        candidates = [self._normalize(candidate) for candidate in candidates if candidate]
        if not candidates:
            return 90

        best_ratio = max(
            SequenceMatcher(None, candidate, normalized_transcript).ratio()
            for candidate in candidates
        )
        token_overlap = max(
            self._token_overlap(candidate, normalized_transcript)
            for candidate in candidates
        )
        return round(max(best_ratio, token_overlap) * 100)

    @staticmethod
    def _normalize(text: str) -> str:
        lowered = text.lower().strip()
        lowered = re.sub(r"[^a-z0-9\s']", " ", lowered)
        return " ".join(lowered.split())

    @staticmethod
    def _token_overlap(expected: str, actual: str) -> float:
        expected_tokens = set(expected.split())
        actual_tokens = set(actual.split())
        if not expected_tokens:
            return 0
        return len(expected_tokens & actual_tokens) / len(expected_tokens)
