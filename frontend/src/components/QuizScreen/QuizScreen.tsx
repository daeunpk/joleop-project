import { useEffect, useState } from 'react'
import type { QuizQuestion } from '../../types'
import { IMAGES } from '../../constants/assets'
import ResponsiveSceneImage from '../ResponsiveSceneImage/ResponsiveSceneImage'
import styles from './QuizScreen.module.css'

const QUIZ_MAX_RECORD_MS = 12000
const QUIZ_SILENCE_MS = 2200

type QuizState = 'idle' | 'recording' | 'done'
type QuizFeedback = 'correct' | 'wrong' | ''

interface QuizRecordResult {
  transcript: string
  passed: boolean
}

interface Props {
  quiz: QuizQuestion
  onNext: () => void
  onRecord: (audio: Blob, transcript?: string) => Promise<void | number | boolean | QuizRecordResult>
  currentStep?: number
  totalSteps?: number
}

function recordQuizSpeech(durationMs = QUIZ_MAX_RECORD_MS): Promise<{ audio: Blob; transcript: string }> {
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
    let transcript = ''
    let settled = false
    let maxTimer: number | null = null
    let silenceTimer: number | null = null

    const cleanup = () => {
      if (maxTimer !== null) window.clearTimeout(maxTimer)
      if (silenceTimer !== null) window.clearTimeout(silenceTimer)
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
      if (mediaRecorder.state !== 'inactive') mediaRecorder.stop()
    }

    const restartSilenceTimer = () => {
      if (silenceTimer !== null) window.clearTimeout(silenceTimer)
      if (!transcript.trim()) return
      silenceTimer = window.setTimeout(finish, QUIZ_SILENCE_MS)
    }

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) chunks.push(event.data)
    }
    mediaRecorder.onerror = () => {
      cleanup()
      reject(new Error('Recording failed.'))
    }
    mediaRecorder.onstop = () => {
      cleanup()
      resolve({
        audio: new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' }),
        transcript: transcript.trim(),
      })
    }

    mediaRecorder.start(250)
    maxTimer = window.setTimeout(finish, durationMs)

    if (!recognition) return

    recognition.lang = 'en-US'
    recognition.interimResults = true
    recognition.continuous = false
    recognition.maxAlternatives = 1
    recognition.onresult = (event) => {
      transcript = Array.from(event.results)
        .map((result) => result[0]?.transcript ?? '')
        .join(' ')
        .trim()
      if (event.results[event.results.length - 1]?.isFinal) {
        finish()
        return
      }
      restartSilenceTimer()
    }
    recognition.onerror = (event) => {
      if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
        return
      }
      if (transcript.trim()) finish()
    }
    recognition.onend = () => {
      if (transcript.trim()) finish()
    }

    try {
      recognition.start()
    } catch {
      // Keep the audio recording as a fallback for backend-side handling.
    }
  })
}

export default function QuizScreen({ quiz, onNext, onRecord, currentStep, totalSteps }: Props) {
  const [state, setState] = useState<QuizState>('idle')
  const [error, setError] = useState('')
  const [feedback, setFeedback] = useState<QuizFeedback>('')
  const [spokenAnswer, setSpokenAnswer] = useState('')

  useEffect(() => {
    setState('idle')
    setError('')
    setFeedback('')
    setSpokenAnswer('')
  }, [quiz.question, quiz.sentence, quiz.answer])

  const handleMicTap = async () => {
    if (state === 'done') {
      onNext()
      return
    }
    if (state === 'idle') {
      setState('recording')
      setError('')
      try {
        const recording = await recordQuizSpeech()
        const result = await onRecord(recording.audio, recording.transcript)
        if (typeof result === 'boolean') {
          setFeedback(result ? 'correct' : 'wrong')
        } else if (typeof result === 'number') {
          setFeedback(result >= 70 ? 'correct' : 'wrong')
        } else if (result && typeof result === 'object') {
          setSpokenAnswer(result.transcript)
          if (!result.passed && (!result.transcript.trim() || result.transcript.trim().length <= 1)) {
            setState('idle')
            setError('Could not hear that. Please try again.')
            return
          }
          setFeedback(result.passed ? 'correct' : 'wrong')
        }
        setState('done')
      } catch {
        setState('idle')
        setError('Could not hear that. Please try again.')
      }
    }
  }

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>

      <div className={styles.illustrationWrapper}>
        <div
          className={styles.illustration}
          style={{ background: quiz.imageColor }}
          aria-label="Quiz picture"
        >
          {quiz.imageUrl
            ? <ResponsiveSceneImage src={quiz.imageUrl} alt="" className={styles.sceneImage} />
            : <span>📖</span>
          }
        </div>
        <div className={styles.quizCard}>
          {currentStep && totalSteps && (
            <b className={styles.quizStep}>
              {currentStep}/{totalSteps}
            </b>
          )}
          <span className={styles.quizCardText}>{quiz.question}</span>
        </div>
      </div>

      <div className={styles.sentenceBoxWrapper}>
        <div className={styles.sentenceBox}>
          <p className={`${styles.sentence} ${state === 'done' ? styles.sentenceDone : ''}`}>
            {quiz.sentence}{' '}
            <span className={[
              styles.blank,
              state === 'done' ? styles.blankFilled : '',
              feedback === 'correct' ? styles.blankCorrect : '',
              feedback === 'wrong' ? styles.blankWrong : '',
            ].join(' ')}>
              {state === 'done' ? spokenAnswer : ''}
            </span>
          </p>
        </div>
      </div>

      <div className={styles.bottomArea}>
        {error && <p className={styles.errorText}>{error}</p>}
        {feedback && (
          <p className={`${styles.feedbackText} ${styles[feedback]}`}>
            {feedback === 'correct' ? 'Correct!' : 'Wrong!'}
          </p>
        )}
        {feedback === 'wrong' && (
          <div className={styles.answerBox} aria-label="Correct answer">
            <span className={styles.answerLabel}>Answer</span>
            <strong className={styles.answerWord}>{quiz.answer}</strong>
          </div>
        )}
        {state === 'done' ? (
          <button className={styles.imgBtn} onClick={onNext} aria-label="Next">
            <img src={IMAGES.nextBtnActive} alt="Next" className={styles.btnImg} />
          </button>
        ) : (
          <button
            className={styles.imgBtn}
            onClick={handleMicTap}
            aria-label={state === 'recording' ? 'Recording...' : 'Tap to speak'}
          >
            <img
              src={state === 'recording' ? IMAGES.recordBtnActive : IMAGES.recordBtnInactive}
              alt={state === 'recording' ? 'Recording' : 'Tap to speak'}
              className={`${styles.btnImg} ${state === 'recording' ? styles.recording : ''}`}
            />
          </button>
        )}
      </div>

    </div>
  )
}
