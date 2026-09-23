from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.api.deps import (
    get_current_profile,
    get_learning_session_service,
    get_speech_to_text_service,
)
from app.core.exceptions import AudioValidationException
from app.models import ChildProfile
from app.schemas.common import success_response
from app.schemas.learning import (
    QuestionProgressRequest,
    ReadingProgressRequest,
    StartLearningSessionRequest,
)
from app.services.learning_sessions import LearningSessionService
from app.services.speech import SpeechToTextService

book_sessions_router = APIRouter(prefix="/books", tags=["Learning - Reading"])
router = APIRouter(prefix="/learning-sessions", tags=["Learning - Reading"])


@book_sessions_router.post("/{bookId}/sessions")
async def start_or_resume_learning_session(
    bookId: int,
    request: StartLearningSessionRequest | None = None,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.start_or_resume_session(
            profile=current_profile,
            book_id=bookId,
            chapter_number=request.chapter_number if request else 1,
            restart=request.restart if request else False,
        )
    )


@router.get("/{sessionId}")
async def get_learning_session(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.get_session(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.patch("/{sessionId}/skip", tags=["Learning"])
async def skip_current_course(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.skip_current_course(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.get("/{sessionId}/reading", tags=["Learning - Reading"])
async def get_reading(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.get_reading(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.patch("/{sessionId}/reading/progress", tags=["Learning - Reading"])
async def update_reading_progress(
    sessionId: int,
    request: ReadingProgressRequest,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.update_reading_progress(
            profile=current_profile,
            session_id=sessionId,
            current_step=request.current_step,
        )
    )


@router.get("/{sessionId}/repeat", tags=["Learning - Repeat"])
async def get_repeat(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.get_repeat(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.post("/{sessionId}/repeat/attempts", tags=["Learning - Repeat"])
async def create_repeat_attempt(
    sessionId: int,
    audio: UploadFile = File(...),
    question_id: int = Form(..., alias="questionId"),
    transcript: str | None = Form(None),
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
    speech_to_text_service: SpeechToTextService = Depends(get_speech_to_text_service),
) -> dict:
    transcript = (
        transcript.strip()
        if isinstance(transcript, str) and transcript.strip()
        else await speech_to_text_service.transcribe(audio)
    )
    return success_response(
        await learning_session_service.create_repeat_attempt(
            profile=current_profile,
            session_id=sessionId,
            question_id=question_id,
            transcript=transcript,
        )
    )


@router.patch("/{sessionId}/repeat/progress", tags=["Learning - Repeat"])
async def update_repeat_progress(
    sessionId: int,
    request: QuestionProgressRequest,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.update_repeat_progress(
            profile=current_profile,
            session_id=sessionId,
            question_id=request.question_id,
        )
    )


@router.get("/{sessionId}/description", tags=["Learning - Description"])
async def get_description(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.get_description(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.post("/{sessionId}/description/attempts", tags=["Learning - Description"])
async def create_description_attempt(
    sessionId: int,
    audio: UploadFile = File(...),
    question_id: int = Form(..., alias="questionId"),
    transcript: str | None = Form(default=None),
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
    speech_to_text_service: SpeechToTextService = Depends(get_speech_to_text_service),
) -> dict:
    transcript = transcript.strip() if isinstance(transcript, str) else ""
    if not transcript:
        transcript = await speech_to_text_service.transcribe(audio)
    return success_response(
        await learning_session_service.create_description_attempt(
            profile=current_profile,
            session_id=sessionId,
            question_id=question_id,
            transcript=transcript,
        )
    )


@router.patch("/{sessionId}/description/progress", tags=["Learning - Description"])
async def update_description_progress(
    sessionId: int,
    request: QuestionProgressRequest,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.update_description_progress(
            profile=current_profile,
            session_id=sessionId,
            question_id=request.question_id,
        )
    )


@router.get("/{sessionId}/roleplay", tags=["Learning - Roleplay"])
async def get_roleplay(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.get_roleplay(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.post("/{sessionId}/roleplay/messages", tags=["Learning - Roleplay"])
async def create_roleplay_message(
    sessionId: int,
    audio: UploadFile = File(...),
    mission_id: int = Form(..., alias="missionId"),
    transcript: str | None = Form(default=None),
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
    speech_to_text_service: SpeechToTextService = Depends(get_speech_to_text_service),
) -> dict:
    submitted_transcript = transcript if isinstance(transcript, str) else None
    if submitted_transcript and submitted_transcript.strip():
        transcript = submitted_transcript.strip()
    else:
        try:
            transcript = await speech_to_text_service.transcribe(audio)
        except Exception:
            transcript = ""
    return success_response(
        await learning_session_service.create_roleplay_message(
            profile=current_profile,
            session_id=sessionId,
            mission_id=mission_id,
            transcript=transcript,
        )
    )


@router.post("/{sessionId}/exit")
async def exit_learning_session(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.exit_session(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.post("/{sessionId}/complete")
async def complete_learning_session(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.complete_session(
            profile=current_profile,
            session_id=sessionId,
        )
    )


@router.get("/{sessionId}/result")
async def get_learning_session_result(
    sessionId: int,
    current_profile: ChildProfile = Depends(get_current_profile),
    learning_session_service: LearningSessionService = Depends(
        get_learning_session_service
    ),
) -> dict:
    return success_response(
        await learning_session_service.get_result(
            profile=current_profile,
            session_id=sessionId,
        )
    )
