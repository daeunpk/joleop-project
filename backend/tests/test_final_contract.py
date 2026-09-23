import json
from datetime import UTC, datetime

import pytest
from fastapi.exceptions import RequestValidationError

from app.api.v1.learning_sessions import (
    complete_learning_session,
    create_roleplay_message,
    exit_learning_session,
    get_learning_session_result,
    get_roleplay,
)
from app.core.exceptions import SessionAlreadyCompletedException, validation_exception_handler
from app.main import app
from app.models import (
    Book,
    ChildProfile,
    CourseType,
    DescriptionQuestion,
    Difficulty,
    LearningAttempt,
    LearningSession,
    LearningSessionStatus,
    PointTransaction,
    ReadingChunk,
    RepeatQuestion,
    ReviewCard,
    RoleplayMessage,
    RoleplayMission,
    UserBookProgress,
)
from app.services.learning_sessions import LearningSessionService
from app.services.final_score import FinalScoreService
from app.services.speech import MockSpeechToTextService


class FakeScalarResult:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None


class FakeResult:
    def __init__(self, value=None, values=None):
        self.value = value
        self.values = values

    def scalar_one_or_none(self):
        return self.value

    def scalar_one(self):
        return self.value

    def scalars(self):
        return FakeScalarResult(self.values or [])


class FakeUploadFile:
    content_type = "audio/wav"

    async def read(self) -> bytes:
        return b"Hello test character"


class EmptyUploadFile:
    content_type = "audio/wav"

    async def read(self) -> bytes:
        return b""


class FakeRoleplayStore:
    def __init__(self) -> None:
        self.profile = ChildProfile(
            profile_id=101,
            parent_id=10,
            nickname="은정",
            age=8,
            password_hash="hash",
        )
        self.learning_session = LearningSession(
            session_id=128,
            profile_id=101,
            book_id=1,
            chapter_number=1,
            status=LearningSessionStatus.IN_PROGRESS,
            current_course=CourseType.ROLEPLAY,
            current_course_number=4,
            current_step=1,
            total_progress=75,
            total_score=None,
            stars=None,
            started_at=datetime.now(UTC),
            last_studied_at=datetime.now(UTC),
        )
        self.progress = UserBookProgress(
            progress_id=1,
            profile_id=101,
            book_id=1,
            progress=75,
            completed=False,
            unlocked=True,
        )
        self.book = Book(
            book_id=1,
            title="Test Story",
            lesson_name="Lesson 1",
            difficulty=Difficulty.BEGINNER,
        )
        self.mission = RoleplayMission(
            mission_id=401,
            book_id=1,
            title="Help the Character",
            description="Talk with the test character.",
            character_name="Test Character",
            character_image_url="https://cdn.example.com/test-character.png",
            opening_message="Can you help me?",
            model_answer="I can help the test character.",
            similar_answers=["Let us help together."],
            required_turns=3,
        )
        self.messages: list[RoleplayMessage] = []
        self.review_cards: list[ReviewCard] = []
        self.point_transactions: list[PointTransaction] = []
        self.attempts: list[LearningAttempt] = [
            LearningAttempt(
                attempt_id=1,
                session_id=128,
                course_type=CourseType.REPEAT,
                question_id=201,
                transcript="She is reading a book.",
                score=95,
                passed=True,
            ),
            LearningAttempt(
                attempt_id=2,
                session_id=128,
                course_type=CourseType.DESCRIPTION,
                question_id=301,
                transcript="The queen is wearing red.",
                score=88,
                passed=True,
            ),
        ]
        self.next_message_id = 1

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        params = statement.compile().params
        if entity is LearningSession:
            if "session_id_1" in params:
                return FakeResult(
                    self.learning_session
                    if self.learning_session.session_id == params["session_id_1"]
                    else None
                )
            return FakeResult(
                values=[self.learning_session]
                if (
                    self.learning_session.profile_id == params["profile_id_1"]
                    and self.learning_session.book_id == params["book_id_1"]
                    and self.learning_session.status
                    in {
                        LearningSessionStatus.IN_PROGRESS,
                        LearningSessionStatus.EXITED,
                    }
                )
                else []
            )
        if entity is Book:
            return FakeResult(self.book if self.book.book_id == params["book_id_1"] else None)
        if entity is RoleplayMission:
            return FakeResult(values=[self.mission])
        if entity is RoleplayMessage:
            return FakeResult(
                values=[
                    message
                    for message in self.messages
                    if message.session_id == params["session_id_1"]
                ]
            )
        if entity is LearningAttempt:
            return FakeResult(
                values=[
                    attempt
                    for attempt in self.attempts
                    if attempt.session_id == params["session_id_1"]
                ]
            )
        if entity is DescriptionQuestion:
            return FakeResult(values=[])
        if entity in {RepeatQuestion, ReadingChunk}:
            return FakeResult(values=[])
        if entity is ReviewCard:
            return FakeResult(None)
        if entity is UserBookProgress:
            return FakeResult(self.progress)
        if "reading_chunks" in str(statement):
            return FakeResult(1)
        raise AssertionError(f"Unexpected query: {statement}")

    def add(self, instance) -> None:
        if isinstance(instance, RoleplayMessage):
            instance.message_id = self.next_message_id
            self.next_message_id += 1
            instance.created_at = datetime.now(UTC)
            self.messages.append(instance)
            return
        if isinstance(instance, PointTransaction):
            self.point_transactions.append(instance)
            return
        if isinstance(instance, ReviewCard):
            instance.card_id = len(self.review_cards) + 1
            instance.created_at = datetime.now(UTC)
            instance.updated_at = datetime.now(UTC)
            self.review_cards.append(instance)
            return
        raise AssertionError(f"Unexpected add: {instance}")

    async def scalar(self, statement):
        return (await self.execute(statement)).scalar_one_or_none()

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, instance) -> None:
        return None


@pytest.fixture
def roleplay_context():
    store = FakeRoleplayStore()
    return {
        "store": store,
        "profile": store.profile,
        "service": LearningSessionService(session=store),
        "speech": MockSpeechToTextService(),
    }


def test_required_endpoints_exist() -> None:
    actual = {
        (next(iter(route.methods - {"HEAD", "OPTIONS"})), route.path)
        for route in app.routes
        if hasattr(route, "methods")
    }
    expected = {
        ("POST", "/api/v1/auth/kakao"),
        ("POST", "/api/v1/auth/logout"),
        ("POST", "/api/v1/auth/refresh"),
        ("GET", "/api/v1/parents/me"),
        ("GET", "/api/v1/profiles"),
        ("POST", "/api/v1/profiles"),
        ("PATCH", "/api/v1/profiles/{profileId}"),
        ("DELETE", "/api/v1/profiles/{profileId}"),
        ("PATCH", "/api/v1/profiles/{profileId}/password"),
        ("POST", "/api/v1/profiles/{profileId}/login"),
        ("POST", "/api/v1/profile-auth/logout"),
        ("GET", "/api/v1/profiles/me"),
        ("POST", "/api/v1/profiles/me/onboarding"),
        ("GET", "/api/v1/home"),
        ("GET", "/api/v1/books"),
        ("GET", "/api/v1/books/{bookId}"),
        ("POST", "/api/v1/books/{bookId}/sessions"),
        ("GET", "/api/v1/learning-sessions/{sessionId}"),
        ("GET", "/api/v1/learning-sessions/{sessionId}/reading"),
        ("PATCH", "/api/v1/learning-sessions/{sessionId}/reading/progress"),
        ("GET", "/api/v1/learning-sessions/{sessionId}/repeat"),
        ("POST", "/api/v1/learning-sessions/{sessionId}/repeat/attempts"),
        ("PATCH", "/api/v1/learning-sessions/{sessionId}/repeat/progress"),
        ("GET", "/api/v1/learning-sessions/{sessionId}/description"),
        ("POST", "/api/v1/learning-sessions/{sessionId}/description/attempts"),
        ("PATCH", "/api/v1/learning-sessions/{sessionId}/description/progress"),
        ("GET", "/api/v1/learning-sessions/{sessionId}/roleplay"),
        ("POST", "/api/v1/learning-sessions/{sessionId}/roleplay/messages"),
        ("POST", "/api/v1/learning-sessions/{sessionId}/exit"),
        ("POST", "/api/v1/learning-sessions/{sessionId}/complete"),
        ("GET", "/api/v1/learning-sessions/{sessionId}/result"),
        ("POST", "/api/v1/reviews/story-talk/roleplay/messages"),
    }
    assert expected.issubset(actual)


@pytest.mark.asyncio
async def test_validation_error_uses_common_response() -> None:
    response = await validation_exception_handler(None, RequestValidationError([]))
    assert response.status_code == 422
    assert json.loads(response.body) == {
        "success": False,
        "error": {
            "code": "INVALID_REQUEST",
            "message": "요청 값이 올바르지 않습니다.",
        },
    }


@pytest.mark.asyncio
async def test_roleplay(roleplay_context) -> None:
    response = await get_roleplay(
        128,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
    )
    assert response["data"]["mission"]["missionId"] == 401
    assert response["data"]["character"] == {
        "name": "Test Character",
        "imageUrl": "https://cdn.example.com/test-character.png",
    }
    assert response["data"]["courseProgress"] == 0


@pytest.mark.asyncio
async def test_roleplay_audio(roleplay_context) -> None:
    response = await create_roleplay_message(
        128,
        audio=FakeUploadFile(),
        mission_id=401,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
        speech_to_text_service=roleplay_context["speech"],
    )
    assert response["data"]["user"]["transcript"] == "Hello test character"
    assert response["data"]["character"] == {
        "speaker": "TEST CHARACTER",
        "text": "Thank you! That helps a lot.",
    }
    assert response["data"]["turn"] == 1
    assert response["data"]["missionCompleted"] is False
    assert response["data"]["courseProgress"] == 33
    assert response["data"]["totalProgress"] == 83


@pytest.mark.asyncio
async def test_roleplay_empty_audio_uses_fallback_transcript(roleplay_context) -> None:
    response = await create_roleplay_message(
        128,
        audio=EmptyUploadFile(),
        mission_id=401,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
        speech_to_text_service=roleplay_context["speech"],
    )

    assert response["data"]["user"]["transcript"] == "I can help the test character."
    assert response["data"]["turn"] == 1
    assert response["data"]["missionCompleted"] is False


@pytest.mark.asyncio
async def test_roleplay_turns_and_mission_complete(roleplay_context) -> None:
    for expected_turn in [1, 2, 3]:
        response = await create_roleplay_message(
            128,
            audio=FakeUploadFile(),
            mission_id=401,
            current_profile=roleplay_context["profile"],
            learning_session_service=roleplay_context["service"],
            speech_to_text_service=roleplay_context["speech"],
        )
        assert response["data"]["turn"] == expected_turn

    assert response["data"]["missionCompleted"] is True
    assert response["data"]["courseProgress"] == 100
    assert response["data"]["totalProgress"] == 100
    assert roleplay_context["store"].progress.progress == 100


@pytest.mark.asyncio
async def test_exit_complete_result_and_recomplete_block(roleplay_context) -> None:
    for _ in range(3):
        await create_roleplay_message(
            128,
            audio=FakeUploadFile(),
            mission_id=401,
            current_profile=roleplay_context["profile"],
            learning_session_service=roleplay_context["service"],
            speech_to_text_service=roleplay_context["speech"],
        )

    exit_response = await exit_learning_session(
        128,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
    )
    assert exit_response["data"]["status"] == "EXITED"
    assert exit_response["data"]["saved"] is True
    assert exit_response["data"]["currentCourse"] == "ROLEPLAY"

    resume_response = await roleplay_context["service"].start_or_resume_session(
        profile=roleplay_context["profile"],
        book_id=1,
    )
    assert resume_response["isNew"] is False
    assert resume_response["status"] == "IN_PROGRESS"

    complete_response = await complete_learning_session(
        128,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
    )
    assert complete_response["data"]["status"] == "COMPLETED"
    assert complete_response["data"]["totalScore"] == 91
    assert complete_response["data"]["stars"] == 3
    assert complete_response["data"]["rewards"] == {"hearts": 10, "energy": 0}
    assert roleplay_context["store"].progress.completed is True
    assert roleplay_context["store"].progress.progress == 100

    result_response = await get_learning_session_result(
        128,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
    )
    assert result_response["data"]["profile"] == {"profileId": 101, "nickname": "은정"}
    assert result_response["data"]["book"] == {
        "bookId": 1,
        "title": "Test Story",
    }
    assert result_response["data"]["completed"] is True

    with pytest.raises(SessionAlreadyCompletedException):
        await complete_learning_session(
            128,
            current_profile=roleplay_context["profile"],
            learning_session_service=roleplay_context["service"],
        )


@pytest.mark.asyncio
async def test_complete_requires_all_scored_courses(roleplay_context) -> None:
    with pytest.raises(Exception) as exc:
        await complete_learning_session(
            128,
            current_profile=roleplay_context["profile"],
            learning_session_service=roleplay_context["service"],
        )

    assert exc.value.error_code == "RESULT_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_complete_requires_finished_roleplay_mission(roleplay_context) -> None:
    await create_roleplay_message(
        128,
        audio=FakeUploadFile(),
        mission_id=401,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
        speech_to_text_service=roleplay_context["speech"],
    )

    with pytest.raises(Exception) as exc:
        await complete_learning_session(
            128,
            current_profile=roleplay_context["profile"],
            learning_session_service=roleplay_context["service"],
        )

    assert exc.value.error_code == "RESULT_NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_complete_accepts_courses_skipped_before_finished_roleplay(
    roleplay_context,
) -> None:
    roleplay_context["store"].attempts.clear()
    for _ in range(3):
        await create_roleplay_message(
            128,
            audio=FakeUploadFile(),
            mission_id=401,
            current_profile=roleplay_context["profile"],
            learning_session_service=roleplay_context["service"],
            speech_to_text_service=roleplay_context["speech"],
        )

    response = await complete_learning_session(
        128,
        current_profile=roleplay_context["profile"],
        learning_session_service=roleplay_context["service"],
    )

    assert response["data"]["status"] == "COMPLETED"
    assert response["data"]["totalScore"] == 30
    assert response["data"]["stars"] == 0


@pytest.mark.asyncio
async def test_result_requires_completed_session(roleplay_context) -> None:
    with pytest.raises(Exception) as exc:
        await get_learning_session_result(
            128,
            current_profile=roleplay_context["profile"],
            learning_session_service=roleplay_context["service"],
        )

    assert exc.value.error_code == "RESULT_NOT_AVAILABLE"


def test_final_score_stars() -> None:
    assert FinalScoreService.stars(59) == 1
    assert FinalScoreService.stars(60) == 2
    assert FinalScoreService.stars(79) == 2
    assert FinalScoreService.stars(80) == 3
