"""
묘사 채점 모듈
- 1단계: 룰베이스 (빈칸 단어 매칭)
- 2단계: 룰베이스 + LLM 보조 (문장 의미 검증)
- 3단계: 룰베이스 + LLM (이유 포함 여부 판단)

공통 규칙:
- 전체 문장을 말해야 통과
- 회색 가이드라인(guide_hint) 제공
- Whisper STT → 채점
"""

import re
import json
import difflib

from shared.settings import ANTHROPIC_API_KEY, MODELS
from ai.llm_client import generate_text
from shared.models import DescriptionScene, DescriptionType, DescriptionResult
from ai.pronunciation import transcribe_audio, normalize_text


# ─── 공통: 전체 문장 완성도 체크 (룰베이스) ─────────────────

def check_sentence_completeness(answer_sentence: str, transcribed: str) -> tuple[bool, float]:
    """
    정답 문장의 핵심 단어가 모두 포함되었는지 확인
    Returns: (passed, similarity_ratio)
    """
    ref = normalize_text(answer_sentence)
    hyp = normalize_text(transcribed)
    
    ratio = difflib.SequenceMatcher(None, ref.split(), hyp.split()).ratio()
    # 70% 이상 유사하면 완성 간주
    return ratio >= 0.70, ratio


# ─── 1단계: 빈칸 단어 매칭 ───────────────────────────────────

def score_level1(scene: DescriptionScene, transcribed: str) -> tuple[bool, str]:
    """
    빈칸 단어(blank_word)가 발화에 포함되어 있는지 + 전체 문장 완성도 확인
    """
    if not scene.blank_word:
        return False, "빈칸 단어 정보 없음"

    hyp = normalize_text(transcribed)
    blank = normalize_text(scene.blank_word)

    # 빈칸 단어 포함 여부
    word_found = blank in hyp.split() or any(
        difflib.SequenceMatcher(None, blank, w).ratio() > 0.85
        for w in hyp.split()
    )

    if not word_found:
        return False, f"'{scene.blank_word}' 단어가 발화에 없습니다. 다시 말해보세요."

    # 전체 문장 완성도
    complete, ratio = check_sentence_completeness(scene.answer_sentence, transcribed)
    if not complete:
        return False, f"문장을 완전하게 말해주세요. (현재 {int(ratio*100)}% 완성)"

    return True, "정답입니다!"


# ─── 2단계: LLM 보조 채점 ────────────────────────────────────

def score_level2(scene: DescriptionScene, transcribed: str) -> tuple[bool, str]:
    """
    한 문장 상황 설명
    1) 룰베이스: 문장 완성도 체크
    2) LLM: 의미적 정확성 검증
    """
    complete, ratio = check_sentence_completeness(scene.answer_sentence, transcribed)
    if not complete and ratio < 0.4:
        # 너무 다르면 LLM 호출 전에 실패 처리
        return False, f"문장을 완전하게 말해주세요. 힌트: '{scene.guide_hint}...'"

    # LLM 의미 검증
    prompt = f"""You are evaluating a child's English sentence describing a picture.

Reference answer: "{scene.answer_sentence}"
Child's response: "{transcribed}"

Is the child's response:
1. A complete sentence (not just a word or fragment)?
2. Describing the same scene/situation as the reference?

Reply ONLY with JSON: {{"passed": true/false, "feedback": "one short encouraging sentence in Korean"}}"""

    try:
        text = generate_text([{"role": "user", "content": prompt}], max_tokens=150)
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        return result["passed"], result["feedback"]
    except Exception:
        # LLM 실패 시 룰베이스 결과 사용
        return complete, "잘했어요!" if complete else "다시 한번 해볼까요?"


# ─── 3단계: LLM 이유 판단 ────────────────────────────────────

def score_level3(scene: DescriptionScene, transcribed: str) -> tuple[bool, str]:
    """
    장면 묘사 + 이유 말하기
    'because', 'so', 'since' 등 인과 표현 포함 여부 + LLM 내용 검증
    """
    hyp = normalize_text(transcribed)
    
    # 인과 표현 키워드 체크
    causal_keywords = ["because", "since", "so", "because of", "that's why", "due to"]
    has_reason = any(kw in hyp for kw in causal_keywords)
    
    if not has_reason:
        return False, "'because'를 사용해서 이유를 말해보세요. 예: 'She looks happy because...'"

    # LLM 내용 검증
    prompt = f"""You are evaluating a child's English answer for a fairy tale scene description task.

The child must describe a scene AND give a reason using "because".
Reference answer: "{scene.answer_sentence}"
Child's response: "{transcribed}"

Evaluate:
1. Does it describe the scene correctly?
2. Does it include a reasonable reason (even if different from reference)?
3. Is it a complete sentence?

Be lenient — any reasonable reason related to the story is acceptable.

Reply ONLY with JSON: {{"passed": true/false, "feedback": "one encouraging sentence in Korean"}}"""

    try:
        text = generate_text([{"role": "user", "content": prompt}], max_tokens=150)
        text = re.sub(r"```json|```", "", text).strip()
        result = json.loads(text)
        return result["passed"], result["feedback"]
    except Exception:
        return has_reason, "잘했어요!" if has_reason else "이유를 말해주세요."


# ─── 통합 묘사 채점 함수 ─────────────────────────────────────

def evaluate_description(
    scene: DescriptionScene,
    audio_bytes: bytes,
) -> DescriptionResult:
    """
    묘사 채점 메인 함수
    """
    # STT
    transcribed = transcribe_audio(audio_bytes)
    print(f"  [묘사 STT] 인식: '{transcribed}'")

    # 단계별 채점
    scorers = {
        DescriptionType.WORD_GUESS: score_level1,
        DescriptionType.SENTENCE:   score_level2,
        DescriptionType.REASON:     score_level3,
    }
    scorer = scorers.get(scene.desc_type, score_level1)
    passed, feedback = scorer(scene, transcribed)

    # 오답 시 모범답안 노출 (1단계) or 피드백 (2,3단계)
    if not passed and scene.desc_type == DescriptionType.WORD_GUESS:
        feedback += f"\n모범답안: {scene.answer_sentence}"

    print(f"  [묘사 채점] {'✓ 통과' if passed else '✗ 실패'}: {feedback}")

    return DescriptionResult(
        scene_number=scene.scene_number,
        user_answer=transcribed,
        passed=passed,
        feedback=feedback,
    )


# ─── 묘사 세션 전체 처리 ─────────────────────────────────────

def run_description_session(
    scenes: list[DescriptionScene],
    audio_bytes_list: list[bytes],
) -> list[DescriptionResult]:
    """
    묘사 세션 전체 처리
    각 장면마다 최대 2회 시도, 모두 실패 시 모범답안 노출
    """
    results = []
    for i, scene in enumerate(scenes):
        print(f"\n  [장면 {scene.scene_number}] 타입: {scene.desc_type.value}")
        print(f"  가이드라인: '{scene.guide_hint}...'")

        if i >= len(audio_bytes_list):
            break

        result = evaluate_description(scene, audio_bytes_list[i])
        
        # 실패 시 한 번 더 (2회 시도 제한)
        if not result.passed and i + len(scenes) < len(audio_bytes_list):
            print("  재시도...")
            retry_audio = audio_bytes_list[i + len(scenes)]
            result = evaluate_description(scene, retry_audio)

        results.append(result)

    return results
