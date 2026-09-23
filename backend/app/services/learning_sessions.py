from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    AppException,
    AttemptRequiredException,
    BookLockedException,
    BookNotFoundException,
    InvalidCourseStateException,
    InvalidStepException,
    QuestionNotFoundException,
    ResultNotAvailableException,
    SessionAccessDeniedException,
    SessionAlreadyCompletedException,
    SessionNotFoundException,
)
from app.models import (
    Book,
    ChildProfile,
    CourseType,
    DescriptionQuestion,
    LearningSession,
    LearningSessionStatus,
    LearningAttempt,
    PointTransaction,
    ReadingChunk,
    RepeatQuestion,
    RoleplayMessage,
    RoleplayMission,
    UserBookProgress,
)
from app.services.evaluation import DescriptionEvaluationService, RepeatEvaluationService
from app.services.energy import EnergyService
from app.services.final_score import FinalScoreService
from app.services.progress import ProgressService
from app.services.rewards import RewardService
from app.services.reviews import ReviewService
from app.services.roleplay import (
    AIRoleplayService,
    RoleplayService,
    clean_roleplay_transcript,
    roleplay_runtime_context,
)


class LearningSessionService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        progress_service: ProgressService | None = None,
        repeat_evaluation_service: RepeatEvaluationService | None = None,
        description_evaluation_service: DescriptionEvaluationService | None = None,
        roleplay_service: RoleplayService | None = None,
        energy_service: EnergyService | None = None,
        final_score_service: FinalScoreService | None = None,
        reward_service: RewardService | None = None,
    ) -> None:
        self.session = session
        self.progress_service = progress_service or ProgressService()
        self.repeat_evaluation_service = (
            repeat_evaluation_service or RepeatEvaluationService()
        )
        self.description_evaluation_service = (
            description_evaluation_service or DescriptionEvaluationService()
        )
        self.roleplay_service = roleplay_service or AIRoleplayService(session=session)
        self.energy_service = energy_service or EnergyService()
        self.final_score_service = final_score_service or FinalScoreService()
        self.reward_service = reward_service or RewardService()
        self.review_service = ReviewService(session=session)

    async def start_or_resume_session(
        self,
        *,
        profile: ChildProfile,
        book_id: int,
        chapter_number: int = 1,
        restart: bool = False,
    ) -> dict:
        await self._ensure_book_available_for_profile(profile=profile, book_id=book_id)
        learning_session = await self._active_or_exited_session(
            profile_id=profile.profile_id,
            book_id=book_id,
            chapter_number=chapter_number,
            for_update=True,
        )
        is_new = learning_session is None
        now = datetime.now(UTC)

        if learning_session is None:
            self.energy_service.consume_for_learning(profile, now=now)
            learning_session = LearningSession(
                profile_id=profile.profile_id,
                book_id=book_id,
                chapter_number=chapter_number,
                status=LearningSessionStatus.IN_PROGRESS,
                current_course=CourseType.READING,
                current_course_number=1,
                current_step=1,
                total_progress=0,
                started_at=now,
                last_studied_at=now,
            )
            self.session.add(learning_session)
            await self.session.flush()
        else:
            if restart:
                self.energy_service.consume_for_learning(profile, now=now)
                await self.session.execute(
                    delete(RoleplayMessage).where(
                        RoleplayMessage.session_id == learning_session.session_id
                    )
                )
                await self.session.execute(
                    delete(LearningAttempt).where(
                        LearningAttempt.session_id == learning_session.session_id
                    )
                )
                learning_session.current_course = CourseType.READING
                learning_session.current_course_number = 1
                learning_session.current_step = 1
                learning_session.total_progress = 0
            learning_session.status = LearningSessionStatus.IN_PROGRESS
            learning_session.last_studied_at = now

        await self._get_or_create_book_progress(
            profile_id=profile.profile_id,
            book_id=book_id,
            now=now,
        )
        await self.session.commit()
        await self.session.refresh(learning_session)
        return self.session_response(learning_session, is_new=is_new)

    async def get_session(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        return self.session_detail_response(learning_session)

    async def skip_current_course(
        self,
        *,
        profile: ChildProfile,
        session_id: int,
    ) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
            for_update=True,
        )
        next_courses = {
            CourseType.READING: (CourseType.REPEAT, 2),
            CourseType.REPEAT: (CourseType.DESCRIPTION, 3),
            CourseType.DESCRIPTION: (CourseType.ROLEPLAY, 4),
        }
        next_course = next_courses.get(learning_session.current_course)
        if next_course is None:
            raise AppException(status_code=409, detail="마지막 코스는 건너뛸 수 없습니다.")

        skipped_course = learning_session.current_course
        now = datetime.now(UTC)
        progress = await self._get_or_create_book_progress(
            profile_id=profile.profile_id,
            book_id=learning_session.book_id,
            now=now,
        )
        learning_session.current_course = next_course[0]
        learning_session.current_course_number = next_course[1]
        learning_session.current_step = 1
        learning_session.total_progress = self.progress_service.COURSE_RANGES[skipped_course][1]
        learning_session.last_studied_at = now
        progress.progress = learning_session.total_progress
        progress.unlocked = True
        progress.last_studied_at = now

        await self.session.commit()
        await self.session.refresh(learning_session)
        return self.session_detail_response(learning_session)

    async def get_reading(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        self._ensure_reading_course(learning_session)
        chunks = await self._reading_chunks(learning_session.book_id, learning_session.chapter_number)
        chunk = self._chunk_for_step(chunks, learning_session.current_step)
        total_steps = len(chunks)
        return {
            "courseType": CourseType.READING.value,
            "courseNumber": 1,
            "currentStep": learning_session.current_step,
            "totalSteps": total_steps,
            "courseProgress": self.progress_service.course_progress(
                current_step=learning_session.current_step,
                total_steps=total_steps,
            ),
            "totalProgress": learning_session.total_progress,
            "content": self.chunk_content(chunk),
        }

    async def update_reading_progress(
        self,
        *,
        profile: ChildProfile,
        session_id: int,
        current_step: int,
    ) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
            for_update=True,
        )
        self._ensure_reading_course(learning_session)
        if learning_session.current_step != current_step:
            raise InvalidStepException()

        chunks = await self._reading_chunks(learning_session.book_id, learning_session.chapter_number)
        total_steps = len(chunks)
        if total_steps == 0:
            raise AppException(status_code=404, detail="읽기 콘텐츠를 찾을 수 없습니다.")

        now = datetime.now(UTC)
        progress = await self._get_or_create_book_progress(
            profile_id=profile.profile_id,
            book_id=learning_session.book_id,
            now=now,
        )

        if current_step >= total_steps:
            learning_session.current_course = CourseType.REPEAT
            learning_session.current_course_number = 2
            learning_session.current_step = 1
            learning_session.total_progress = 25
            learning_session.last_studied_at = now
            progress.progress = 25
            progress.unlocked = True
            progress.last_studied_at = now
            await self.session.commit()
            return {
                "courseProgress": 100,
                "totalProgress": 25,
                "courseCompleted": True,
                "nextCourse": CourseType.REPEAT.value,
            }

        next_step = current_step + 1
        total_progress = self.progress_service.total_progress(
            course_type=CourseType.READING,
            current_step=next_step,
            total_steps=total_steps,
        )
        learning_session.current_step = next_step
        learning_session.total_progress = total_progress
        learning_session.last_studied_at = now
        progress.progress = total_progress
        progress.unlocked = True
        progress.last_studied_at = now

        await self.session.commit()
        await self.session.refresh(learning_session)
        next_chunk = self._chunk_for_step(chunks, next_step)
        return {
            "currentStep": next_step,
            "totalSteps": total_steps,
            "courseProgress": self.progress_service.course_progress(
                current_step=next_step,
                total_steps=total_steps,
            ),
            "totalProgress": total_progress,
            "courseCompleted": False,
            "content": self.chunk_content(next_chunk),
        }

    async def get_repeat(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        self._ensure_course(learning_session, CourseType.REPEAT)
        questions = await self._repeat_questions(learning_session.book_id, learning_session.chapter_number)
        question = self._question_for_step(questions, learning_session.current_step)
        total_steps = len(questions)
        return {
            "courseType": CourseType.REPEAT.value,
            "courseNumber": 2,
            "currentStep": learning_session.current_step,
            "totalSteps": total_steps,
            "courseProgress": self.progress_service.course_progress(
                current_step=learning_session.current_step,
                total_steps=total_steps,
            ),
            "totalProgress": learning_session.total_progress,
            "content": {
                "questionId": question.question_id,
                "targetText": question.target_text,
                "imageUrl": question.image_url,
            },
        }

    async def create_repeat_attempt(
        self,
        *,
        profile: ChildProfile,
        session_id: int,
        question_id: int,
        transcript: str,
    ) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        self._ensure_course(learning_session, CourseType.REPEAT)
        question = await self._current_repeat_question(learning_session, question_id)
        evaluation = self.repeat_evaluation_service.evaluate(
            target_text=question.target_text,
            transcript=transcript,
        )
        attempt = LearningAttempt(
            session_id=learning_session.session_id,
            course_type=CourseType.REPEAT,
            question_id=question.question_id,
            transcript=transcript,
            score=evaluation["score"],
            passed=evaluation["passed"],
            word_results=evaluation["wordResults"],
        )
        self.session.add(attempt)
        await self.session.flush()
        await self.session.commit()
        return {
            "attemptId": attempt.attempt_id,
            "questionId": question.question_id,
            "targetText": question.target_text,
            "transcript": transcript,
            "score": attempt.score,
            "passed": attempt.passed,
            "wordResults": attempt.word_results,
            "courseProgress": self.progress_service.course_progress(
                current_step=learning_session.current_step,
                total_steps=len(await self._repeat_questions(learning_session.book_id, learning_session.chapter_number)),
            ),
            "totalProgress": min(50, learning_session.total_progress + 1),
        }

    async def update_repeat_progress(
        self,
        *,
        profile: ChildProfile,
        session_id: int,
        question_id: int,
    ) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
            for_update=True,
        )
        if learning_session.current_course != CourseType.REPEAT:
            if await self._has_attempt(
                learning_session.session_id,
                CourseType.REPEAT,
                question_id,
            ):
                return {
                    "courseProgress": 100,
                    "totalProgress": learning_session.total_progress,
                    "courseCompleted": True,
                    "nextCourse": learning_session.current_course.value,
                }
            self._ensure_course(learning_session, CourseType.REPEAT)

        self._ensure_course(learning_session, CourseType.REPEAT)
        questions = await self._repeat_questions(learning_session.book_id, learning_session.chapter_number)
        question = self._question_for_step(questions, learning_session.current_step)
        if question.question_id != question_id:
            if await self._has_attempt(
                learning_session.session_id,
                CourseType.REPEAT,
                question_id,
            ):
                return {
                    "currentStep": learning_session.current_step,
                    "totalSteps": len(questions),
                    "courseProgress": self.progress_service.course_progress(
                        current_step=learning_session.current_step,
                        total_steps=len(questions),
                    ),
                    "totalProgress": learning_session.total_progress,
                    "courseCompleted": False,
                }
            raise InvalidStepException()
        if not await self._has_attempt(learning_session.session_id, CourseType.REPEAT, question.question_id):
            raise AttemptRequiredException()
        return await self._advance_question_course(
            learning_session=learning_session,
            profile=profile,
            total_steps=len(questions),
            current_step=learning_session.current_step,
            course_type=CourseType.REPEAT,
            next_course=CourseType.DESCRIPTION,
            next_course_number=3,
        )

    async def get_description(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        self._ensure_course(learning_session, CourseType.DESCRIPTION)
        questions = await self._description_questions(learning_session.book_id, learning_session.chapter_number)
        question = self._question_for_step(questions, learning_session.current_step)
        total_steps = len(questions)
        return {
            "courseType": CourseType.DESCRIPTION.value,
            "courseNumber": 3,
            "currentStep": learning_session.current_step,
            "totalSteps": total_steps,
            "courseProgress": self.progress_service.course_progress(
                current_step=learning_session.current_step,
                total_steps=total_steps,
            ),
            "totalProgress": learning_session.total_progress,
            "content": {
                "questionId": question.question_id,
                "questionType": question.question_type.value,
                "instruction": question.instruction,
                "imageUrl": question.image_url,
                "sentence": question.sentence,
                "pageNumber": question.page_number,
                "sourceText": question.source_text,
                "blankWord": question.blank_word,
                "answerSentence": question.answer_sentence,
                "guideHint": question.guide_hint,
            },
        }

    async def create_description_attempt(
        self,
        *,
        profile: ChildProfile,
        session_id: int,
        question_id: int,
        transcript: str,
    ) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        self._ensure_course(learning_session, CourseType.DESCRIPTION)
        question = await self._current_description_question(learning_session, question_id)
        evaluation = self.description_evaluation_service.evaluate(
            instruction=question.instruction,
            sentence=question.sentence,
            transcript=transcript,
            answer_sentence=question.answer_sentence,
            blank_word=question.blank_word,
        )
        attempt = LearningAttempt(
            session_id=learning_session.session_id,
            course_type=CourseType.DESCRIPTION,
            question_id=question.question_id,
            transcript=transcript,
            score=evaluation["score"],
            passed=evaluation["passed"],
            feedback=evaluation["feedback"],
            word_results=evaluation.get("wordResults"),
        )
        self.session.add(attempt)
        await self.session.flush()
        await self.session.commit()
        return {
            "attemptId": attempt.attempt_id,
            "questionId": question.question_id,
            "transcript": transcript,
            "score": attempt.score,
            "passed": attempt.passed,
            "feedback": attempt.feedback,
            "wordResults": attempt.word_results,
            "modelAnswer": question.answer_sentence or question.sentence,
            "guideHint": question.guide_hint,
            "courseProgress": self.progress_service.course_progress(
                current_step=learning_session.current_step,
                total_steps=len(await self._description_questions(learning_session.book_id, learning_session.chapter_number)),
            ),
            "totalProgress": min(75, learning_session.total_progress + 2),
        }

    async def update_description_progress(
        self,
        *,
        profile: ChildProfile,
        session_id: int,
        question_id: int,
    ) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
            for_update=True,
        )
        self._ensure_course(learning_session, CourseType.DESCRIPTION)
        question = await self._current_description_question(learning_session, question_id)
        if not await self._has_attempt(
            learning_session.session_id,
            CourseType.DESCRIPTION,
            question.question_id,
        ):
            raise AttemptRequiredException()
        questions = await self._description_questions(learning_session.book_id, learning_session.chapter_number)
        return await self._advance_question_course(
            learning_session=learning_session,
            profile=profile,
            total_steps=len(questions),
            current_step=learning_session.current_step,
            course_type=CourseType.DESCRIPTION,
            next_course=CourseType.ROLEPLAY,
            next_course_number=4,
        )

    async def get_roleplay(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        self._ensure_course(learning_session, CourseType.ROLEPLAY)
        mission = await self._roleplay_mission(learning_session.book_id, learning_session.chapter_number)
        roleplay_messages = await self._roleplay_messages(learning_session.session_id)
        message_count = len(roleplay_messages)
        roleplay_context = roleplay_runtime_context(mission)
        required_turns = roleplay_context["required_turns"]
        course_progress = self.progress_service.course_progress(
            current_step=message_count,
            total_steps=required_turns,
        )
        return {
            "courseType": CourseType.ROLEPLAY.value,
            "courseNumber": 4,
            "courseProgress": course_progress,
            "totalProgress": learning_session.total_progress,
            "mission": {
                "missionId": mission.mission_id,
                "title": mission.title,
                "description": roleplay_context["situation"],
                "playerGoal": roleplay_context["player_goal"],
                "hints": mission.hint_sequence or [],
                "requiredTurns": required_turns,
                "childRole": roleplay_context["child_role"],
            },
            "character": {
                "name": roleplay_context["ai_character"],
                "imageUrl": mission.character_image_url,
            },
            "openingMessage": {
                "speaker": roleplay_context["ai_character"].upper(),
                "text": roleplay_context["opening_message"],
            },
            "messages": [
                {
                    "messageId": message.message_id,
                    "turn": message.turn,
                    "user": {"transcript": message.user_transcript},
                    "character": {
                        "speaker": roleplay_context["ai_character"].upper(),
                        "text": message.character_response,
                    },
                    "score": message.score,
                    "missionCompleted": message.mission_completed,
                }
                for message in roleplay_messages
                if message.mission_id == mission.mission_id
            ],
        }

    async def create_roleplay_message(
        self,
        *,
        profile: ChildProfile,
        session_id: int,
        mission_id: int,
        transcript: str,
    ) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
            for_update=True,
        )
        self._ensure_course(learning_session, CourseType.ROLEPLAY)
        mission = await self._roleplay_mission(learning_session.book_id, learning_session.chapter_number)
        if mission.mission_id != mission_id:
            raise QuestionNotFoundException()

        turn = await self._roleplay_message_count(learning_session.session_id) + 1
        transcript = clean_roleplay_transcript(
            mission,
            transcript or self._roleplay_fallback_transcript(mission, turn),
        )
        roleplay_result = await self.roleplay_service.respond(
            mission=mission,
            session_id=learning_session.session_id,
            transcript=transcript,
            turn=turn,
        )
        character_response = roleplay_result["text"]
        required_turns = roleplay_runtime_context(mission)["required_turns"]
        mission_completed = turn >= required_turns
        total_progress = self.progress_service.total_progress(
            course_type=CourseType.ROLEPLAY,
            current_step=min(turn, required_turns),
            total_steps=required_turns,
        )
        message = RoleplayMessage(
            session_id=learning_session.session_id,
            mission_id=mission.mission_id,
            turn=turn,
            user_transcript=transcript,
            character_response=character_response,
            score=roleplay_result["score"],
            mission_completed=mission_completed,
        )
        self.session.add(message)
        learning_session.total_progress = total_progress
        learning_session.last_studied_at = datetime.now(UTC)
        progress = await self._get_or_create_book_progress(
            profile_id=profile.profile_id,
            book_id=learning_session.book_id,
            now=learning_session.last_studied_at,
        )
        progress.progress = total_progress
        progress.last_studied_at = learning_session.last_studied_at
        await self.session.flush()
        await self.session.commit()
        return {
            "messageId": message.message_id,
            "turn": turn,
            "user": {"transcript": transcript},
            "character": {
                "speaker": roleplay_result["speaker"],
                "text": character_response,
            },
            "score": message.score,
            "source": roleplay_result.get("source"),
            "missionCompleted": mission_completed,
            "courseProgress": self.progress_service.course_progress(
                current_step=min(turn, required_turns),
                total_steps=required_turns,
            ),
            "totalProgress": total_progress,
        }

    async def exit_session(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
            for_update=True,
        )
        if learning_session.status == LearningSessionStatus.COMPLETED:
            raise SessionAlreadyCompletedException()
        learning_session.status = LearningSessionStatus.EXITED
        learning_session.last_studied_at = datetime.now(UTC)
        await self.session.commit()
        return {
            "sessionId": learning_session.session_id,
            "status": learning_session.status.value,
            "bookId": learning_session.book_id,
            "chapterNumber": learning_session.chapter_number,
            "currentCourse": learning_session.current_course.value,
            "currentStep": learning_session.current_step,
            "totalProgress": learning_session.total_progress,
            "saved": True,
        }

    async def complete_session(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
            for_update=True,
        )
        if learning_session.status == LearningSessionStatus.COMPLETED:
            raise SessionAlreadyCompletedException()

        now = datetime.now(UTC)
        attempts = await self._attempts(learning_session.session_id)
        roleplay_messages = await self._roleplay_messages(learning_session.session_id)
        attempts = self._include_skipped_course_attempts(
            learning_session=learning_session,
            attempts=attempts,
        )
        score_result = self.final_score_service.calculate(
            attempts=attempts,
            roleplay_messages=roleplay_messages,
        )
        rewards = self.reward_service.apply_completion_reward(profile)
        learning_session.status = LearningSessionStatus.COMPLETED
        learning_session.completed_at = now
        learning_session.last_studied_at = now
        learning_session.total_progress = max(learning_session.total_progress, 100)
        learning_session.total_score = score_result["totalScore"]
        learning_session.stars = score_result["stars"]
        self.session.add(
            PointTransaction(
                profile_id=profile.profile_id,
                transaction_type="earned",
                amount=rewards["hearts"],
                label=f"Chapter {learning_session.chapter_number} completed",
                reference_type="learning_session",
                reference_id=learning_session.session_id,
            )
        )

        progress = await self._get_or_create_book_progress(
            profile_id=profile.profile_id,
            book_id=learning_session.book_id,
            now=now,
        )
        total_chapters = await self._chapter_count(learning_session.book_id)
        progress.progress = max(progress.progress, round((learning_session.chapter_number / max(total_chapters, 1)) * 100))
        progress.completed = learning_session.chapter_number >= total_chapters
        progress.last_studied_at = now
        review_cards_created = await self.review_service.enqueue_chapter_cards(
            profile_id=profile.profile_id,
            book_id=learning_session.book_id,
            chapter_number=learning_session.chapter_number,
            due_at=now,
        )
        await self.session.commit()
        return {
            "sessionId": learning_session.session_id,
            "status": learning_session.status.value,
            "bookId": learning_session.book_id,
            "chapterNumber": learning_session.chapter_number,
            "totalScore": learning_session.total_score,
            "stars": learning_session.stars,
            "completedAt": learning_session.completed_at.isoformat(),
            "rewards": rewards,
            "reviewCardsCreated": review_cards_created,
        }

    @staticmethod
    def _include_skipped_course_attempts(
        *,
        learning_session: LearningSession,
        attempts: list[LearningAttempt],
    ) -> list[LearningAttempt]:
        if (
            learning_session.current_course != CourseType.ROLEPLAY
            or learning_session.total_progress < 100
        ):
            return attempts

        completed_courses = {attempt.course_type for attempt in attempts}
        scored_attempts = list(attempts)
        for course_type in (CourseType.REPEAT, CourseType.DESCRIPTION):
            if course_type in completed_courses:
                continue
            scored_attempts.append(
                LearningAttempt(
                    session_id=learning_session.session_id,
                    course_type=course_type,
                    question_id=0,
                    transcript="",
                    score=0,
                    passed=False,
                    feedback="Skipped",
                )
            )
        return scored_attempts

    async def get_result(self, *, profile: ChildProfile, session_id: int) -> dict:
        learning_session = await self.get_owned_session(
            profile_id=profile.profile_id,
            session_id=session_id,
        )
        if learning_session.status != LearningSessionStatus.COMPLETED:
            raise ResultNotAvailableException()
        profile_data = {
            "profileId": profile.profile_id,
            "nickname": profile.nickname,
        }
        book = await self._book(learning_session.book_id)
        return {
            "profile": profile_data,
            "book": {
                "bookId": book.book_id,
                "title": book.title,
            },
            "chapterNumber": learning_session.chapter_number,
            "totalScore": learning_session.total_score,
            "stars": learning_session.stars,
            "completed": True,
            "completedAt": learning_session.completed_at.isoformat(),
        }

    async def get_owned_session(
        self,
        *,
        profile_id: int,
        session_id: int,
        for_update: bool = False,
    ) -> LearningSession:
        stmt = select(LearningSession).where(LearningSession.session_id == session_id)
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        learning_session = result.scalar_one_or_none()
        if learning_session is None:
            raise SessionNotFoundException()
        if learning_session.profile_id != profile_id:
            raise SessionAccessDeniedException()
        return learning_session

    async def _active_or_exited_session(
        self,
        *,
        profile_id: int,
        book_id: int,
        chapter_number: int,
        for_update: bool = False,
    ) -> LearningSession | None:
        stmt = (
            select(LearningSession)
            .where(
                LearningSession.profile_id == profile_id,
                LearningSession.book_id == book_id,
                LearningSession.chapter_number == chapter_number,
                LearningSession.status.in_(
                    [
                        LearningSessionStatus.IN_PROGRESS,
                        LearningSessionStatus.EXITED,
                    ]
                ),
            )
            .order_by(LearningSession.session_id.desc())
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def _ensure_book_exists(self, book_id: int) -> None:
        result = await self.session.execute(select(Book).where(Book.book_id == book_id))
        if result.scalar_one_or_none() is None:
            raise BookNotFoundException()

    async def _ensure_book_available_for_profile(
        self,
        *,
        profile: ChildProfile,
        book_id: int,
    ) -> None:
        result = await self.session.execute(select(Book).where(Book.book_id == book_id))
        book = result.scalar_one_or_none()
        if book is None:
            raise BookNotFoundException()
        if profile.difficulty is not None and book.difficulty != profile.difficulty:
            raise BookLockedException()

    async def _book(self, book_id: int) -> Book:
        result = await self.session.execute(select(Book).where(Book.book_id == book_id))
        book = result.scalar_one_or_none()
        if book is None:
            raise BookNotFoundException()
        return book

    async def _chapter_count(self, book_id: int) -> int:
        result = await self.session.execute(
            select(func.count(func.distinct(ReadingChunk.chapter_number)))
            .where(ReadingChunk.book_id == book_id)
        )
        return int(result.scalar_one() or 1)

    async def _reading_chunks(self, book_id: int, chapter_number: int) -> list[ReadingChunk]:
        result = await self.session.execute(
            select(ReadingChunk)
            .where(ReadingChunk.book_id == book_id, ReadingChunk.chapter_number == chapter_number)
            .order_by(ReadingChunk.step)
        )
        return list(result.scalars().all())

    async def _roleplay_mission(self, book_id: int, chapter_number: int) -> RoleplayMission:
        result = await self.session.execute(
            select(RoleplayMission)
            .where(RoleplayMission.book_id == book_id, RoleplayMission.chapter_number == chapter_number)
            .order_by(RoleplayMission.mission_id)
        )
        mission = result.scalars().first()
        if mission is not None:
            return mission
        raise QuestionNotFoundException()

    async def _roleplay_message_count(self, session_id: int) -> int:
        result = await self.session.execute(
            select(RoleplayMessage).where(RoleplayMessage.session_id == session_id)
        )
        return len(result.scalars().all())

    @staticmethod
    def _roleplay_fallback_transcript(mission: RoleplayMission, turn: int) -> str:
        examples = [
            mission.model_answer,
            *(mission.similar_answers or []),
            mission.player_goal,
        ]
        examples = [example.strip() for example in examples if example and example.strip()]
        if not examples:
            return "I can help."
        return examples[min(max(turn - 1, 0), len(examples) - 1)]

    async def _roleplay_messages(self, session_id: int) -> list[RoleplayMessage]:
        result = await self.session.execute(
            select(RoleplayMessage)
            .where(RoleplayMessage.session_id == session_id)
            .order_by(RoleplayMessage.turn, RoleplayMessage.message_id)
        )
        return list(result.scalars().all())

    async def _attempts(self, session_id: int) -> list[LearningAttempt]:
        result = await self.session.execute(
            select(LearningAttempt).where(LearningAttempt.session_id == session_id)
        )
        return list(result.scalars().all())

    async def _repeat_questions(self, book_id: int, chapter_number: int) -> list[RepeatQuestion]:
        result = await self.session.execute(
            select(RepeatQuestion)
            .where(RepeatQuestion.book_id == book_id, RepeatQuestion.chapter_number == chapter_number)
            .order_by(RepeatQuestion.step)
        )
        return list(result.scalars().all())

    async def _description_questions(self, book_id: int, chapter_number: int) -> list[DescriptionQuestion]:
        result = await self.session.execute(
            select(DescriptionQuestion)
            .where(DescriptionQuestion.book_id == book_id, DescriptionQuestion.chapter_number == chapter_number)
            .order_by(DescriptionQuestion.step)
        )
        return list(result.scalars().all())

    async def _current_repeat_question(
        self,
        learning_session: LearningSession,
        question_id: int,
    ) -> RepeatQuestion:
        questions = await self._repeat_questions(learning_session.book_id, learning_session.chapter_number)
        question = self._question_for_step(questions, learning_session.current_step)
        if question.question_id != question_id:
            raise InvalidStepException()
        return question

    async def _current_description_question(
        self,
        learning_session: LearningSession,
        question_id: int,
    ) -> DescriptionQuestion:
        questions = await self._description_questions(learning_session.book_id, learning_session.chapter_number)
        question = self._question_for_step(questions, learning_session.current_step)
        if question.question_id != question_id:
            raise InvalidStepException()
        return question

    async def _has_attempt(
        self,
        session_id: int,
        course_type: CourseType,
        question_id: int,
    ) -> bool:
        result = await self.session.execute(
            select(LearningAttempt).where(
                LearningAttempt.session_id == session_id,
                LearningAttempt.course_type == course_type,
                LearningAttempt.question_id == question_id,
            ).limit(1)
        )
        return result.scalars().first() is not None

    async def _advance_question_course(
        self,
        *,
        learning_session: LearningSession,
        profile: ChildProfile,
        total_steps: int,
        current_step: int,
        course_type: CourseType,
        next_course: CourseType,
        next_course_number: int,
    ) -> dict:
        now = datetime.now(UTC)
        progress = await self._get_or_create_book_progress(
            profile_id=profile.profile_id,
            book_id=learning_session.book_id,
            now=now,
        )
        if current_step >= total_steps:
            learning_session.current_course = next_course
            learning_session.current_course_number = next_course_number
            learning_session.current_step = 1
            learning_session.total_progress = self.progress_service.COURSE_RANGES[course_type][1]
            learning_session.last_studied_at = now
            progress.progress = learning_session.total_progress
            progress.last_studied_at = now
            await self.session.commit()
            return {
                "courseProgress": 100,
                "totalProgress": learning_session.total_progress,
                "courseCompleted": True,
                "nextCourse": next_course.value,
            }

        next_step = current_step + 1
        total_progress = self.progress_service.total_progress(
            course_type=course_type,
            current_step=next_step,
            total_steps=total_steps,
        )
        learning_session.current_step = next_step
        learning_session.total_progress = total_progress
        learning_session.last_studied_at = now
        progress.progress = total_progress
        progress.last_studied_at = now
        await self.session.commit()
        return {
            "currentStep": next_step,
            "totalSteps": total_steps,
            "courseProgress": self.progress_service.course_progress(
                current_step=next_step,
                total_steps=total_steps,
            ),
            "totalProgress": total_progress,
            "courseCompleted": False,
        }

    async def _get_or_create_book_progress(
        self,
        *,
        profile_id: int,
        book_id: int,
        now: datetime,
    ) -> UserBookProgress:
        result = await self.session.execute(
            select(UserBookProgress)
            .where(
                UserBookProgress.profile_id == profile_id,
                UserBookProgress.book_id == book_id,
            )
            .with_for_update()
        )
        progress = result.scalar_one_or_none()
        if progress is not None:
            progress.unlocked = True
            return progress

        progress = UserBookProgress(
            profile_id=profile_id,
            book_id=book_id,
            progress=0,
            completed=False,
            unlocked=True,
            last_studied_at=now,
        )
        self.session.add(progress)
        await self.session.flush()
        return progress

    @staticmethod
    def _ensure_reading_course(learning_session: LearningSession) -> None:
        if learning_session.current_course != CourseType.READING:
            raise InvalidCourseStateException()

    @staticmethod
    def _ensure_course(
        learning_session: LearningSession,
        course_type: CourseType,
    ) -> None:
        if learning_session.current_course != course_type:
            raise InvalidCourseStateException()

    @staticmethod
    def _chunk_for_step(chunks: list[ReadingChunk], step: int) -> ReadingChunk:
        for chunk in chunks:
            if chunk.step == step:
                return chunk
        raise AppException(status_code=404, detail="읽기 콘텐츠를 찾을 수 없습니다.")

    @staticmethod
    def _question_for_step(questions, step: int):
        for question in questions:
            if question.step == step:
                return question
        raise QuestionNotFoundException()

    @staticmethod
    def chunk_content(chunk: ReadingChunk) -> dict:
        return {
            "chunkId": chunk.chunk_id,
            "text": chunk.text,
            "imageUrl": chunk.image_url,
        }

    @staticmethod
    def session_response(learning_session: LearningSession, *, is_new: bool) -> dict:
        return {
            "sessionId": learning_session.session_id,
            "bookId": learning_session.book_id,
            "chapterNumber": learning_session.chapter_number,
            "isNew": is_new,
            "status": learning_session.status.value,
            "currentCourse": learning_session.current_course.value,
            "currentCourseNumber": learning_session.current_course_number,
            "currentStep": learning_session.current_step,
            "totalProgress": learning_session.total_progress,
        }

    @staticmethod
    def session_detail_response(learning_session: LearningSession) -> dict:
        return {
            "sessionId": learning_session.session_id,
            "bookId": learning_session.book_id,
            "chapterNumber": learning_session.chapter_number,
            "status": learning_session.status.value,
            "currentCourse": learning_session.current_course.value,
            "currentCourseNumber": learning_session.current_course_number,
            "currentStep": learning_session.current_step,
            "totalProgress": learning_session.total_progress,
            "startedAt": learning_session.started_at.isoformat(),
            "lastStudiedAt": learning_session.last_studied_at.isoformat(),
        }

    @staticmethod
    def result_response(learning_session: LearningSession) -> dict:
        return {
            "sessionId": learning_session.session_id,
            "bookId": learning_session.book_id,
            "status": learning_session.status.value,
            "totalProgress": learning_session.total_progress,
            "totalScore": learning_session.total_score,
            "stars": learning_session.stars,
            "completedAt": (
                learning_session.completed_at.isoformat()
                if learning_session.completed_at
                else None
            ),
        }
