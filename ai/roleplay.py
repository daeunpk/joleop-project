"""
롤플레잉 모듈
- Claude Haiku가 캐릭터 역할 수행
- 정답 여부 LLM 판단
- 3턴 초과 시 힌트 순차 제공
- 10초 무음 감지 → 라이온 등장 (프론트엔드 연동용 이벤트 발행)
- 언제든 정답을 말하면 패스
"""

import json
import re
import time
from typing import Optional, Generator

from shared.settings import ANTHROPIC_API_KEY, MODELS
from ai.llm_client import generate_text
from shared.models import RoleplayScenario, RoleplayTurn
from ai.pronunciation import transcribe_audio


# ─── 롤플레잉 세션 상태 ───────────────────────────────────────

class RoleplaySession:
    def __init__(self, scenario: RoleplayScenario):
        self.scenario = scenario
        self.turn_count = 0
        self.passed = False
        self.turns: list[RoleplayTurn] = []
        self.conversation_history: list[dict] = []
        self.last_speak_time = time.time()
        self.SILENCE_TIMEOUT = 10  # 초

        # 시스템 프롬프트: AI 캐릭터 설정
        self.system_prompt = self._build_system_prompt()

    def _build_system_prompt(self) -> str:
        s = self.scenario
        return f"""You are {s.character_name} in a children's fairy tale English learning game.

Scene: {s.scene_description}
The child's goal: {s.player_goal}
Model answer the child should eventually say: "{s.model_answer}"

Rules:
- Stay in character as {s.character_name}
- Use simple, friendly English appropriate for young learners (level {s.level})
- Keep responses SHORT (1-2 sentences max)
- Be encouraging and warm
- Do NOT give away the answer directly
- If the child is close, give a small nudge
- React naturally to what the child says"""


# ─── AI 캐릭터 응답 생성 ─────────────────────────────────────

def get_character_response(session: RoleplaySession, user_input: str) -> str:
    """Claude Haiku가 캐릭터로 응답"""
    # 힌트 추가 여부 결정
    hint_text = ""
    if session.turn_count >= 3 and session.turn_count - 3 < len(session.scenario.hint_sequence):
        hint_idx = session.turn_count - 3
        hint_text = f"\n[HINT TO WORK IN NATURALLY: {session.scenario.hint_sequence[hint_idx]}]"

    # 대화 기록 업데이트
    session.conversation_history.append({"role": "user", "content": user_input})

    messages_to_send = session.conversation_history.copy()
    if hint_text:
        # 힌트는 마지막 user 메시지 뒤에 시스템 지시로 삽입
        messages_to_send[-1]["content"] += hint_text

    response = generate_text(
        messages_to_send,
        system=session.system_prompt,
        max_tokens=150,
    )
    session.conversation_history.append({"role": "assistant", "content": response})

    return response


# ─── 정답 판단 ───────────────────────────────────────────────

def judge_answer(scenario: RoleplayScenario, user_input: str) -> tuple[bool, str]:
    """
    LLM으로 사용자 발화가 목표 달성인지 판단
    정답이 완전히 동일하지 않아도 의미가 맞으면 패스
    """
    prompt = f"""You are judging a child's English roleplay response in a fairy tale learning game.

Player's goal: {scenario.player_goal}
Model answer: "{scenario.model_answer}"
Child said: "{user_input}"

Does the child's response successfully achieve the goal?
Be LENIENT — different words or simpler phrasing is fine as long as the intent matches.

Reply ONLY with JSON: {{"passed": true/false, "reason": "one sentence in Korean"}}"""

    try:
        text = generate_text([{"role": "user", "content": prompt}], max_tokens=150)
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        return result["passed"], result["reason"]
    except Exception:
        # 간단한 키워드 fallback
        key_words = scenario.model_answer.lower().split()
        user_words = user_input.lower().split()
        overlap = len(set(key_words) & set(user_words)) / max(len(key_words), 1)
        passed = overlap >= 0.5
        return passed, "잘했어요!" if passed else "조금 더 해볼까요?"


# ─── 무음 감지 이벤트 ────────────────────────────────────────

def check_silence(session: RoleplaySession) -> bool:
    """10초 이상 무음이면 True 반환 (프론트엔드에서 라이온 버튼 표시)"""
    return (time.time() - session.last_speak_time) >= session.SILENCE_TIMEOUT


# ─── 롤플레잉 턴 처리 ────────────────────────────────────────

def process_roleplay_turn(
    session: RoleplaySession,
    audio_bytes: bytes,
) -> RoleplayTurn:
    """
    한 턴 처리:
    1. STT
    2. 정답 판단
    3. 통과 or AI 캐릭터 응답 (힌트 포함)
    """
    session.last_speak_time = time.time()
    session.turn_count += 1

    # STT
    user_text = transcribe_audio(audio_bytes)
    print(f"\n  [롤플레잉 턴 {session.turn_count}] 사용자: '{user_text}'")

    # 정답 판단 (매 턴)
    passed, reason = judge_answer(session.scenario, user_text)
    hint_given = session.turn_count > 3

    if passed:
        session.passed = True
        ai_response = f"Great job! {reason} You did it! 🎉"
        print(f"  ✓ 패스: {reason}")
    else:
        ai_response = get_character_response(session, user_text)
        if hint_given:
            print(f"  힌트 제공 중 (턴 {session.turn_count})")
        print(f"  AI 캐릭터: '{ai_response}'")

    turn = RoleplayTurn(
        turn_number=session.turn_count,
        user_utterance=user_text,
        ai_response=ai_response,
        passed=passed,
        hint_given=hint_given,
    )
    session.turns.append(turn)
    return turn


# ─── 롤플레잉 세션 전체 처리 ─────────────────────────────────

def run_roleplay_session(
    scenario: RoleplayScenario,
    audio_bytes_stream: list[bytes],  # 각 턴의 오디오
    max_turns: int = 15,
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

    # 캐릭터 오프닝 멘트
    opening = _get_opening_line(scenario)
    print(f"  캐릭터 오프닝: '{opening}'")

    for audio_bytes in audio_bytes_stream[:max_turns]:
        turn = process_roleplay_turn(session, audio_bytes)

        if session.passed:
            print("\n  ✓ 롤플레잉 완료!")
            break

        # 무음 감지 시뮬레이션 (실제는 프론트엔드에서 타이머로 처리)
        if check_silence(session):
            print("  [10초 무음] 라이온 이벤트 발행 → 프론트엔드에서 버튼 표시")

    if not session.passed:
        print(f"\n  [최대 턴 도달] 모범답안 공개: '{scenario.model_answer}'")

    return session.turns


def _get_opening_line(scenario: RoleplayScenario) -> str:
    """캐릭터 첫 등장 대사"""
    prompt = f"""You are {scenario.character_name}. Say one short greeting line (max 15 words) 
to start the scene: "{scenario.scene_description}". 
Keep it simple for young English learners."""

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

    async def handle_audio_chunk(self, audio_bytes: bytes) -> dict:
        """오디오 수신 시 처리 → 이벤트 반환"""
        turn = process_roleplay_turn(self.session, audio_bytes)

        event = {
            "type": "turn_result",
            "turn": turn.turn_number,
            "user_text": turn.user_utterance,
            "ai_response": turn.ai_response,
            "passed": turn.passed,
            "hint_given": turn.hint_given,
            "session_passed": self.session.passed,
        }

        if self.session.passed:
            event["type"] = "session_complete"

        return event

    def get_silence_event(self) -> dict:
        """10초 무음 감지 이벤트"""
        return {
            "type": "silence_detected",
            "message": "라이온이 나타났어요! 버튼을 눌러봐요.",
            "show_lion": True,
        }
