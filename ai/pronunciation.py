"""
따라말하기 채점 모듈
- Whisper STT → 텍스트 변환
- 단어별 유사도(SequenceMatcher) → 발음 점수 산출
- 억양/강세 분석 제외 (기획 결정)
"""

import re
import io
import difflib
import tempfile
import os
from dataclasses import dataclass
from typing import Optional

from shared.settings import MODELS, LEVEL_CONFIGS
from shared.models import PronunciationResult


# ─── Whisper STT ─────────────────────────────────────────────

def transcribe_audio(audio_bytes: bytes, language: str = "en") -> str:
    """
    Whisper large-v3 로 STT 수행
    
    배포 옵션:
    A) HuggingFace Inference API (무료, 느림)
    B) faster-whisper 로컬 (빠름, GPU 권장)
    C) OpenAI Whisper API (유료)
    
    여기서는 faster-whisper 로컬 방식 구현
    (pip install faster-whisper)
    """
    try:
        from faster_whisper import WhisperModel
        
        # 모델 초기화 (첫 실행 시 다운로드)
        # compute_type: GPU="float16", CPU="int8"
        model = WhisperModel("large-v3", device="cpu", compute_type="int8")
        
        # bytes → 임시 파일
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            f.write(audio_bytes)
            tmp_path = f.name
        
        try:
            segments, info = model.transcribe(tmp_path, language=language, beam_size=5)
            transcript = " ".join(seg.text.strip() for seg in segments)
            return transcript.strip()
        finally:
            os.unlink(tmp_path)

    except ImportError:
        # fallback: HuggingFace Inference API
        return _transcribe_via_hf_api(audio_bytes)


def _transcribe_via_hf_api(audio_bytes: bytes) -> str:
    """HuggingFace Inference API fallback (무료 tier)"""
    import requests
    from shared.settings import HF_TOKEN, MODELS
    
    url = f"https://api-inference.huggingface.co/models/{MODELS.whisper_model}"
    headers = {"Authorization": f"Bearer {HF_TOKEN}"}
    resp = requests.post(url, headers=headers, data=audio_bytes, timeout=30)
    if resp.status_code == 200:
        return resp.json().get("text", "").strip()
    return ""


# ─── 텍스트 정규화 ────────────────────────────────────────────

def normalize_text(text: str) -> str:
    """발음 비교를 위한 텍스트 정규화"""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s']", "", text)       # 구두점 제거 (apostrophe 유지)
    text = re.sub(r"\s+", " ", text)            # 공백 정규화
    return text


# ─── 발음 점수 계산 ───────────────────────────────────────────

def compute_word_scores(reference: str, hypothesis: str) -> dict[str, float]:
    """
    단어별 유사도 계산
    SequenceMatcher로 단어 정렬 후 각 단어 쌍의 문자 유사도 산출
    """
    ref_words  = normalize_text(reference).split()
    hyp_words  = normalize_text(hypothesis).split()

    # 단어 시퀀스 매칭
    matcher = difflib.SequenceMatcher(None, ref_words, hyp_words)
    word_scores: dict[str, float] = {}

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for w in ref_words[i1:i2]:
                word_scores[w] = 1.0
        elif tag == "replace":
            for ref_w, hyp_w in zip(ref_words[i1:i2], hyp_words[j1:j2]):
                # 문자 단위 유사도
                char_sim = difflib.SequenceMatcher(None, ref_w, hyp_w).ratio()
                word_scores[ref_w] = char_sim
            # 남은 ref 단어 (replace 길이 불일치)
            for ref_w in ref_words[i2 - (i2-i1) + len(hyp_words[j1:j2]):i2]:
                word_scores[ref_w] = 0.0
        elif tag == "delete":
            for w in ref_words[i1:i2]:
                word_scores[w] = 0.0
        # "insert"는 ref에 없는 단어 → 점수 영향 없음

    return word_scores


def compute_pronunciation_score(reference: str, hypothesis: str) -> int:
    """
    전체 발음 점수 (0~100)
    = 단어별 유사도 가중 평균
    """
    if not hypothesis.strip():
        return 0

    word_scores = compute_word_scores(reference, hypothesis)
    if not word_scores:
        return 0

    score = sum(word_scores.values()) / len(word_scores) * 100
    return round(score)


# ─── 메인 채점 함수 ───────────────────────────────────────────

def evaluate_pronunciation(
    target_sentence: str,
    audio_bytes: bytes,
    level: int,
) -> PronunciationResult:
    """
    따라말하기 평가 메인 함수
    
    Args:
        target_sentence: 정답 문장
        audio_bytes: 녹음된 오디오 (WAV/MP3)
        level: 난이도 (1/2/3)
    
    Returns:
        PronunciationResult
    """
    cfg = LEVEL_CONFIGS[level]

    # STT
    transcribed = transcribe_audio(audio_bytes)
    print(f"  [STT] 원문: '{target_sentence}'")
    print(f"  [STT] 인식: '{transcribed}'")

    # 점수 계산
    word_scores = compute_word_scores(target_sentence, transcribed)
    score = compute_pronunciation_score(target_sentence, transcribed)
    passed = score >= cfg.pronunciation_cutoff

    print(f"  [채점] 점수: {score} / 커트라인: {cfg.pronunciation_cutoff} → {'✓ 통과' if passed else '✗ 실패'}")

    return PronunciationResult(
        sentence=target_sentence,
        transcribed=transcribed,
        score=score,
        passed=passed,
        word_scores=word_scores,
    )


# ─── 따라말하기 세션 전체 처리 ────────────────────────────────

def run_repeat_session(
    sentences: list[str],
    audio_bytes_list: list[bytes],
    level: int,
) -> list[PronunciationResult]:
    """
    난이도별 문장 수만큼 채점 수행
    
    Args:
        sentences: 정답 문장 리스트
        audio_bytes_list: 각 문장에 대한 녹음 리스트
        level: 난이도
    
    Returns:
        채점 결과 리스트
    """
    cfg = LEVEL_CONFIGS[level]
    n = cfg.repeat_sentences

    results = []
    for i in range(min(n, len(sentences), len(audio_bytes_list))):
        print(f"\n  [문장 {i+1}/{n}]")
        result = evaluate_pronunciation(sentences[i], audio_bytes_list[i], level)
        results.append(result)

    return results


# ─── 테스트용 더미 오디오 생성 ────────────────────────────────

def create_test_audio_from_text(text: str) -> bytes:
    """
    테스트용: edge-tts로 텍스트를 음성으로 변환 (실제 환경에서는 사용자 녹음 사용)
    pip install edge-tts
    """
    import asyncio
    import edge_tts

    async def _generate():
        communicate = edge_tts.Communicate(text, "en-US-JennyNeural")
        audio_chunks = []
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_chunks.append(chunk["data"])
        return b"".join(audio_chunks)

    return asyncio.run(_generate())
