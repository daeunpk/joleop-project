from datetime import UTC, datetime

import pytest

from app.api.v1.learning_sessions import (
    create_description_attempt,
    create_repeat_attempt,
    get_description,
    get_repeat,
    update_description_progress,
    update_repeat_progress,
)
from app.core.config import settings
from app.core.exceptions import (
    AppException,
    AttemptRequiredException,
    AudioValidationException,
    InvalidCourseStateException,
)
from app.models import (
    ChildProfile,
    CourseType,
    DescriptionQuestion,
    DescriptionQuestionType,
    LearningAttempt,
    LearningSession,
    LearningSessionStatus,
    RepeatQuestion,
    UserBookProgress,
)
from app.schemas.learning import QuestionProgressRequest
from app.services.learning_sessions import LearningSessionService
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

    def scalars(self):
        return FakeScalarResult(self.values or [])


class FakeCourseStore:
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
            status=LearningSessionStatus.IN_PROGRESS,
            current_course=CourseType.REPEAT,
            current_course_number=2,
            current_step=1,
            total_progress=30,
            started_at=datetime.now(UTC),
            last_studied_at=datetime.now(UTC),
        )
        self.progress = UserBookProgress(
            progress_id=1,
            profile_id=101,
            book_id=1,
            progress=30,
            completed=False,
            unlocked=True,
        )
        self.repeat_questions = [
            RepeatQuestion(
                question_id=200 + step,
                book_id=1,
                step=step,
                target_text="She is reading a book.",
                image_url=f"https://cdn.example.com/repeat/{step}.png",
            )
            for step in range(1, 5)
        ]
        self.description_questions = [
            DescriptionQuestion(
                question_id=300 + step,
                book_id=1,
                step=step,
                question_type=DescriptionQuestionType.WORD_GUESS,
                instruction="여왕의 옷 색깔은 무엇일까요?",
                sentence="The color of the queen's clothes is ____.",
                image_url=f"https://cdn.example.com/description/{step}.png",
                page_number=step,
                source_text="The queen is wearing red.",
                blank_word="red",
                answer_sentence="red",
                guide_hint="Look at the queen's clothes.",
            )
            for step in range(1, 5)
        ]
        self.attempts: list[LearningAttempt] = []
        self.next_attempt_id = 1001

    async def execute(self, statement):
        entity = statement.column_descriptions[0].get("entity")
        params = statement.compile().params
        if entity is LearningSession:
            return FakeResult(
                self.learning_session
                if self.learning_session.session_id == params["session_id_1"]
                else None
            )
        if entity is RepeatQuestion:
            return FakeResult(values=self.repeat_questions)
        if entity is DescriptionQuestion:
            return FakeResult(values=self.description_questions)
        if entity is LearningAttempt:
            return FakeResult(
                values=self._attempts(
                    session_id=params["session_id_1"],
                    course_type=params["course_type_1"],
                    question_id=params["question_id_1"],
                )
            )
        if entity is UserBookProgress:
            return FakeResult(self.progress)
        raise AssertionError(f"Unexpected query: {statement}")

    def add(self, instance) -> None:
        if isinstance(instance, LearningAttempt):
            instance.attempt_id = self.next_attempt_id
            self.next_attempt_id += 1
            instance.created_at = datetime.now(UTC)
            self.attempts.append(instance)
            return
        raise AssertionError(f"Unexpected add: {instance}")

    async def flush(self) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, instance) -> None:
        return None

    def _attempt(
        self,
        *,
        session_id: int,
        course_type: CourseType,
        question_id: int,
    ) -> LearningAttempt | None:
        return next(
            (
                attempt
                for attempt in self.attempts
                if attempt.session_id == session_id
                and attempt.course_type == course_type
                and attempt.question_id == question_id
            ),
            None,
        )

    def _attempts(
        self,
        *,
        session_id: int,
        course_type: CourseType,
        question_id: int,
    ) -> list[LearningAttempt]:
        return [
            attempt
            for attempt in self.attempts
            if attempt.session_id == session_id
            and attempt.course_type == course_type
            and attempt.question_id == question_id
        ]


@pytest.fixture
def course_context():
    settings.MAX_AUDIO_UPLOAD_BYTES = 1024
    store = FakeCourseStore()
    return {
        "store": store,
        "profile": store.profile,
        "service": LearningSessionService(session=store),
        "speech": MockSpeechToTextService(),
    }


class FakeUploadFile:
    def __init__(
        self,
        data: bytes = b"She is reading a book.",
        *,
        content_type: str = "audio/wav",
    ) -> None:
        self.data = data
        self.content_type = content_type

    async def read(self) -> bytes:
        return self.data


def upload_file(
    data: bytes = b"She is reading a book.",
    *,
    content_type: str = "audio/wav",
) -> FakeUploadFile:
    return FakeUploadFile(data=data, content_type=content_type)


@pytest.mark.asyncio
async def test_repeat_lookup(course_context) -> None:
    response = await get_repeat(
        128,
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["courseType"] == "REPEAT"
    assert response["data"]["courseNumber"] == 2
    assert response["data"]["currentStep"] == 1
    assert response["data"]["totalSteps"] == 4
    assert response["data"]["content"]["questionId"] == 201


@pytest.mark.asyncio
async def test_invalid_course(course_context) -> None:
    course_context["store"].learning_session.current_course = CourseType.READING

    with pytest.raises(InvalidCourseStateException):
        await get_repeat(
            128,
            current_profile=course_context["profile"],
            learning_session_service=course_context["service"],
        )


@pytest.mark.asyncio
async def test_audio_upload_and_repeat_attempt_saved(course_context) -> None:
    response = await create_repeat_attempt(
        128,
        audio=upload_file(),
        question_id=201,
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
        speech_to_text_service=course_context["speech"],
    )

    assert response["data"]["attemptId"] == 1001
    assert response["data"]["transcript"] == "She is reading a book."
    assert response["data"]["score"] == 100
    assert response["data"]["wordResults"][0]["word"] == "She"
    assert response["data"]["wordResults"] == [
        {
            "word": "She",
            "normalizedWord": "she",
            "recognizedWord": "she",
            "correct": True,
        },
        {
            "word": "is",
            "normalizedWord": "is",
            "recognizedWord": "is",
            "correct": True,
        },
        {
            "word": "reading",
            "normalizedWord": "reading",
            "recognizedWord": "reading",
            "correct": True,
        },
        {
            "word": "a",
            "normalizedWord": "a",
            "recognizedWord": "a",
            "correct": True,
        },
        {
            "word": "book",
            "normalizedWord": "book",
            "recognizedWord": "book",
            "correct": True,
        },
    ]
    assert len(course_context["store"].attempts) == 1
    assert course_context["store"].attempts[0].transcript == "She is reading a book."
    assert course_context["store"].attempts[0].word_results == response["data"]["wordResults"]


@pytest.mark.asyncio
async def test_repeat_attempt_uses_browser_transcript_when_provided(course_context) -> None:
    response = await create_repeat_attempt(
        128,
        audio=upload_file(b"unreadable audio bytes"),
        question_id=201,
        transcript="She is reading a book.",
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
        speech_to_text_service=course_context["speech"],
    )

    assert response["data"]["transcript"] == "She is reading a book."
    assert response["data"]["score"] == 100


@pytest.mark.asyncio
async def test_attempt_required_before_repeat_next(course_context) -> None:
    with pytest.raises(AttemptRequiredException):
        await update_repeat_progress(
            128,
            QuestionProgressRequest(questionId=201),
            current_profile=course_context["profile"],
            learning_session_service=course_context["service"],
        )


@pytest.mark.asyncio
async def test_repeat_next(course_context) -> None:
    await create_repeat_attempt(
        128,
        audio=upload_file(),
        question_id=201,
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
        speech_to_text_service=course_context["speech"],
    )

    response = await update_repeat_progress(
        128,
        QuestionProgressRequest(questionId=201),
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["currentStep"] == 2
    assert response["data"]["courseProgress"] == 50
    assert response["data"]["courseCompleted"] is False


@pytest.mark.asyncio
async def test_repeat_next_allows_multiple_attempts_for_same_question(course_context) -> None:
    for attempt_id in (1001, 1002):
        course_context["store"].attempts.append(
            LearningAttempt(
                attempt_id=attempt_id,
                session_id=128,
                course_type=CourseType.REPEAT,
                question_id=201,
                transcript="She is reading a book.",
                score=100,
                passed=True,
            )
        )

    response = await update_repeat_progress(
        128,
        QuestionProgressRequest(questionId=201),
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["currentStep"] == 2
    assert response["data"]["courseCompleted"] is False


@pytest.mark.asyncio
async def test_repeat_next_is_idempotent_for_already_advanced_question(course_context) -> None:
    await create_repeat_attempt(
        128,
        audio=upload_file(),
        question_id=201,
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
        speech_to_text_service=course_context["speech"],
    )
    await update_repeat_progress(
        128,
        QuestionProgressRequest(questionId=201),
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    response = await update_repeat_progress(
        128,
        QuestionProgressRequest(questionId=201),
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["currentStep"] == 2
    assert response["data"]["courseCompleted"] is False


@pytest.mark.asyncio
async def test_repeat_complete_moves_to_description(course_context) -> None:
    session = course_context["store"].learning_session
    session.current_step = 4
    session.total_progress = 44
    course_context["store"].attempts.append(
        LearningAttempt(
            attempt_id=1001,
            session_id=128,
            course_type=CourseType.REPEAT,
            question_id=204,
            transcript="She is reading a book.",
            score=95,
            passed=True,
        )
    )

    response = await update_repeat_progress(
        128,
        QuestionProgressRequest(questionId=204),
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["nextCourse"] == "DESCRIPTION"
    assert session.current_course == CourseType.DESCRIPTION
    assert session.current_course_number == 3
    assert session.current_step == 1


@pytest.mark.asyncio
async def test_description_lookup(course_context) -> None:
    course_context["store"].learning_session.current_course = CourseType.DESCRIPTION
    course_context["store"].learning_session.current_course_number = 3
    course_context["store"].learning_session.total_progress = 55

    response = await get_description(
        128,
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["courseType"] == "DESCRIPTION"
    assert response["data"]["content"]["questionId"] == 301
    assert response["data"]["content"]["questionType"] == "WORD_GUESS"
    assert response["data"]["content"]["pageNumber"] == 1
    assert response["data"]["content"]["blankWord"] == "red"
    assert response["data"]["content"]["answerSentence"] == "red"
    assert response["data"]["content"]["guideHint"] == "Look at the queen's clothes."


@pytest.mark.asyncio
async def test_description_attempt(course_context) -> None:
    course_context["store"].learning_session.current_course = CourseType.DESCRIPTION
    course_context["store"].learning_session.current_course_number = 3
    course_context["store"].learning_session.total_progress = 55

    response = await create_description_attempt(
        128,
        audio=upload_file(b"The queen is wearing red."),
        question_id=301,
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
        speech_to_text_service=course_context["speech"],
    )

    assert response["data"]["attemptId"] == 1001
    assert response["data"]["score"] == 100
    assert response["data"]["passed"] is True
    assert response["data"]["feedback"] == "Great!"
    assert response["data"]["modelAnswer"] == "red"
    assert response["data"]["guideHint"] == "Look at the queen's clothes."


@pytest.mark.asyncio
async def test_description_next(course_context) -> None:
    session = course_context["store"].learning_session
    session.current_course = CourseType.DESCRIPTION
    session.current_course_number = 3
    session.total_progress = 55
    course_context["store"].attempts.append(
        LearningAttempt(
            attempt_id=1001,
            session_id=128,
            course_type=CourseType.DESCRIPTION,
            question_id=301,
            transcript="The queen is wearing red.",
            score=88,
            passed=True,
        )
    )

    response = await update_description_progress(
        128,
        QuestionProgressRequest(questionId=301),
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["currentStep"] == 2
    assert response["data"]["courseCompleted"] is False


@pytest.mark.asyncio
async def test_description_complete_moves_to_roleplay(course_context) -> None:
    session = course_context["store"].learning_session
    session.current_course = CourseType.DESCRIPTION
    session.current_course_number = 3
    session.current_step = 4
    session.total_progress = 69
    course_context["store"].attempts.append(
        LearningAttempt(
            attempt_id=1001,
            session_id=128,
            course_type=CourseType.DESCRIPTION,
            question_id=304,
            transcript="The queen is wearing red.",
            score=88,
            passed=True,
        )
    )

    response = await update_description_progress(
        128,
        QuestionProgressRequest(questionId=304),
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
    )

    assert response["data"]["nextCourse"] == "ROLEPLAY"
    assert session.current_course == CourseType.ROLEPLAY
    assert session.current_course_number == 4
    assert session.current_step == 1


@pytest.mark.asyncio
async def test_invalid_mime(course_context) -> None:
    with pytest.raises(AudioValidationException) as exc:
        await create_repeat_attempt(
            128,
            audio=upload_file(content_type="text/plain"),
            question_id=201,
            current_profile=course_context["profile"],
            learning_session_service=course_context["service"],
            speech_to_text_service=course_context["speech"],
        )
    assert exc.value.error_code == "INVALID_AUDIO_MIME_TYPE"


@pytest.mark.asyncio
async def test_audio_mime_with_codec_parameter(course_context) -> None:
    response = await create_repeat_attempt(
        128,
        audio=upload_file(content_type="audio/webm;codecs=opus"),
        question_id=201,
        current_profile=course_context["profile"],
        learning_session_service=course_context["service"],
        speech_to_text_service=course_context["speech"],
    )

    assert response["data"]["transcript"] == "She is reading a book."


@pytest.mark.asyncio
async def test_empty_audio(course_context) -> None:
    with pytest.raises(AudioValidationException) as exc:
        await create_repeat_attempt(
            128,
            audio=upload_file(b""),
            question_id=201,
            current_profile=course_context["profile"],
            learning_session_service=course_context["service"],
            speech_to_text_service=course_context["speech"],
        )
    assert exc.value.error_code == "EMPTY_AUDIO"


@pytest.mark.asyncio
async def test_oversized_audio(course_context) -> None:
    with pytest.raises(AudioValidationException) as exc:
        await create_repeat_attempt(
            128,
            audio=upload_file(b"x" * 1025),
            question_id=201,
            current_profile=course_context["profile"],
            learning_session_service=course_context["service"],
            speech_to_text_service=course_context["speech"],
        )
    assert exc.value.error_code == "AUDIO_TOO_LARGE"
