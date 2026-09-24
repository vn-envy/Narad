import { useState, useCallback, useEffect, useRef } from 'react'
import { toast } from 'sonner'
import { splitForSpeech } from '@/lib/speech-segments'
import { SpeechQueue, unlockAudio } from '@/lib/speech-queue'

export type TTSAvatar = 'Krishna' | 'Rama' | 'Parashurama'
export const VOICE_AVATARS: TTSAvatar[] = ['Krishna', 'Rama', 'Parashurama']

export type TTSState = 'idle' | 'loading' | 'playing' | 'error'

/**
 * Read one message aloud. The text is split into speakable segments (code,
 * tables and links become short notes) that play gaplessly while the next
 * ones are synthesized, so long replies start at once and keep speaking.
 */
export function useTTS() {
  const [ttsState, setTtsState] = useState<{ state: TTSState; playingId: string | null }>({
    state: 'idle',
    playingId: null,
  })
  const queueRef = useRef<SpeechQueue | null>(null)
  const activeKeyRef = useRef<string | null>(null)  // source of truth; avoids stale closure

  const stop = useCallback(() => {
    queueRef.current?.stop()
    queueRef.current = null
    activeKeyRef.current = null
    setTtsState({ state: 'idle', playingId: null })
  }, [])

  useEffect(() => () => queueRef.current?.stop(), [])

  const speak = useCallback((
    text: string,
    avatar: TTSAvatar,
    messageId: string,
    lang: 'en' | 'hi' = 'en',
  ) => {
    const key = `${messageId}:${lang}`

    if (activeKeyRef.current === key) {
      stop()
      return
    }

    stop()
    unlockAudio()  // still inside the tap
    const segments = splitForSpeech(text, { lang })
    if (segments.length === 0) {
      toast.error('Nothing to read aloud in this reply.')
      return
    }
    activeKeyRef.current = key
    setTtsState({ state: 'loading', playingId: key })

    const queue = new SpeechQueue({
      onStart: () => {
        if (activeKeyRef.current === key) setTtsState({ state: 'playing', playingId: key })
      },
      onIdle: played => {
        if (activeKeyRef.current !== key) return
        activeKeyRef.current = null
        queueRef.current = null
        setTtsState({ state: played === 0 && queue.failed ? 'error' : 'idle', playingId: null })
      },
      onError: message => {
        console.error('[TTS]', message)
        toast.error(`Voice failed: ${message.slice(0, 120)}`)
      },
    })
    queueRef.current = queue
    for (const segment of segments) queue.enqueue(segment, { avatar, lang })
    queue.close()
  }, [stop])

  return { speak, stop, state: ttsState.state, playingId: ttsState.playingId }
}
