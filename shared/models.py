"""
공통 데이터 모델
"""
from dataclasses import dataclass, field
from typing import Optional
from enum import Enum


class DescriptionType(str, Enum):
    WORD_GUESS   = "word_guess"    # 1단계: 빈칸 단어 추론
    SENTENCE     = "sentence"      # 2단계: 한 문장 상황 설명
    REASON       = "reason"        # 3단계: 장면 묘사 + 이유


class RoleplayTopic(str, Enum):
    INTRO        = "self_intro"    # 1단계: 자기소개
    DIRECTION    = "direction"     # 2단계: 길 묻기
    ESCAPE       = "escape"        # 3단계: 무도회장 탈출


@dataclass
class StoryPage:
    page_number: int
    text: str                        # 동화 문장
    image_prompt: str                # 이미지 생성용 프롬프트
    image_path: Optional[str] = None  # 생성된/생성 예정 이미지 경로
    audio_path: Optional[str] = None # TTS 결과 파일 경로


@dataclass
class DescriptionScene:
    scene_number: int
    image_path: str
    desc_type: DescriptionType
    blank_word: Optional[str] = None   # 1단계: 빈칸에 들어갈 단어
    answer_sentence: str = ""          # 정답 문장 (가이드라인 포함)
    guide_hint: str = ""               # 회색 글자 가이드라인 (answer 앞부분)


@dataclass
class RoleplayScenario:
    scenario_id: str
    topic: RoleplayTopic
    level: int
    scene_description: str           # 장면 설명
    character_name: str              # AI가 맡을 캐릭터
    player_goal: str                 # 플레이어가 달성해야 할 목표
    model_answer: str                # 모범 답안 (LLM 판단 기준)
    hint_sequence: list[str] = field(default_factory=list)  # 3턴 후 순차 힌트


@dataclass
class Lesson:
    lesson_id: str
    book_id: str
    level: int
    episode: int
    pages: list[StoryPage] = field(default_factory=list)
    description_scenes: list[DescriptionScene] = field(default_factory=list)
    roleplay_scenarios: list[RoleplayScenario] = field(default_factory=list)


@dataclass
class PronunciationResult:
    sentence: str
    transcribed: str
    score: int           # 0~100
    passed: bool
    word_scores: dict[str, float] = field(default_factory=dict)  # 단어별 유사도


@dataclass
class DescriptionResult:
    scene_number: int
    user_answer: str
    passed: bool
    feedback: str


@dataclass
class RoleplayTurn:
    turn_number: int
    user_utterance: str
    ai_response: str
    passed: bool = False
    hint_given: bool = False


@dataclass
class LessonResult:
    lesson_id: str
    pronunciation_results: list[PronunciationResult] = field(default_factory=list)
    description_results:   list[DescriptionResult]   = field(default_factory=list)
    roleplay_turns:        list[RoleplayTurn]         = field(default_factory=list)
    total_score: int = 0
    passed: bool = False
