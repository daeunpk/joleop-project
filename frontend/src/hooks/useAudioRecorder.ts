import { useState, useCallback } from 'react'

export type RecorderState = 'idle' | 'recording'

const RECORD_DURATION_MS = 4000

export function useAudioRecorder() {
  const [state, setState] = useState<RecorderState>('idle')

  const record = useCallback(async (durationMs = RECORD_DURATION_MS): Promise<Blob> => {
    setState('recording')

    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setState('idle')
      throw new Error('Microphone permission is needed.')
    }

    const recorder = new MediaRecorder(stream)
    const chunks: Blob[] = []
    recorder.ondataavailable = (e) => { if (e.data.size > 0) chunks.push(e.data) }
    recorder.start()

    await new Promise<void>((resolve) => setTimeout(resolve, durationMs))

    return new Promise<Blob>((resolve) => {
      recorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop())
        setState('idle')
        resolve(new Blob(chunks, { type: recorder.mimeType || 'audio/webm' }))
      }
      recorder.stop()
    })
  }, [])

  return { state, record }
}
