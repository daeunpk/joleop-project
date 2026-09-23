import { useCallback, useRef } from 'react'

interface AudioPlayOptions {
  onTimeUpdate?: (audio: HTMLAudioElement) => void
  onEnded?: () => void
  onError?: () => void
}

export function useAudioPlayer() {
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const frameRef = useRef<number | null>(null)

  const stopFrame = useCallback(() => {
    if (frameRef.current !== null) {
      window.cancelAnimationFrame(frameRef.current)
      frameRef.current = null
    }
  }, [])

  const play = useCallback((url: string, options: AudioPlayOptions = {}): Promise<void> => {
    stopFrame()
    audioRef.current?.pause()
    const audio = new Audio(url)
    const tick = () => {
      options.onTimeUpdate?.(audio)
      if (!audio.paused && !audio.ended) {
        frameRef.current = window.requestAnimationFrame(tick)
      }
    }
    audio.ontimeupdate = () => options.onTimeUpdate?.(audio)
    audio.onplay = tick
    audio.onended = () => {
      stopFrame()
      options.onEnded?.()
    }
    audio.onerror = () => {
      stopFrame()
      options.onError?.()
    }
    audioRef.current = audio
    return audio.play()
  }, [stopFrame])

  const stop = useCallback(() => {
    stopFrame()
    audioRef.current?.pause()
    audioRef.current = null
  }, [stopFrame])

  return { play, stop }
}
