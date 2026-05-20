"""
FastAPI 메인 서버
- 콘텐츠 제작 API
- 학습 서비스 API (따라말하기 / 묘사 / 롤플레잉)
- WebSocket (롤플레잉 실시간)

실행: uvicorn backend.main:app --reload
"""

import asyncio
import json
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from backend.database import SessionLocal, init_db
from backend.repositories import (
    get_lesson as db_get_lesson,
    get_roleplay_scenario as db_get_roleplay_scenario,
    save_lesson as db_save_lesson,
)
from backend.serializers import lesson_to_dict
from shared.settings import LEVEL_CONFIGS
from shared.models import Lesson, RoleplayScenario
from ai.story_generator import (
    generate_lesson,
    generate_lesson_if_quality_passes,
    generate_tts_for_lesson,
)
from ai.pronunciation import run_repeat_session, evaluate_pronunciation
from ai.description import run_description_session, evaluate_description
from ai.roleplay import (
    RoleplaySession, process_roleplay_turn,
    check_silence, _get_opening_line, RoleplayWebSocketHandler
)

app = FastAPI(title="동화 영어 학습 API", version="1.0.0")

# 간단한 인메모리 저장소 (실제 서비스에서는 DB 사용)
lesson_store: dict[str, dict] = {}
scenario_store: dict[str, RoleplayScenario] = {}


@app.on_event("startup")
async def startup() -> None:
    await init_db()


# ──────────────────────────────────────────────────────────────
# 콘텐츠 제작 API
# ──────────────────────────────────────────────────────────────

class LessonCreateRequest(BaseModel):
    book_id: str
    episode: int
    level: int          # 1, 2, 3
    age: int            # 학습자 나이
    theme: str          # 동화 주제 (예: "friendship", "bravery")
    protagonist: str    # 주인공 설명 (예: "a brave little rabbit")
    generate_images: bool = False
    generate_tts: bool = False


class LessonBatchCreateRequest(BaseModel):
    book_id: str
    start_episode: int = 1
    level: int
    age: int
    themes: list[str]
    protagonist: str
    min_score: int = 80
    generate_images: bool = False
    generate_tts: bool = False


@app.post("/api/content/lesson")
async def create_lesson(req: LessonCreateRequest):
    """동화 강의 콘텐츠 생성 (텍스트 + 퀴즈 + 롤플레잉)"""
    if req.level not in [1, 2, 3]:
        raise HTTPException(400, "level은 1, 2, 3 중 하나여야 합니다.")

    lesson = await generate_lesson(
        book_id=req.book_id,
        episode=req.episode,
        level=req.level,
        age=req.age,
        theme=req.theme,
        protagonist=req.protagonist,
        generate_images=req.generate_images,
    )

    if not lesson:
        raise HTTPException(500, "콘텐츠 생성에 실패했습니다. 다시 시도해주세요.")

    # TTS 생성 (선택)
    if req.generate_tts:
        lesson = generate_tts_for_lesson(lesson)

    lesson_data = lesson_to_dict(lesson)
    await _save_lesson(lesson_data, lesson.roleplay_scenarios)

    return lesson_data


@app.get("/api/content/lesson/{lesson_id}")
async def get_lesson(lesson_id: str):
    """저장된 강의 조회"""
    lesson_data = await _get_lesson(lesson_id)
    if not lesson_data:
        raise HTTPException(404, "강의를 찾을 수 없습니다.")
    return lesson_data


@app.post("/api/content/lessons/batch")
async def create_lessons_batch(req: LessonBatchCreateRequest):
    """테마 목록을 순서대로 생성하고, judge 점수 통과분만 저장/출력."""
    if req.level not in [1, 2, 3]:
        raise HTTPException(400, "level은 1, 2, 3 중 하나여야 합니다.")
    if not req.themes:
        raise HTTPException(400, "themes는 최소 1개 이상이어야 합니다.")

    accepted = []
    rejected = []
    next_episode = req.start_episode

    for theme in req.themes:
        lesson, quality = await generate_lesson_if_quality_passes(
            book_id=req.book_id,
            episode=next_episode,
            level=req.level,
            age=req.age,
            theme=theme,
            protagonist=req.protagonist,
            min_score=req.min_score,
            generate_images=req.generate_images,
        )

        if not lesson:
            rejected.append(quality)
            continue

        if req.generate_tts:
            lesson = generate_tts_for_lesson(lesson)

        lesson_data = lesson_to_dict(lesson)
        lesson_data["theme"] = theme
        lesson_data["quality_score"] = quality["score"]
        lesson_data["quality_reason"] = quality.get("reason", "")
        await _save_lesson(lesson_data, lesson.roleplay_scenarios)

        accepted.append({
            "theme": theme,
            "score": quality["score"],
            "reason": quality.get("reason", ""),
            "lesson": lesson_data,
            "evaluation": quality.get("evaluation", {}),
        })
        next_episode += 1

    return {
        "book_id": req.book_id,
        "level": req.level,
        "min_score": req.min_score,
        "accepted_count": len(accepted),
        "rejected_count": len(rejected),
        "accepted": accepted,
        "rejected": rejected,
    }


# ──────────────────────────────────────────────────────────────
# 따라말하기 API
# ──────────────────────────────────────────────────────────────

@app.post("/api/learning/pronunciation")
async def evaluate_single_pronunciation(
    audio: UploadFile = File(...),
    target_sentence: str = Form(...),
    level: int = Form(...),
):
    """단일 문장 발음 평가"""
    audio_bytes = await audio.read()
    result = evaluate_pronunciation(target_sentence, audio_bytes, level)
    return {
        "sentence": result.sentence,
        "transcribed": result.transcribed,
        "score": result.score,
        "passed": result.passed,
        "cutoff": LEVEL_CONFIGS[level].pronunciation_cutoff,
        "word_scores": result.word_scores,
    }


@app.post("/api/learning/pronunciation/session")
async def run_pronunciation_session_api(
    audios: list[UploadFile] = File(...),
    sentences: str = Form(...),    # JSON 배열
    level: int = Form(...),
):
    """따라말하기 세션 전체 평가"""
    sentence_list = json.loads(sentences)
    audio_bytes_list = [await a.read() for a in audios]

    results = run_repeat_session(sentence_list, audio_bytes_list, level)
    total_score = sum(r.score for r in results) / len(results) if results else 0

    return {
        "level": level,
        "cutoff": LEVEL_CONFIGS[level].pronunciation_cutoff,
        "total_avg_score": round(total_score),
        "session_passed": total_score >= LEVEL_CONFIGS[level].pronunciation_cutoff,
        "results": [
            {
                "sentence": r.sentence,
                "transcribed": r.transcribed,
                "score": r.score,
                "passed": r.passed,
            }
            for r in results
        ],
    }


# ──────────────────────────────────────────────────────────────
# 묘사 API
# ──────────────────────────────────────────────────────────────

@app.post("/api/learning/description/{lesson_id}/scene/{scene_number}")
async def evaluate_description_scene(
    lesson_id: str,
    scene_number: int,
    audio: UploadFile = File(...),
):
    """특정 장면 묘사 평가"""
    lesson_data = await _get_lesson(lesson_id)
    if not lesson_data:
        raise HTTPException(404, "강의를 찾을 수 없습니다.")

    scene_data = next(
        (s for s in lesson_data["description_scenes"] if s["scene_number"] == scene_number),
        None
    )
    if not scene_data:
        raise HTTPException(404, f"장면 {scene_number}을 찾을 수 없습니다.")

    from shared.models import DescriptionScene, DescriptionType
    scene = DescriptionScene(
        scene_number=scene_data["scene_number"],
        image_path=scene_data["image_path"],
        desc_type=DescriptionType(scene_data["desc_type"]),
        blank_word=scene_data.get("blank_word"),
        answer_sentence=scene_data["answer_sentence"],
        guide_hint=scene_data["guide_hint"],
    )

    audio_bytes = await audio.read()
    result = evaluate_description(scene, audio_bytes)

    return {
        "scene_number": result.scene_number,
        "user_answer": result.user_answer,
        "passed": result.passed,
        "feedback": result.feedback,
        "model_answer": scene.answer_sentence if not result.passed else None,
        "guide_hint": scene.guide_hint,
    }


# ──────────────────────────────────────────────────────────────
# 롤플레잉 WebSocket API
# ──────────────────────────────────────────────────────────────

@app.get("/api/learning/roleplay/{scenario_id}/start")
async def start_roleplay(scenario_id: str):
    """롤플레잉 시작 - 캐릭터 오프닝 멘트 반환"""
    scenario = await _get_roleplay_scenario(scenario_id)
    if not scenario:
        raise HTTPException(404, "시나리오를 찾을 수 없습니다.")

    opening = _get_opening_line(scenario)

    return {
        "scenario_id": scenario_id,
        "character_name": scenario.character_name,
        "scene_description": scenario.scene_description,
        "player_goal": scenario.player_goal,
        "opening_line": opening,
        "websocket_url": f"/ws/roleplay/{scenario_id}",
    }


@app.websocket("/ws/roleplay/{scenario_id}")
async def roleplay_websocket(websocket: WebSocket, scenario_id: str):
    """
    롤플레잉 실시간 WebSocket
    
    클라이언트 → 서버: 오디오 바이트 전송
    서버 → 클라이언트: JSON 이벤트 전송
    
    이벤트 타입:
    - turn_result: 턴 처리 결과
    - session_complete: 롤플레잉 완료
    - silence_detected: 10초 무음 감지 (라이온 버튼)
    - hint: 힌트 제공
    """
    await websocket.accept()

    scenario = await _get_roleplay_scenario(scenario_id)
    if not scenario:
        await websocket.send_json({"type": "error", "message": "시나리오를 찾을 수 없습니다."})
        await websocket.close()
        return

    handler = RoleplayWebSocketHandler(scenario)

    # 무음 감지 백그라운드 태스크
    silence_task = None

    async def silence_checker():
        while not handler.session.passed:
            await asyncio.sleep(1)
            if check_silence(handler.session):
                await websocket.send_json(handler.session.__class__.__name__ and {
                    "type": "silence_detected",
                    "message": "라이온이 나타났어요! 버튼을 눌러봐요.",
                    "show_lion": True,
                })

    try:
        silence_task = asyncio.create_task(silence_checker())

        while True:
            # 오디오 바이트 수신
            audio_bytes = await websocket.receive_bytes()

            # 턴 처리
            event = await handler.handle_audio_chunk(audio_bytes)
            await websocket.send_json(event)

            # 완료 시 종료
            if handler.session.passed:
                break

    except WebSocketDisconnect:
        print(f"[WebSocket] 클라이언트 연결 종료: {scenario_id}")
    finally:
        if silence_task:
            silence_task.cancel()


# ──────────────────────────────────────────────────────────────
# 유틸리티
# ──────────────────────────────────────────────────────────────

async def _save_lesson(lesson_data: dict, scenarios: list[RoleplayScenario]) -> None:
    if SessionLocal:
        async with SessionLocal() as session:
            await db_save_lesson(session, lesson_data, scenarios)
        return

    lesson_store[lesson_data["lesson_id"]] = lesson_data
    for scenario in scenarios:
        scenario_store[scenario.scenario_id] = scenario


async def _get_lesson(lesson_id: str) -> Optional[dict]:
    if SessionLocal:
        async with SessionLocal() as session:
            return await db_get_lesson(session, lesson_id)
    return lesson_store.get(lesson_id)


async def _get_roleplay_scenario(scenario_id: str) -> Optional[RoleplayScenario]:
    if SessionLocal:
        async with SessionLocal() as session:
            return await db_get_roleplay_scenario(session, scenario_id)
    return scenario_store.get(scenario_id)


@app.get("/")
async def root():
    return {
        "service": "동화 영어 학습 API",
        "version": "1.0.0",
        "endpoints": {
            "content": "POST /api/content/lesson",
            "content_batch": "POST /api/content/lessons/batch",
            "pronunciation": "POST /api/learning/pronunciation",
            "description": "POST /api/learning/description/{lesson_id}/scene/{scene_number}",
            "roleplay_start": "GET /api/learning/roleplay/{scenario_id}/start",
            "roleplay_ws": "WS /ws/roleplay/{scenario_id}",
        }
    }
