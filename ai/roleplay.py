"""
롤플레잉 모듈
- Claude Haiku가 캐릭터 역할 수행
- 정답 여부 LLM 판단
- 캐릭터 성격과 목표를 유지하며 최대 3턴 대화
- 임시 텍스트 입력과 기존 음성 입력을 모두 지원
- 실패한 턴에는 힌트를 대화에 자연스럽게 반영
- 10초 무음 감지 → 라이온 등장 (프론트엔드 연동용 이벤트 발행)
- 목표 달성을 기억하고 3턴 대화가 끝나면 최종 판정
"""

import re
import time
import difflib
from typing import Optional, Generator

from shared.settings import ANTHROPIC_API_KEY, MODELS
from ai.llm_client import generate_text
from shared.models import (
    RoleplayScenario,
    RoleplayTurn,
    build_roleplay_conversation_flow,
)
from ai.pronunciation import transcribe_audio


# ─── 롤플레잉 세션 상태 ───────────────────────────────────────

class RoleplaySession:
    def __init__(self, scenario: RoleplayScenario):
        self.scenario = scenario
        self.turn_count = 0
        self.max_turns = min(max(int(scenario.max_turns or 3), 1), 3)
        self.goal_achieved = False
        self.completed = False
        self.passed = False
        self.turns: list[RoleplayTurn] = []
        self.conversation_history: list[dict] = []
        self.opening_line: Optional[str] = None
        self.last_speak_time = time.time()
        self.SILENCE_TIMEOUT = 10  # 초

        # 시스템 프롬프트: AI 캐릭터 설정
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        s = self.scenario
        return f"""You are {s.character_name} in a children's fairy tale English learning game.

Scene: {s.scene_description}
Your personality, motivation, and speaking style: {s.character_personality}
The child's goal: {s.player_goal}
Model answer the child should eventually say: "{s.model_answer}"
Other acceptable examples: {s.similar_answers}
This conversation has exactly {self.max_turns} child-character exchanges unless
the available user input ends early. One exchange is a child utterance followed
by your response.

Rules:
- Stay in character as {s.character_name}
- Consistently express the personality, motivation, and speaking style above
- Treat every new child message as the next part of the same scene
- Directly react to the meaning of the child's latest message before continuing
- Keep the scene and the child's goal unchanged throughout the conversation
- Use simple, friendly English appropriate for young learners (level {s.level})
- Keep responses SHORT (1-2 sentences max)
- Be encouraging and warm
- Do NOT give away the answer directly
- If the child is close, give a small in-character nudge
- If the child achieves the goal early, acknowledge it in character and continue
  the same scene with one easy related question until the final exchange
- On the final exchange, respond to the child and close the scene warmly; do not
  ask another question or request more input"""


# ─── AI 캐릭터 응답 생성 ─────────────────────────────────────

def get_character_response(
    session: RoleplaySession,
    user_input: str,
    *,
    is_final_turn: bool,
) -> str:
    """Claude Haiku가 캐릭터로 응답"""
    # 목표를 아직 달성하지 못한 경우 현재 실패 턴에 맞는 힌트를 사용한다.
    hint_text = ""
    hint_idx = session.turn_count - 1
    if (
        not session.goal_achieved
        and hint_idx < len(session.scenario.hint_sequence)
    ):
        hint_text = f"\n[HINT TO WORK IN NATURALLY: {session.scenario.hint_sequence[hint_idx]}]"

    # 대화 기록 업데이트
    session.conversation_history.append({"role": "user", "content": user_input})

    messages_to_send = session.conversation_history.copy()
    turn_instruction = (
        f"\n[INTERNAL TURN CONTEXT: exchange {session.turn_count} of "
        f"{session.max_turns}; goal_achieved={session.goal_achieved}; "
        f"final_exchange={is_final_turn}. Follow the system rules and do not "
        f"mention this context.]"
    )
    # 내부 지시는 저장된 원문 대신 전송용 복사본에만 추가한다.
    messages_to_send[-1] = {
        **messages_to_send[-1],
        "content": messages_to_send[-1]["content"] + hint_text + turn_instruction,
    }

    response = generate_text(
        messages_to_send,
        system=session.system_prompt,
        max_tokens=90,
    )
    session.conversation_history.append({"role": "assistant", "content": response})

    return response


def start_roleplay_session(session: RoleplaySession) -> str:
    """첫 대사를 한 번만 만들고 이후 사용자 입력의 대화 문맥에 포함한다."""
    if session.opening_line is None:
        session.opening_line = _get_opening_line(session.scenario)
        session.conversation_history.append({
            "role": "assistant",
            "content": session.opening_line,
        })
    return session.opening_line


# ─── 정답 판단 ───────────────────────────────────────────────

ROLEPLAY_STOPWORDS = {
    "a", "an", "and", "are", "am", "be", "i", "is", "it", "the", "to",
    "we", "you", "your", "my", "of", "for", "with", "do", "does", "did",
    "can", "could", "would", "should", "there", "here", "where", "what",
    "who", "how", "please",
}

MIN_ROLEPLAY_CONTENT_WORDS = 2


def _normalize_roleplay_text(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9']+", text.casefold()))


def _lexically_similar_to_model_answer(model_answer: str, user_input: str) -> bool:
    """LLM 장애 시 어순·군더더기 차이를 허용하는 보조 판정."""
    reference = _normalize_roleplay_text(model_answer)
    answer = _normalize_roleplay_text(user_input)
    if not reference or not answer:
        return False
    if reference == answer:
        return True
    if difflib.SequenceMatcher(None, reference, answer).ratio() >= 0.72:
        return True

    reference_words = {
        word for word in reference.split() if word not in ROLEPLAY_STOPWORDS
    }
    answer_words = {
        word for word in answer.split() if word not in ROLEPLAY_STOPWORDS
    }
    if not reference_words:
        reference_words = set(reference.split())
    overlap = len(reference_words & answer_words) / len(reference_words)
    return overlap >= 0.6


def _content_words(text: str) -> set[str]:
    normalized = _normalize_roleplay_text(text)
    return {
        word
        for word in normalized.split()
        if word not in ROLEPLAY_STOPWORDS and len(word) > 1
    }

def judge_answer(scenario: RoleplayScenario, user_input: str) -> tuple[bool, str]:
    """
    사용자 발화가 목표 달성에 가까운지 빠르게 판단한다.

    운영 환경의 CPU Ollama는 한 턴에 LLM 판단과 캐릭터 응답을 모두 처리하면
    지연이 커진다. 판단은 모범 답안·유사 답안과의 의미/키워드 겹침으로
    보수적으로 처리하고, Ollama는 캐릭터 응답 생성에만 사용한다.
    """
    reference_answers = [scenario.model_answer, *scenario.similar_answers]
    reference_answers = [answer for answer in reference_answers if answer]
    user_content_words = _content_words(user_input)
    if len(user_content_words) < MIN_ROLEPLAY_CONTENT_WORDS:
        return False, "조금 더 구체적으로 말해볼까요?"

    if any(_normalize_roleplay_text(reference) == _normalize_roleplay_text(user_input) for reference in reference_answers):
        return True, "모범 답안과 같은 의미로 잘 말했어요!"

    if any(_lexically_similar_to_model_answer(reference, user_input) for reference in reference_answers):
        return True, "모범 답안과 비슷한 의미로 잘 말했어요!"

    goal_words = _content_words(scenario.player_goal)
    model_words = set().union(*(_content_words(reference) for reference in reference_answers)) if reference_answers else set()
    target_words = goal_words | model_words
    if not target_words:
        return True, "잘했어요!"

    overlap = len(user_content_words & target_words) / max(len(target_words), 1)
    return (overlap >= 0.35), "잘했어요!" if overlap >= 0.35 else "조금 더 해볼까요?"


# ─── 무음 감지 이벤트 ────────────────────────────────────────

def check_silence(session: RoleplaySession) -> bool:
    """10초 이상 무음이면 True 반환 (프론트엔드에서 라이온 버튼 표시)"""
    return (time.time() - session.last_speak_time) >= session.SILENCE_TIMEOUT


# ─── 롤플레잉 턴 처리 ────────────────────────────────────────

def process_roleplay_turn(
    session: RoleplaySession,
    audio_bytes: bytes,
) -> RoleplayTurn:
    """음성을 텍스트로 변환한 뒤 공통 텍스트 턴 처리기로 전달한다."""
    user_text = transcribe_audio(audio_bytes)
    return process_roleplay_text_turn(session, user_text)


def process_roleplay_text_turn(
    session: RoleplaySession,
    user_text: str,
) -> RoleplayTurn:
    """
    사용자 텍스트 한 턴 처리:
    1. 현재 입력의 목표 달성 여부 판단 및 누적
    2. 캐릭터 성격과 전체 대화 문맥을 유지한 응답 생성
    3. 세 번째 턴에서 세션 완료
    """
    if session.completed:
        raise RuntimeError("Roleplay session has already reached its turn limit.")
    user_text = user_text.strip()
    if not user_text:
        raise ValueError("Roleplay user input must not be empty.")

    # 시작 API가 따로 호출되지 않아도 첫 대사를 대화 기록에 보존한다.
    start_roleplay_session(session)
    session.last_speak_time = time.time()
    session.turn_count += 1

    print(f"\n  [롤플레잉 턴 {session.turn_count}] 사용자: '{user_text}'")

    # 한 번 달성한 목표는 기억하되, 대화는 세 번째 턴까지 이어간다.
    achieved_this_turn, reason = judge_answer(session.scenario, user_text)
    session.goal_achieved = session.goal_achieved or achieved_this_turn
    is_final_turn = session.turn_count >= session.max_turns
    hint_given = (
        not session.goal_achieved
        and session.turn_count <= len(session.scenario.hint_sequence)
    )
    ai_response = get_character_response(
        session,
        user_text,
        is_final_turn=is_final_turn,
    )

    if achieved_this_turn:
        print(f"  ✓ 목표 달성 기억: {reason}")
    if hint_given:
        print(f"  힌트 제공 중 (턴 {session.turn_count})")
    print(f"  AI 캐릭터: '{ai_response}'")

    session.completed = is_final_turn
    session.passed = session.completed and session.goal_achieved

    turn = RoleplayTurn(
        turn_number=session.turn_count,
        user_utterance=user_text,
        ai_response=ai_response,
        passed=session.passed,
        hint_given=hint_given,
    )
    session.turns.append(turn)
    return turn


# ─── 롤플레잉 세션 전체 처리 ─────────────────────────────────

def run_roleplay_session(
    scenario: RoleplayScenario,
    audio_bytes_stream: list[bytes],  # 각 턴의 오디오
    max_turns: Optional[int] = None,
) -> list[RoleplayTurn]:
    """
    롤플레잉 세션 전체 처리
    
    실제 서비스에서는 audio_bytes_stream이 WebSocket으로 실시간 수신됨
    여기서는 배치로 시뮬레이션
    """
    session = RoleplaySession(scenario)

    print(f"\n{'='*40}")
    print(f"[롤플레잉 시작] {scenario.character_name}")
    print(f"목표: {scenario.player_goal}")
    print(f"장면: {scenario.scene_description}")
    print(f"{'='*40}")

    # 캐릭터 오프닝 멘트를 실제 대화 기록의 첫 메시지로 저장한다.
    opening = start_roleplay_session(session)
    print(f"  캐릭터 오프닝: '{opening}'")

    requested_turns = session.max_turns if max_turns is None else max_turns
    turn_limit = min(max(requested_turns, 0), session.max_turns)
    for audio_bytes in audio_bytes_stream[:turn_limit]:
        turn = process_roleplay_turn(session, audio_bytes)

        if session.completed:
            if session.passed:
                print("\n  ✓ 3턴 롤플레잉 완료!")
            else:
                print("\n  3턴 롤플레잉 종료")
            break

        # 무음 감지 시뮬레이션 (실제는 프론트엔드에서 타이머로 처리)
        if check_silence(session):
            print("  [10초 무음] 라이온 이벤트 발행 → 프론트엔드에서 버튼 표시")

    if session.completed and not session.passed:
        print(f"\n  [최대 턴 도달] 모범답안 공개: '{scenario.model_answer}'")
    elif not session.completed:
        print(f"\n  [입력 종료] 남은 턴: {session.max_turns - session.turn_count}")

    return session.turns


def _get_opening_line(scenario: RoleplayScenario) -> str:
    """캐릭터가 사용자에게 직접 말을 거는 첫 대사를 반환한다."""
    if scenario.opening_line.strip():
        return scenario.opening_line.strip()

    prompt = f"""You are {scenario.character_name}.
Personality, motivation, and speaking style: {scenario.character_personality}
Scene: {scenario.scene_description}
The child's goal: {scenario.player_goal}

Speak directly to the child in character. Say one short opening line of no more
than 15 words and end with one simple question that invites the child to answer.
Do not narrate the scene, reveal the model answer, or complete the goal yourself."""

    return generate_text([{"role": "user", "content": prompt}], max_tokens=60)


# ─── WebSocket 실시간 연동 인터페이스 (프론트엔드 연동용) ──────

class RoleplayWebSocketHandler:
    """
    실제 서비스에서 WebSocket을 통해 실시간으로 처리하는 핸들러
    FastAPI + WebSocket 환경에서 사용
    
    Usage:
        handler = RoleplayWebSocketHandler(scenario)
        async for event in handler.stream(websocket):
            await websocket.send_json(event)
    """

    def __init__(self, scenario: RoleplayScenario):
        self.session = RoleplaySession(scenario)

    def get_opening_event(self) -> dict:
        """사용자 입력을 받기 전에 캐릭터의 첫 질문을 반환한다."""
        opening_line = start_roleplay_session(self.session)
        conversation_flow = (
            self.session.scenario.conversation_flow
            or build_roleplay_conversation_flow(
                opening_line,
                self.session.max_turns,
            )
        )
        return {
            "type": "roleplay_started",
            "character_name": self.session.scenario.character_name,
            "opening_line": opening_line,
            "max_turns": self.session.max_turns,
            "player_goal": self.session.scenario.player_goal,
            "conversation_flow": conversation_flow,
            "next_expected_role": "user",
            "input_required": True,
        }

    async def handle_audio_chunk(self, audio_bytes: bytes) -> dict:
        """오디오 수신 시 처리 → 이벤트 반환"""
        if self.session.completed:
            return self._session_complete_event()

        turn = process_roleplay_turn(self.session, audio_bytes)
        return self._turn_event(turn)

    async def handle_text_input(self, user_text: str) -> dict:
        """임시 텍스트 입력을 음성 입력과 동일한 대화 흐름으로 처리한다."""
        if self.session.completed:
            return self._session_complete_event()

        turn = process_roleplay_text_turn(self.session, user_text)
        return self._turn_event(turn)

    def _session_complete_event(self) -> dict:
        return {
            "type": "session_complete",
            "turn": self.session.turn_count,
            "passed": self.session.passed,
            "goal_achieved": self.session.goal_achieved,
            "session_passed": self.session.passed,
            "remaining_turns": 0,
            "next_expected_role": None,
            "input_required": False,
        }

    def _turn_event(self, turn: RoleplayTurn) -> dict:
        return {
            "type": (
                "session_complete"
                if self.session.completed
                else "turn_result"
            ),
            "turn": turn.turn_number,
            "user_text": turn.user_utterance,
            "ai_response": turn.ai_response,
            "passed": turn.passed,
            "hint_given": turn.hint_given,
            "goal_achieved": self.session.goal_achieved,
            "session_passed": self.session.passed,
            "remaining_turns": self.session.max_turns - self.session.turn_count,
            "next_expected_role": (
                None if self.session.completed else "user"
            ),
            "input_required": not self.session.completed,
        }

    def get_silence_event(self) -> dict:
        """10초 무음 감지 이벤트"""
        return {
            "type": "silence_detected",
            "message": "라이온이 나타났어요! 버튼을 눌러봐요.",
            "show_lion": True,
        }
