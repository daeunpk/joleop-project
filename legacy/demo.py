"""
전체 파이프라인 E2E 데모
실제 API 키 없이 각 단계의 로직을 확인하는 mock 데모
"""

import sys, os, json, difflib, re
sys.path.append(os.path.dirname(os.path.abspath(__file__)))


# ─── MOCK 버전 (API 키 없이 실행 가능) ───────────────────────

print("=" * 60)
print("  동화 영어 학습 파이프라인 데모")
print("=" * 60)


# ── 1. 후처리 검사 데모 ───────────────────────────────────────
print("\n[1] 후처리 검사 데모")

def mock_post_process_check(text: str, level: int) -> tuple[bool, str, list[str]]:
    max_words = {1: 10, 2: 14, 3: 18}[level]
    cfg_pages = {1: 5, 2: 7, 3: 10}[level]

    lines = [re.sub(r"^\d+\.\s*", "", l.strip())
             for l in text.split("\n") if re.match(r"^\d+\.", l.strip())]

    if len(lines) != cfg_pages:
        return False, f"문장 수 불일치: {len(lines)} (필요: {cfg_pages})", lines

    for i, sent in enumerate(lines):
        wc = len(sent.split())
        if wc > max_words:
            return False, f"문장 {i+1} 너무 김: {wc}단어 (최대 {max_words})", lines

    return True, "ok", lines

sample_story_lv1 = """1. A little rabbit found a shiny apple.
2. He ran home to share it with his friends.
3. The fox tried to take the apple away.
4. But the rabbit was clever and hid it well.
5. Everyone ate together and smiled happily."""

ok, reason, sentences = mock_post_process_check(sample_story_lv1, level=1)
print(f"  입력: {len(sentences)}문장, 레벨 1")
print(f"  결과: {'✓ 통과' if ok else '✗ 실패'} — {reason}")
for i, s in enumerate(sentences):
    print(f"    {i+1}. [{len(s.split())}단어] {s}")


# ── 2. 발음 채점 데모 ─────────────────────────────────────────
print("\n[2] 발음 채점 데모 (STT 없이 텍스트로 시뮬레이션)")

def mock_pronunciation_score(reference: str, hypothesis: str) -> dict:
    ref_words = reference.lower().strip().split()
    hyp_words = hypothesis.lower().strip().split()

    matcher = difflib.SequenceMatcher(None, ref_words, hyp_words)
    word_scores = {}

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for w in ref_words[i1:i2]:
                word_scores[w] = 1.0
        elif tag == "replace":
            for rw, hw in zip(ref_words[i1:i2], hyp_words[j1:j2]):
                word_scores[rw] = difflib.SequenceMatcher(None, rw, hw).ratio()
            for rw in ref_words[i1 + len(hyp_words[j1:j2]):i2]:
                word_scores[rw] = 0.0
        elif tag == "delete":
            for w in ref_words[i1:i2]:
                word_scores[w] = 0.0

    score = round(sum(word_scores.values()) / len(word_scores) * 100) if word_scores else 0
    return {"score": score, "word_scores": word_scores}

test_cases = [
    ("A little rabbit found a shiny apple", "A little rabbit found a shiny apple", 1),   # 완벽
    ("A little rabbit found a shiny apple", "A little rabbit found shiny apple", 1),     # 단어 누락
    ("He ran home to share it with his friends", "He ran home share it with his friend", 2),  # 일부 오류
]

cutoffs = {1: 50, 2: 70, 3: 80}
for ref, hyp, level in test_cases:
    result = mock_pronunciation_score(ref, hyp)
    passed = result["score"] >= cutoffs[level]
    print(f"  정답: '{ref}'")
    print(f"  발화: '{hyp}'")
    print(f"  점수: {result['score']} / 커트라인: {cutoffs[level]} → {'✓ 통과' if passed else '✗ 실패'}")
    low = [w for w, s in result["word_scores"].items() if s < 0.8]
    if low:
        print(f"  낮은 점수 단어: {low}")
    print()


# ── 3. 묘사 채점 데모 (룰베이스) ─────────────────────────────
print("[3] 묘사 채점 데모 (룰베이스 부분)")

def mock_level1_description(blank_word: str, answer_sentence: str, user_said: str) -> dict:
    hyp = user_said.lower()
    blank = blank_word.lower()

    word_found = blank in hyp.split() or any(
        difflib.SequenceMatcher(None, blank, w).ratio() > 0.85 for w in hyp.split()
    )
    ratio = difflib.SequenceMatcher(None, answer_sentence.lower().split(), hyp.split()).ratio()
    complete = ratio >= 0.70

    if not word_found:
        return {"passed": False, "feedback": f"'{blank_word}' 단어가 없어요.", "ratio": ratio}
    if not complete:
        return {"passed": False, "feedback": f"문장을 전부 말해주세요. ({int(ratio*100)}% 완성)", "ratio": ratio}
    return {"passed": True, "feedback": "정답입니다!", "ratio": ratio}

desc_tests = [
    ("red", "The apple is red and round.", "The apple is red and round."),
    ("red", "The apple is red and round.", "The apple is red."),       # 문장 불완전
    ("red", "The apple is red and round.", "The apple is blue and round."),  # 틀린 단어
]
for blank, answer, user_said in desc_tests:
    r = mock_level1_description(blank, answer, user_said)
    print(f"  빈칸: '{blank}' | 발화: '{user_said}'")
    print(f"  → {'✓ 통과' if r['passed'] else '✗ 실패'}: {r['feedback']}")
print()


# ── 4. 롤플레잉 판단 로직 데모 ───────────────────────────────
print("[4] 롤플레잉 판단 로직 데모")

def mock_keyword_judge(model_answer: str, player_goal: str, user_said: str) -> dict:
    """LLM 없이 키워드 기반 간이 판단 (데모용)"""
    key_words = set(model_answer.lower().split()) - {"a", "the", "is", "am", "i", "my", "to"}
    user_words = set(user_said.lower().split())
    overlap = len(key_words & user_words) / max(len(key_words), 1)
    passed = overlap >= 0.5
    return {
        "passed": passed,
        "overlap_ratio": round(overlap, 2),
        "matched_words": list(key_words & user_words),
    }

roleplay_tests = [
    {
        "goal": "자기소개하기",
        "model_answer": "Hi! My name is Alice and I am seven years old.",
        "user_said": "Hello! My name is Alice. I am seven.",
    },
    {
        "goal": "자기소개하기",
        "model_answer": "Hi! My name is Alice and I am seven years old.",
        "user_said": "I like cookies.",
    },
]
for t in roleplay_tests:
    r = mock_keyword_judge(t["model_answer"], t["goal"], t["user_said"])
    print(f"  목표: {t['goal']}")
    print(f"  모범: '{t['model_answer']}'")
    print(f"  발화: '{t['user_said']}'")
    print(f"  → {'✓ 패스' if r['passed'] else '✗ 계속'} (키워드 겹침: {r['overlap_ratio']} | {r['matched_words']})")
    print()


# ── 5. 전체 구조 요약 ─────────────────────────────────────────
print("=" * 60)
print("  파일 구조")
print("=" * 60)

structure = {
    "config/settings.py":            "모델 설정 / 난이도별 파라미터",
    "shared/models.py":              "공통 데이터 클래스",
    "content_pipeline/story_generator.py": "동화 생성 파이프라인 (HF + Claude Haiku)",
    "learning_service/pronunciation.py":   "따라말하기 채점 (Whisper + SequenceMatcher)",
    "learning_service/description.py":     "묘사 채점 (룰베이스 + Claude Haiku)",
    "learning_service/roleplay.py":        "롤플레잉 (Claude Haiku 캐릭터 + 판단)",
    "main.py":                        "FastAPI 서버 (REST + WebSocket)",
}
for path, desc in structure.items():
    print(f"  {path:<45} {desc}")

print("\n  사용 모델:")
models = [
    ("동화 생성 후보",    "google/gemma-3-12b-it, Mistral-7B (HuggingFace 무료)"),
    ("품질 평가 / 채점", "claude-haiku-4-5 (빠름·저렴)"),
    ("STT",             "openai/whisper-large-v3 (faster-whisper 로컬)"),
    ("TTS",             "ElevenLabs eleven_turbo_v2"),
    ("롤플레잉 AI",     "claude-haiku-4-5 (캐릭터 + 정답 판단)"),
]
for role, model in models:
    print(f"  {role:<20} → {model}")

print("\n  실행 방법:")
print("  pip install fastapi uvicorn anthropic aiohttp faster-whisper edge-tts")
print("  export ANTHROPIC_API_KEY=sk-ant-...")
print("  export HF_TOKEN=hf_...")
print("  export ELEVENLABS_API_KEY=...")
print("  uvicorn main:app --reload")
print()
