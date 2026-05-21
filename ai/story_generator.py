"""
콘텐츠 제작 파이프라인
① 로컬 Llama로 동화 생성
② Qwen judge로 품질 점수 평가
③ 후처리 검사 (문장 수, 어휘, 반복, 문법)
④ 이미지 프롬프트/이미지 + 묘사 퀴즈 + 롤플레잉 시나리오 생성
"""

import re
import json
from typing import Optional

from shared.settings import MODELS, LEVEL_CONFIGS, IMAGE_STYLE_GUIDE
from ai.image_generator import attach_planned_image_paths, generate_story_images
from ai.llm_client import generate_text
from ai.prompts import (
    DESCRIPTION_QUIZ_PROMPT,
    ROLEPLAY_MISSION_PROMPT,
    STORY_GENERATION_PROMPT,
    STORY_JUDGE_PROMPT,
    STORY_REPAIR_PROMPT,
    STORY_SCORE_PROMPT,
)
from shared.models import (
    StoryPage, DescriptionScene, RoleplayScenario,
    DescriptionType, RoleplayTopic, Lesson
)


def call_local_story_model(prompt: str) -> str:
    """로컬 Llama 모델로 동화 초안 생성."""
    try:
        return generate_text(
            [{"role": "user", "content": prompt}],
            model=MODELS.story_model,
            max_tokens=900,
            temperature=0.45,
        )
    except Exception as e:
        print(f"[Local LLM] 동화 생성 실패: {e}")
        return ""


# ─── 동화 생성 프롬프트 ───────────────────────────────────────

def build_story_prompt(
    age: int,
    level: int,
    theme: str,
    protagonist: str,
    *,
    episode: int = 1,
    total_episodes: int = 1,
    continuity_context: str = "This is episode 1. Start the longer story gently.",
) -> str:
    cfg = LEVEL_CONFIGS[level]
    max_words = max_words_for_level(level)
    min_pages, max_pages = page_range_for_level(level)
    return STORY_GENERATION_PROMPT.format(
        age=age,
        level=level,
        theme=theme,
        protagonist=protagonist,
        episode=episode,
        total_episodes=total_episodes,
        continuity_context=continuity_context,
        page_count=cfg.pages,
        min_pages=min_pages,
        max_pages=max_pages,
        max_words=max_words,
    )


def max_words_for_level(level: int) -> int:
    return {1: 12, 2: 16, 3: 20}[level]


def page_range_for_level(level: int) -> tuple[int, int]:
    target = LEVEL_CONFIGS[level].pages
    return max(3, target - 2), target + 2


def repair_story_output(
    draft: str,
    *,
    age: int,
    level: int,
    theme: str,
    protagonist: str,
    episode: int = 1,
    total_episodes: int = 1,
    continuity_context: str = "",
) -> str:
    cfg = LEVEL_CONFIGS[level]
    min_pages, max_pages = page_range_for_level(level)
    prompt = STORY_REPAIR_PROMPT.format(
        draft=draft,
        age=age,
        level=level,
        theme=theme,
        protagonist=protagonist,
        episode=episode,
        total_episodes=total_episodes,
        continuity_context=continuity_context or "Repair this episode while preserving the same longer story.",
        page_count=cfg.pages,
        min_pages=min_pages,
        max_pages=max_pages,
        max_words=max_words_for_level(level),
    )
    try:
        return generate_text(
            [{"role": "user", "content": prompt}],
            model=MODELS.story_model,
            max_tokens=900,
            temperature=0.2,
        )
    except Exception as e:
        print(f"[Local LLM] 동화 형식 수리 실패: {e}")
        return draft


# ─── 후처리 검사 ──────────────────────────────────────────────

def post_process_check(text: str, level: int) -> tuple[bool, str, list[str]]:
    """
    Returns: (passed, cleaned_text, sentences)
    """
    cfg = LEVEL_CONFIGS[level]
    max_words = max_words_for_level(level)
    min_pages, max_pages = page_range_for_level(level)

    # 문장 추출: 새 구조화 출력의 "Story sentence"를 우선 사용하고,
    # 실패하면 예전 numbered-line 형식도 허용한다.
    lines = extract_story_sentences(text)

    # 1. 문장 수 검사
    if not min_pages <= len(lines) <= max_pages:
        return False, f"문장 수 불일치: {len(lines)} (허용: {min_pages}-{max_pages}, 목표: {cfg.pages})", lines

    # 2. 문장 길이 검사
    for i, sent in enumerate(lines):
        wc = len(sent.split())
        if wc > max_words:
            return False, f"문장 {i+1} 너무 김: {wc}단어 (최대 {max_words})", lines

    # 3. 반복 검사 (연속 동일 단어 3개 이상)
    all_text = " ".join(lines).lower()
    words = all_text.split()
    for i in range(len(words) - 2):
        if words[i] == words[i+1] == words[i+2]:
            return False, f"단어 반복 감지: '{words[i]}'", lines

    # 4. 빈 문장 검사
    if any(len(s.strip()) < 5 for s in lines):
        return False, "너무 짧은 문장 존재", lines

    # 5. 따옴표 짝 검사
    if any("'" in s for s in lines):
        return False, "작은따옴표/축약형 사용 감지", lines
    quote_count = sum(s.count('"') + s.count("“") + s.count("”") for s in lines)
    if quote_count % 2 != 0:
        return False, "대화 따옴표 짝이 맞지 않음", lines

    return True, "ok", lines


def extract_story_sentences(text: str) -> list[str]:
    json_sentences = extract_story_sentences_from_json(text)
    if json_sentences:
        return json_sentences

    story_sentence_patterns = [
        r"(?:-\s*)?Story sentence\s*:\s*[\"“”']?(.*?)[\"“”']?\s*$",
        r"(?:-\s*)?Story Sentence\s*:\s*[\"“”']?(.*?)[\"“”']?\s*$",
        r"(?:-\s*)?Sentence\s*:\s*[\"“”']?(.*?)[\"“”']?\s*$",
        r"(?:-\s*)?Text\s*:\s*[\"“”']?(.*?)[\"“”']?\s*$",
    ]
    sentences = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        for pattern in story_sentence_patterns:
            match = re.search(pattern, line, flags=re.IGNORECASE)
            if match:
                sentence = _clean_story_sentence(match.group(1))
                if sentence:
                    sentences.append(sentence)
                break

    if sentences:
        return sentences

    numbered_sentences = []
    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not re.match(r"^\d+[\.)]\s*", line):
            continue
        sentence = _clean_story_sentence(re.sub(r"^\d+[\.)]\s*", "", line))
        if looks_like_story_sentence(sentence):
            numbered_sentences.append(sentence)
    return numbered_sentences


def extract_story_sentences_from_json(text: str) -> list[str]:
    try:
        data = parse_json_object(text)
    except Exception:
        return []

    pages = data.get("pages", [])
    if not isinstance(pages, list):
        return []

    sentences = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        sentence = (
            page.get("story_sentence")
            or page.get("Story sentence")
            or page.get("sentence")
            or page.get("text")
        )
        if sentence:
            sentences.append(_clean_story_sentence(str(sentence)))
    return [sentence for sentence in sentences if sentence]


def looks_like_story_sentence(sentence: str) -> bool:
    lowered = sentence.lower()
    metadata_markers = [
        "story title",
        "ar level",
        "main theme",
        "emotional goal",
        "story structure",
        "page number",
        "korean translation",
        "illustration idea",
    ]
    if any(marker in lowered for marker in metadata_markers):
        return False
    if ":" in sentence and len(sentence.split()) < 8:
        return False
    return bool(re.search(r"[.!?\"”']$", sentence))


def _clean_story_sentence(sentence: str) -> str:
    sentence = sentence.strip().strip("\"'“”")
    sentence = re.sub(r"\s+", " ", sentence)
    return sentence


def story_text_from_sentences(sentences: list[str]) -> str:
    return "\n".join(f"{i + 1}. {sentence}" for i, sentence in enumerate(sentences))


def parse_json_object(text: str) -> dict:
    text = re.sub(r"```json|```", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        return json.loads(match.group(0))


def evaluate_story_score(story_text: str) -> dict:
    prompt = STORY_SCORE_PROMPT.format(story=story_text)
    evaluations = []

    for judge_model in MODELS.story_judge_models:
        try:
            text = generate_text(
                [{"role": "user", "content": prompt}],
                model=judge_model,
                max_tokens=1200,
                temperature=0.1,
            )
            result = parse_json_object(text)
            score = int(result.get("total_score", 0))
            result["total_score"] = max(0, min(100, score))
            result["passed"] = story_evaluation_passed(result)
            result["judge_model"] = judge_model
            evaluations.append(result)
            print(f"    Score Judge {judge_model} → {result['total_score']}: {result.get('reason', '')}")
        except Exception as e:
            print(f"    Score Judge {judge_model} 실패: {e}")

    if not evaluations:
        return {
            "total_score": 0,
            "tier": "Weak",
            "reason": "No configured judge model returned a valid score.",
            "judge_model": None,
            "evaluations": [],
        }

    best = max(evaluations, key=lambda item: item.get("total_score", 0))
    best_result = dict(best)
    best_result["evaluations"] = [dict(item) for item in evaluations]
    return best_result


def story_evaluation_passed(evaluation: dict, min_score: int = 80) -> bool:
    scores = evaluation.get("category_scores") or {}
    total_score = int(evaluation.get("total_score", 0))
    if total_score < min_score:
        return False

    required_criteria = [
        "story_structure_completeness",
        "emotional_progression_clarity",
        "character_growth",
        "child_emotional_relatability",
        "emotional_warmth",
        "readability",
        "sentence_simplicity",
        "repetition_effectiveness",
        "read_aloud_quality",
        "dialogue_naturalness",
        "visual_scene_clarity",
        "illustration_friendliness",
        "description_quiz_compatibility",
        "roleplay_compatibility",
        "emotional_safety",
        "creativity",
        "memorability",
        "theme_consistency",
        "educational_suitability",
        "award_level_literary_feeling",
    ]
    if any(int(scores.get(key, 0)) < 3 for key in required_criteria):
        return False

    critical_criteria = [
        "emotional_safety",
        "readability",
        "visual_scene_clarity",
        "roleplay_compatibility",
    ]
    if any(int(scores.get(key, 0)) < 4 for key in critical_criteria):
        return False

    automatic_fail = [
        "emotional_safety",
        "readability",
        "visual_scene_clarity",
    ]
    return not any(int(scores.get(key, 0)) <= 2 for key in automatic_fail)


# ─── LLM 품질 평가 (Claude Haiku) ────────────────────────────

def judge_story_quality(stories: list[tuple[str, str]]) -> int:
    """
    stories: [(model_id, story_text), ...]
    Returns: index of best story
    """
    numbered = "\n\n".join(
        f"[Story {i+1} by {mid}]\n{text}" for i, (mid, text) in enumerate(stories) if text
    )
    prompt = STORY_JUDGE_PROMPT.format(numbered_stories=numbered)

    votes: list[int] = []
    for judge_model in MODELS.story_judge_models:
        try:
            text = generate_text(
                [{"role": "user", "content": prompt}],
                model=judge_model,
                max_tokens=200,
                temperature=0.1,
            )
            result = parse_json_object(text)
            best = int(result["best"]) - 1
            if 0 <= best < len(stories):
                votes.append(best)
                print(f"    Judge {judge_model} → Story {best + 1}: {result.get('reason', '')}")
        except Exception as e:
            print(f"    Judge {judge_model} 실패: {e}")

    if votes:
        return max(set(votes), key=votes.count)

    return 0


# ─── 이미지 프롬프트 생성 ─────────────────────────────────────

def generate_image_prompts(sentences: list[str], protagonist: str) -> list[str]:
    """각 문장에서 이미지 생성용 프롬프트 추출"""
    joined = "\n".join(f"{i+1}. {s}" for i, s in enumerate(sentences))
    prompt = f"""Convert each fairy tale sentence into a short image generation prompt.

Use this exact art direction for EVERY page so the full story keeps one unified style:
{IMAGE_STYLE_GUIDE}

Character consistency rule:
- Protagonist: {protagonist}
- Keep the protagonist's species, face shape, colors, outfit/accessories, and proportions identical on every page.
- If a recurring side character appears, keep that character identical too.
- Do not change illustration medium or rendering style between pages.

Sentences:
{joined}

Output ONLY a JSON array of strings, one prompt per sentence.
Each prompt must include the fixed style phrase, the protagonist consistency note, and the scene content.
Example: ["Bright 2D mobile children's storybook app illustration, same cute rabbit protagonist, ...", ...]"""

    try:
        text = generate_text([{"role": "user", "content": prompt}], max_tokens=800)
        text = re.sub(r"```json|```", "", text).strip()
        return json.loads(text)
    except Exception:
        return [
            (
                "Bright 2D mobile children's storybook app illustration, "
                f"same protagonist ({protagonist}), consistent character design, "
                f"scene: {s[:120]}"
            )
            for s in sentences
        ]


# ─── 묘사 퀴즈 자동 생성 ─────────────────────────────────────

def generate_description_scenes(
    sentences: list[str],
    level: int,
    image_paths: list[str],
    image_prompts: Optional[list[str]] = None,
    story_title: str = "",
) -> list[DescriptionScene]:
    cfg = LEVEL_CONFIGS[level]
    n = cfg.description_scenes
    desc_type = {1: DescriptionType.WORD_GUESS, 2: DescriptionType.SENTENCE, 3: DescriptionType.REASON}[level]
    image_prompts = image_prompts or ["" for _ in sentences]

    story_pages = "\n".join(
        (
            f"Page {i + 1}\n"
            f"Story sentence: {sentence}\n"
            f"Illustration description: {image_prompts[i] if i < len(image_prompts) else ''}"
        )
        for i, sentence in enumerate(sentences)
    )
    ar_level = {1: "0.1-0.9", 2: "0.9-1.8", 3: "1.8-2.5"}[level]
    prompt = DESCRIPTION_QUIZ_PROMPT.format(
        quiz_count=n,
        story_title=story_title or "Untitled Story",
        ar_level=ar_level,
        level=level,
        story_pages=story_pages,
    )

    try:
        text = generate_text(
            [{"role": "user", "content": prompt}],
            max_tokens=1400,
            temperature=0.2,
        )
        data = parse_json_object(text)
        scenes = []
        for i, quiz in enumerate(data.get("quizzes", [])[:n]):
            scene_number = int(quiz.get("scene_number") or i + 1)
            scene_idx = max(0, min(len(sentences) - 1, scene_number - 1))
            expected_answer = quiz.get("expected_answer") or sentences[scene_idx]
            keywords = quiz.get("keywords_for_evaluation") or []
            blank_word = keywords[0] if desc_type == DescriptionType.WORD_GUESS and keywords else None
            scenes.append(DescriptionScene(
                scene_number=i + 1,
                image_path=image_paths[scene_idx] if scene_idx < len(image_paths) else "",
                desc_type=desc_type,
                blank_word=blank_word,
                answer_sentence=expected_answer,
                guide_hint=quiz.get("hint_1") or expected_answer[:len(expected_answer)//2],
            ))
        if len(scenes) == n:
            return scenes
    except Exception as e:
        print(f"묘사 퀴즈 생성 실패: {e}")

    # fallback: 묘사에 적합한 장면 선택 (가운데 문장들 우선)
    mid = len(sentences) // 2
    indices = list(range(max(0, mid - n//2), min(len(sentences), mid - n//2 + n)))
    selected = [(indices[i], sentences[indices[i]]) for i in range(len(indices))]

    type_instruction = {
        DescriptionType.WORD_GUESS: """Create a fill-in-the-blank exercise. Pick one key word (color, animal, or object) from the sentence.
Return JSON: {"blank_word": "...", "answer_sentence": "The ___ is/has ...", "guide_hint": "The ___ is"}""",
        DescriptionType.SENTENCE: """Create a one-sentence scene description task.
Return JSON: {"answer_sentence": "The boy is running.", "guide_hint": "The boy is"}""",
        DescriptionType.REASON: """Create a scene description + reason task.
Return JSON: {"answer_sentence": "She looks happy because she found the treasure.", "guide_hint": "She looks happy because"}""",
    }[desc_type]

    scenes = []
    for i, (idx, sentence) in enumerate(selected):
        prompt = f"""Given this fairy tale sentence: "{sentence}"
{type_instruction}
Make it simple for young English learners."""

        try:
            text = generate_text([{"role": "user", "content": prompt}], max_tokens=300)
            text = re.sub(r"```json|```", "", text).strip()
            data = json.loads(text)
            scenes.append(DescriptionScene(
                scene_number=i + 1,
                image_path=image_paths[idx] if idx < len(image_paths) else "",
                desc_type=desc_type,
                blank_word=data.get("blank_word"),
                answer_sentence=data.get("answer_sentence", sentence),
                guide_hint=data.get("guide_hint", ""),
            ))
        except Exception:
            scenes.append(DescriptionScene(
                scene_number=i + 1,
                image_path=image_paths[idx] if idx < len(image_paths) else "",
                desc_type=desc_type,
                answer_sentence=sentence,
                guide_hint=sentence[:len(sentence)//2],
            ))
    return scenes


# ─── 롤플레잉 시나리오 생성 ──────────────────────────────────

def generate_roleplay_scenarios(
    sentences: list[str],
    level: int,
    protagonist: str,
    story_title: str = "",
) -> list[RoleplayScenario]:
    cfg = LEVEL_CONFIGS[level]
    topic_map = {1: RoleplayTopic.INTRO, 2: RoleplayTopic.DIRECTION, 3: RoleplayTopic.ESCAPE}
    char_map  = {1: "a dwarf", 2: "a hunter", 3: "the ball host"}
    story_pages = "\n".join(
        f"Page {i + 1}: {sentence}" for i, sentence in enumerate(sentences)
    )
    prompt = ROLEPLAY_MISSION_PROMPT.format(
        mission_count=cfg.roleplay_count,
        story_title=story_title or "Untitled Story",
        level=level,
        protagonist=protagonist,
        story_pages=story_pages,
    )

    try:
        text = generate_text(
            [{"role": "user", "content": prompt}],
            max_tokens=1200,
            temperature=0.2,
        )
        data = parse_json_object(text)
        scenarios = []
        for i, mission in enumerate(data.get("missions", [])[:cfg.roleplay_count]):
            example_answers = mission.get("example_correct_answers") or []
            alternative_answers = mission.get("acceptable_alternative_answers") or []
            model_answer = (
                example_answers[0]
                if example_answers
                else mission.get("expected_intent")
                or mission.get("pass_condition")
                or ""
            )
            hints = [
                mission.get("hint_1", ""),
                mission.get("hint_2", ""),
                mission.get("hint_3", ""),
            ]
            scenarios.append(RoleplayScenario(
                scenario_id=f"rp_{level}_{i+1}",
                topic=topic_map[level],
                level=level,
                scene_description=mission.get("situation_summary", ""),
                character_name=char_map[level],
                player_goal=mission.get("mission_goal", ""),
                model_answer=model_answer,
                hint_sequence=[h for h in hints if h],
            ))
        if len(scenarios) == cfg.roleplay_count:
            return scenarios
    except Exception as e:
        print(f"롤플레잉 생성 실패: {e}")

    goal_map  = {
        1: "Introduce yourself to the character in a kind way",
        2: "Ask for help or directions politely",
        3: "Help solve the story problem with a brave idea",
    }
    return [
        RoleplayScenario(
            scenario_id=f"rp_{level}_1",
            topic=topic_map[level],
            level=level,
            scene_description="A friendly character needs kind words from the child.",
            character_name=char_map[level],
            player_goal=goal_map[level],
            model_answer="I can help you.",
            hint_sequence=["Try saying something kind.", "Offer help.", "Say, 'I can help you.'"],
        )
    ]


# ─── 메인 파이프라인 ──────────────────────────────────────────

async def generate_lesson(
    book_id: str,
    episode: int,
    level: int,
    age: int,
    theme: str,
    protagonist: str,
    generate_images: bool = False,
    max_retries: int = 3,
    total_episodes: int = 1,
    continuity_context: str = "This is episode 1. Start the longer story gently.",
) -> Optional[Lesson]:
    """
    전체 콘텐츠 제작 파이프라인 실행
    """
    print(f"\n{'='*50}")
    print(f"[콘텐츠 생성] book={book_id} ep={episode} level={level}")

    prompt = build_story_prompt(
        age,
        level,
        theme,
        protagonist,
        episode=episode,
        total_episodes=total_episodes,
        continuity_context=continuity_context,
    )

    # ① Llama로 동화 텍스트 생성
    print(f"  ① 동화 텍스트 생성 ({MODELS.story_model})...")
    local_result = call_local_story_model(prompt)
    candidates = [(MODELS.story_model, local_result)] if local_result else []

    if not candidates:
        print("  ✗ 로컬 Llama 동화 생성 실패")
        return None

    # ② LLM 품질 평가 → 최고 채택
    print(f"  ② 품질 평가 ({len(candidates)}개 후보)...")
    best_idx = judge_story_quality(candidates) if len(candidates) > 1 else 0
    best_model, best_text = candidates[best_idx]
    print(f"  → 채택: {best_model}")

    # ③ 후처리 검사 (최대 max_retries회 단일 모델 재시도)
    print("  ③ 후처리 검사...")
    passed = False
    sentences = []
    for attempt in range(max_retries):
        ok, reason, sentences = post_process_check(best_text, level)
        if ok:
            passed = True
            break
        print(f"    재시도 {attempt+1}: {reason}")
        best_text = repair_story_output(
            best_text,
            age=age,
            level=level,
            theme=theme,
            protagonist=protagonist,
            episode=episode,
            total_episodes=total_episodes,
            continuity_context=continuity_context,
        )
        if not best_text:
            break

    if not passed:
        print("  ✗ 후처리 검사 최종 실패")
        return None

    print(f"  ✓ 동화 생성 완료 ({len(sentences)}문장)")

    # ④ 이미지 프롬프트 생성
    print("  ④ 이미지 프롬프트 생성...")
    image_prompts = generate_image_prompts(sentences, protagonist)

    pages = [
        StoryPage(
            page_number=i + 1,
            text=s,
            image_prompt=image_prompts[i] if i < len(image_prompts) else "",
        )
        for i, s in enumerate(sentences)
    ]
    image_paths = (
        generate_story_images(pages, book_id=book_id, episode=episode)
        if generate_images
        else attach_planned_image_paths(pages, book_id=book_id, episode=episode)
    )

    # ⑤ 묘사 퀴즈 생성
    print("  ⑤ 묘사 퀴즈 생성...")
    desc_scenes = generate_description_scenes(
        sentences,
        level,
        image_paths,
        image_prompts=image_prompts,
    )

    # ⑥ 롤플레잉 시나리오 생성
    print("  ⑥ 롤플레잉 시나리오 생성...")
    roleplay = generate_roleplay_scenarios(sentences, level, protagonist)

    lesson = Lesson(
        lesson_id=f"{book_id}_ep{episode}_lv{level}",
        book_id=book_id,
        level=level,
        episode=episode,
        pages=pages,
        description_scenes=desc_scenes,
        roleplay_scenarios=roleplay,
    )

    print(f"  ✓ 강의 패키지 완성: {lesson.lesson_id}")
    return lesson


async def generate_lesson_if_quality_passes(
    book_id: str,
    episode: int,
    level: int,
    age: int,
    theme: str,
    protagonist: str,
    min_score: int = 80,
    generate_images: bool = False,
    max_retries: int = 3,
    quality_retries: int = 3,
    total_episodes: int = 1,
    continuity_context: str = "This is episode 1. Start the longer story gently.",
) -> tuple[Optional[Lesson], dict]:
    print(f"\n{'='*50}")
    print(f"[품질 필터 생성] theme={theme} ep={episode} min_score={min_score}")

    prompt = build_story_prompt(
        age,
        level,
        theme,
        protagonist,
        episode=episode,
        total_episodes=total_episodes,
        continuity_context=continuity_context,
    )
    best_failure = {
        "theme": theme,
        "accepted": False,
        "score": 0,
        "reason": "Story generation failed.",
    }

    for quality_attempt in range(1, quality_retries + 1):
        best_text = ""
        sentences = []
        last_reason = "Story generation failed."

        for attempt in range(1, max_retries + 1):
            best_text = call_local_story_model(prompt)
            if not best_text:
                last_reason = "Story generation failed."
                continue

            ok, reason, sentences = post_process_check(best_text, level)
            if ok:
                break
            last_reason = reason
            print(f"  형식 재시도 {attempt}/{max_retries}: {reason}")
            best_text = repair_story_output(
                best_text,
                age=age,
                level=level,
                theme=theme,
                protagonist=protagonist,
                episode=episode,
                total_episodes=total_episodes,
                continuity_context=continuity_context,
            )
            ok, reason, sentences = post_process_check(best_text, level)
            if ok:
                break
            last_reason = reason
            print(f"  수리 재시도 {attempt}/{max_retries}: {reason}")
        else:
            best_failure = {
                "theme": theme,
                "accepted": False,
                "score": 0,
                "reason": f"Post-process failed: {last_reason}",
                "raw_story": best_text,
                "extracted_sentences": sentences,
            }
            print(f"  품질 재생성 {quality_attempt}/{quality_retries}: {last_reason}")
            continue

        story_text = story_text_from_sentences(sentences)
        evaluation = evaluate_story_score(story_text)
        score = int(evaluation.get("total_score", 0))

        passed = bool(evaluation.get("passed")) and story_evaluation_passed(evaluation, min_score)
        if passed:
            break

        best_failure = {
            "theme": theme,
            "accepted": False,
            "score": score,
            "reason": evaluation.get("reason", "Score below threshold."),
            "evaluation": evaluation,
            "story_sentences": sentences,
        }
        print(f"  품질 재생성 {quality_attempt}/{quality_retries}: {score}/{min_score}")
    else:
        return best_failure and (None, best_failure)

    print(f"  ✓ 품질 통과: {score}/{min_score}")

    image_prompts = generate_image_prompts(sentences, protagonist)
    pages = [
        StoryPage(
            page_number=i + 1,
            text=s,
            image_prompt=image_prompts[i] if i < len(image_prompts) else "",
        )
        for i, s in enumerate(sentences)
    ]
    image_paths = (
        generate_story_images(pages, book_id=book_id, episode=episode)
        if generate_images
        else attach_planned_image_paths(pages, book_id=book_id, episode=episode)
    )
    desc_scenes = generate_description_scenes(
        sentences,
        level,
        image_paths,
        image_prompts=image_prompts,
    )
    roleplay = generate_roleplay_scenarios(sentences, level, protagonist)

    lesson = Lesson(
        lesson_id=f"{book_id}_ep{episode}_lv{level}",
        book_id=book_id,
        level=level,
        episode=episode,
        pages=pages,
        description_scenes=desc_scenes,
        roleplay_scenarios=roleplay,
    )

    return lesson, {
        "theme": theme,
        "accepted": True,
        "score": score,
        "reason": evaluation.get("reason", ""),
        "evaluation": evaluation,
    }


# ─── TTS 생성 ─────────────────────────────────────────────────

def generate_tts_for_lesson(lesson: Lesson, output_dir: str = "audio") -> Lesson:
    """ElevenLabs TTS로 각 페이지 음성 생성"""
    import requests
    import os

    os.makedirs(output_dir, exist_ok=True)
    ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")

    for page in lesson.pages:
        url = f"https://api.elevenlabs.io/v1/text-to-speech/{MODELS.elevenlabs_voice_id}"
        headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
        payload = {
            "text": page.text,
            "model_id": "eleven_turbo_v2",
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}
        }
        resp = requests.post(url, headers=headers, json=payload)
        if resp.status_code == 200:
            path = f"{output_dir}/{lesson.lesson_id}_p{page.page_number}.mp3"
            with open(path, "wb") as f:
                f.write(resp.content)
            page.audio_path = path
            print(f"  TTS 저장: {path}")
        else:
            print(f"  TTS 실패 p{page.page_number}: {resp.status_code}")

    return lesson


if __name__ == "__main__":
    from scripts.generate_lessons import main

    main()
