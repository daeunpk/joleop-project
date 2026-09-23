import { useCallback, useState, useRef, useEffect } from 'react'
import lottie from 'lottie-web'
import type { RoleplayMission } from '../../types'
import type { ChapterResult } from '../../utils/chapterProgress'
import { IMAGES, SOUNDS } from '../../constants/assets'
import StarRow from '../StarRow/StarRow'
import { playEffect, wait } from '../../utils/sound'
import styles from './RoleplayScreen.module.css'

function TrophyAnimation({ className }: { className?: string }) {
  const ref = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!ref.current) return
    const anim = lottie.loadAnimation({
      container: ref.current,
      renderer: 'svg',
      loop: false,
      autoplay: true,
      path: '/animations/Trophy.json',
    })
    return () => anim.destroy()
  }, [])
  return <div ref={ref} className={className} />
}

// Progress range: intro starts at 70%, chat fills the remaining 30% as turns complete
const PROGRESS_INTRO = 0.70
const PROGRESS_CHAT_RANGE = 0.30

const ROLEPLAY_INITIAL_SILENCE_TIMEOUT_MS = 5000
const ROLEPLAY_AFTER_SPEECH_TIMEOUT_MS = 1800
const ROLEPLAY_MAX_RECORD_MS = 12000
/**
 * 결과 화면 등장 순서.
 * 트로피(+소리) → Nice Try → 회색 별 3개 → 보상 별 하나씩(+소리) → 포인트 → 설명 → 버튼
 */
const REVEAL_TROPHY = 0
const REVEAL_TITLE = 1
const REVEAL_STARS = 2
const REVEAL_POINTS = 3
const REVEAL_NOTE = 4
const REVEAL_BUTTON = 5

const REVEAL_STEP_MS = 280        // 단계 사이 간격
const REVEAL_TITLE_MS = 380       // 트로피 소리가 나는 동안 제목이 먼저 뜬다
const REVEAL_GREY_HOLD_MS = 300   // 제목 → 회색 별
const REVEAL_STAR_START_MS = 250  // 회색 별을 잠깐 보여주고 점등 시작
/**
 * 별 사이 간격. star.mp3 는 파일 길이가 1.04초지만 들리는 소리는 0.5초에
 * 끝나므로, ended 를 기다리지 않고 이 간격으로 다음 별을 띄운다.
 */
const STAR_INTERVAL_MS = 500

type RoleplayView = 'intro' | 'chat'
type RecordState = 'idle' | 'recording'

interface Props {
  roleplay: RoleplayMission
  onProgressChange: (v: number) => void
  onFinish: () => Promise<ChapterResult | null> | ChapterResult | null
  onExit: () => void
  onSpeakText?: (text: string) => Promise<void> | void
  onRecord: (audio: Blob, transcript?: string) => Promise<{
    userTranscript: string
    characterText: string
    missionCompleted: boolean
    score?: number
  }>
  variant?: 'lesson' | 'review'
}

function recordRoleplaySpeech(durationMs = ROLEPLAY_MAX_RECORD_MS): Promise<{ audio: Blob; transcript: string }> {
  return new Promise(async (resolve, reject) => {
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      reject(new Error('Microphone permission is needed.'))
      return
    }

    const chunks: Blob[] = []
    const mediaRecorder = new MediaRecorder(stream)
    const Recognition = window.SpeechRecognition ?? window.webkitSpeechRecognition
    const recognition = Recognition ? new Recognition() : null
    let finalTranscript = ''
    let interimTranscript = ''
    let settled = false
    let hasSpeech = false
    let silenceTimer: number | null = null
    const maxRecordTimer = window.setTimeout(() => finish(), durationMs)

    const currentTranscript = () => `${finalTranscript} ${interimTranscript}`.trim()

    const cleanup = () => {
      if (silenceTimer !== null) window.clearTimeout(silenceTimer)
      window.clearTimeout(maxRecordTimer)
      try {
        recognition?.abort()
      } catch {
        // Recognition may already be stopped.
      }
      stream.getTracks().forEach((track) => track.stop())
    }

    const finish = () => {
      if (settled) return
      settled = true
      if (silenceTimer !== null) window.clearTimeout(silenceTimer)
      if (mediaRecorder.state !== 'inactive') {
        mediaRecorder.stop()
        return
      }
      complete()
    }

    const complete = () => {
      const transcript = currentTranscript()
      cleanup()
      resolve({
        audio: new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' }),
        transcript,
      })
    }

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    mediaRecorder.onerror = () => {
      cleanup()
      reject(new Error('Recording failed.'))
    }
    mediaRecorder.onstop = complete
    mediaRecorder.start(250)

    const restartSilenceTimer = () => {
      if (silenceTimer !== null) window.clearTimeout(silenceTimer)
      silenceTimer = window.setTimeout(
        () => finish(),
        hasSpeech ? ROLEPLAY_AFTER_SPEECH_TIMEOUT_MS : ROLEPLAY_INITIAL_SILENCE_TIMEOUT_MS,
      )
    }

    if (!recognition) {
      return
    }

    recognition.lang = 'en-US'
    recognition.interimResults = true
    recognition.continuous = true
    recognition.maxAlternatives = 1
    recognition.onresult = (event) => {
      interimTranscript = ''
      for (let index = event.resultIndex; index < event.results.length; index += 1) {
        const transcript = event.results[index][0]?.transcript ?? ''
        if (event.results[index].isFinal) finalTranscript = `${finalTranscript} ${transcript}`.trim()
        else interimTranscript = `${interimTranscript} ${transcript}`.trim()
      }
      hasSpeech = Boolean(currentTranscript())
      restartSilenceTimer()
    }
    ;(recognition as SpeechRecognition & { onspeechend?: () => void }).onspeechend = () => {
      hasSpeech = hasSpeech || Boolean(currentTranscript())
      restartSilenceTimer()
    }
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        return
      }
      restartSilenceTimer()
    }
    recognition.onend = () => {
      if (!settled && !hasSpeech) {
        try {
          recognition.start()
        } catch {
          restartSilenceTimer()
        }
        return
      }
      if (!settled) restartSilenceTimer()
    }

    try {
      recognition.start()
    } catch {
      restartSilenceTimer()
    }
  })
}

function initialUserAnswers(roleplay: RoleplayMission) {
  return roleplay.history?.map((turn) => turn.user).filter(Boolean) ?? []
}

function initialNpcReplies(roleplay: RoleplayMission) {
  const replies = roleplay.turns.map((turn) => turn.npc)
  roleplay.history?.forEach((turn, index) => {
    replies[index + 1] = turn.npc
  })
  return replies
}

export default function RoleplayScreen({
  roleplay,
  onProgressChange,
  onFinish,
  onExit,
  onSpeakText,
  onRecord,
  variant = 'lesson',
}: Props) {
  const [view, setView] = useState<RoleplayView>(() => roleplay.history?.length ? 'chat' : 'intro')
  const [userAnswers, setUserAnswers] = useState<string[]>(() => initialUserAnswers(roleplay))
  const [npcReplies, setNpcReplies] = useState<string[]>(() => initialNpcReplies(roleplay))
  const [recordState, setRecordState] = useState<RecordState>('idle')
  const [serverCompleted, setServerCompleted] = useState(false)
  const [showFinalNpc, setShowFinalNpc] = useState(false)
  const [showCompletion, setShowCompletion] = useState(false)
  const [revealStage, setRevealStage] = useState(REVEAL_TROPHY)
  const [litStars, setLitStars] = useState(0)
  const revealStartedRef = useRef(false)
  const revealCancelledRef = useRef(false)
  const [finalResult, setFinalResult] = useState<ChapterResult | null>(null)
  const [isFinalizing, setIsFinalizing] = useState(false)
  const [speechError, setSpeechError] = useState('')
  const [finishError, setFinishError] = useState('')
  const chatBottomRef = useRef<HTMLDivElement>(null)
  const roleplayKey = [
    roleplay.mission,
    roleplay.missionSummary,
    roleplay.turns.length,
    roleplay.history?.map((turn) => `${turn.user}=>${turn.npc}`).join('|') ?? '',
  ].join('::')

  useEffect(() => {
    setView(roleplay.history?.length ? 'chat' : 'intro')
    setUserAnswers(initialUserAnswers(roleplay))
    setNpcReplies(initialNpcReplies(roleplay))
    setRecordState('idle')
    setServerCompleted(false)
    setShowFinalNpc(Boolean(roleplay.history?.length && roleplay.history.length >= roleplay.turns.length))
    setShowCompletion(false)
    setRevealStage(REVEAL_TROPHY)
    setLitStars(0)
    revealStartedRef.current = false
    setFinalResult(null)
    setSpeechError('')
    setFinishError('')
  }, [roleplayKey])

  useEffect(() => {
    const progress = view === 'intro'
      ? PROGRESS_INTRO
      : PROGRESS_INTRO + (userAnswers.length / roleplay.turns.length) * PROGRESS_CHAT_RANGE
    onProgressChange(progress)
  }, [view, userAnswers, roleplay.turns.length, onProgressChange])

  useEffect(() => {
    chatBottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [userAnswers])

  const isDone = serverCompleted || userAnswers.length >= roleplay.turns.length

  const handleRecord = async () => {
    if (recordState !== 'idle' || isDone) return
    const currentIdx = userAnswers.length
    setRecordState('recording')
    setSpeechError('')
    try {
      const { audio: blob, transcript } = await recordRoleplaySpeech()
      const cleanTranscript = transcript.trim()
      if (!cleanTranscript) {
        setSpeechError('Could not hear that. Please try again.')
        setRecordState('idle')
        return
      }
      const result = await onRecord(blob, cleanTranscript)
      if (!result.userTranscript.trim()) {
        setSpeechError('Could not hear that. Please try again.')
        setRecordState('idle')
        return
      }
      setUserAnswers(prev => [...prev, result.userTranscript])
      setNpcReplies(prev => {
        const next = [...prev]
        next[currentIdx + 1] = result.characterText
        return next
      })
      void onSpeakText?.(result.characterText)
      if (result.missionCompleted) {
        setServerCompleted(true)
      }
      if (result.missionCompleted || currentIdx + 1 >= roleplay.turns.length) {
        setShowFinalNpc(true)
      }
      setRecordState('idle')
    } catch (error) {
      setSpeechError(error instanceof Error ? error.message : 'Recording failed. Please try again.')
      setRecordState('idle')
    }
  }

  // 트로피가 뜨는 순간 트로피 소리를 함께 재생한다.
  useEffect(() => {
    if (!showCompletion) return
    const audio = new Audio(SOUNDS.trophy)
    audio.play().catch(() => undefined)
    return () => {
      audio.pause()
    }
  }, [showCompletion])

  // 마운트 때 반드시 false 로 되돌린다. StrictMode 는 마운트 → 언마운트 →
  // 재마운트를 하는데, 본문 없이 클린업만 두면 그 언마운트에서 true 로 고정돼
  // 이후 시퀀스가 첫 alive() 검사에서 전부 중단된다.
  useEffect(() => {
    revealCancelledRef.current = false
    return () => {
      revealCancelledRef.current = true
    }
  }, [])

  const runReveal = useCallback(async (stars: number) => {
    if (revealStartedRef.current) return
    revealStartedRef.current = true
    const alive = () => !revealCancelledRef.current

    await wait(REVEAL_TITLE_MS)
    if (!alive()) return
    setRevealStage(REVEAL_TITLE)          // Excellent! / Nice Try / Try Again

    await wait(REVEAL_GREY_HOLD_MS)
    if (!alive()) return
    setRevealStage(REVEAL_STARS)          // 회색 별 3개 동시 등장
    await wait(REVEAL_STAR_START_MS)

    for (let index = 0; index < stars; index += 1) {
      if (!alive()) return
      setLitStars(index + 1)
      playEffect(SOUNDS.star)
      await wait(STAR_INTERVAL_MS)        // 앞 별의 0.5초 지점에서 다음 별
    }
    if (!alive()) return

    setRevealStage(REVEAL_POINTS)
    await wait(REVEAL_STEP_MS)
    if (!alive()) return
    setRevealStage(REVEAL_NOTE)
    await wait(REVEAL_STEP_MS)
    if (!alive()) return
    setRevealStage(REVEAL_BUTTON)
  }, [])

  // 오버레이가 열리는 순간 시퀀스를 시작한다. 트로피 애니메이션 완료를
  // 기다리면 별이 2.5초쯤에야 나오므로, 제목 직후로 당기려고 타이머로 잇는다.
  useEffect(() => {
    if (!showCompletion || !finalResult) return
    runReveal(finalResult.stars)
  }, [showCompletion, finalResult, runReveal])

  const handleCompleteLesson = async () => {
    if (isFinalizing) return
    setIsFinalizing(true)
    setFinishError('')
    try {
      const result = await Promise.resolve(onFinish())
      if (result) {
        setFinalResult(result)
        setShowCompletion(true)
      }
    } catch {
      setFinishError('Could not complete the lesson. Please try again.')
    } finally {
      setIsFinalizing(false)
    }
  }

  if (view === 'intro') {
    return (
      <div className={`${styles.introPage} ${variant === 'review' ? styles.reviewIntroPage : ''}`}>
        <div className={styles.introContent}>
          <div
            className={styles.thumbnail}
            style={{ background: roleplay.thumbnailColor }}
          >
            {roleplay.thumbnailUrl
              ? <img src={roleplay.thumbnailUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
              : <span>🎭</span>
            }
          </div>
          <div className={styles.missionCard}>
            <div className={styles.missionBadge}>Mission</div>
            <p className={styles.missionText}>{roleplay.mission}</p>
          </div>
        </div>
        <div className={styles.introBottom}>
          <button
            className={styles.imgBtn}
            onClick={() => {
              setView('chat')
              void onSpeakText?.(roleplay.turns[0]?.npc ?? '')
            }}
            aria-label="Start"
          >
            <img src={IMAGES.nextBtnActive} alt="Start" className={styles.btnImg} />
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={`${styles.chatPage} ${variant === 'review' ? styles.reviewChatPage : ''}`}>

      <div className={styles.chatHeader}>
        <div className={styles.missionSummaryCard}>
          <span className={styles.missionSummaryLabel}>Situation</span>
          <span className={styles.missionSummaryText}>{roleplay.missionSummary}</span>
        </div>
      </div>

      <div className={styles.chatArea}>
        {roleplay.turns.map((turn, i) => (
          <div key={i} className={styles.turnGroup}>
            {userAnswers.length >= i && (
              <div className={styles.npcBubble}>{npcReplies[i] ?? turn.npc}</div>
            )}
            {userAnswers.length > i && (
              <div className={styles.userBubble}>{userAnswers[i]}</div>
            )}
          </div>
        ))}
        {showFinalNpc && (
          <div className={styles.npcBubble}>{npcReplies[userAnswers.length] ?? roleplay.finalNpc}</div>
        )}
        <div ref={chatBottomRef} />
      </div>

      <div className={styles.chatBottom}>
        {speechError && <p className={styles.speechError}>{speechError}</p>}
        {!isDone && (
          <button
            className={styles.imgBtn}
            onClick={handleRecord}
            disabled={recordState === 'recording'}
            aria-label={recordState === 'recording' ? 'Recording...' : 'Tap to speak'}
          >
            <img
              src={recordState === 'recording' ? IMAGES.recordBtnActive : IMAGES.recordBtnInactive}
              alt={recordState === 'recording' ? 'Recording' : 'Tap to speak'}
              className={`${styles.btnImg} ${recordState === 'recording' ? styles.recording : ''}`}
            />
          </button>
        )}
        {isDone && (
          <section className={styles.finishPanel}>
            <div>
              <strong>Roleplay finished!</strong>
              <p>Check your chat, then complete the lesson.</p>
            </div>
            {finishError && <p className={styles.finishError}>{finishError}</p>}
            <button
              className={styles.finishButton}
              onClick={handleCompleteLesson}
              disabled={isFinalizing}
            >
              {isFinalizing ? 'Saving...' : 'Complete Lesson'}
            </button>
          </section>
        )}
      </div>

      {showCompletion && finalResult && (
        <div className={styles.completionOverlay}>
          <>
            <div className={styles.trophyWrapper}>
              <TrophyAnimation className={styles.trophyAnim} />
            </div>
            <section className={styles.completionResult}>
              <span
                className={`${styles.completionBadge} ${styles.reveal} ${revealStage >= REVEAL_TITLE ? styles.revealed : ''}`}
              >
                Chapter {finalResult.chapterNumber}
              </span>
              <h1 className={`${styles.reveal} ${revealStage >= REVEAL_TITLE ? styles.revealed : ''}`}>
                {finalResult.message}
              </h1>
              <StarRow
                lit={litStars}
                size={46}
                gap={12}
                animateLit
                label={`${finalResult.stars} of 3 stars`}
                className={`${styles.starFan} ${styles.reveal} ${revealStage >= REVEAL_STARS ? styles.revealed : ''}`}
              />
              <strong className={`${styles.reveal} ${revealStage >= REVEAL_POINTS ? styles.revealed : ''}`}>
                {`${finalResult.totalScore} points`}
              </strong>
              <p className={`${styles.reveal} ${revealStage >= REVEAL_NOTE ? styles.revealed : ''}`}>
                {finalResult.totalScore >= 80
                  ? 'You spoke clearly and used the story words well.'
                  : 'Good effort. Try one more chapter to make the sentences smoother.'}
              </p>
            </section>
            <button
              className={`${styles.imgBtn} ${styles.completionBtn} ${styles.reveal} ${revealStage >= REVEAL_BUTTON ? styles.revealed : ''}`}
              onClick={onExit}
              aria-label="Back to chapters"
            >
              <img src={IMAGES.nextBtnActive} alt="Back to chapters" className={styles.btnImg} />
            </button>
          </>
        </div>
      )}

    </div>
  )
}
