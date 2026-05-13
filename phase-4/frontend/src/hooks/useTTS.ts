import { useState, useCallback, useRef } from 'react'
import { toast } from 'sonner'

export type TTSAvatar = 'Krishna' | 'Buddha' | 'Rama'
export const VOICE_AVATARS: TTSAvatar[] = ['Krishna', 'Buddha', 'Rama']

export type TTSState = 'idle' | 'loading' | 'playing' | 'error'

function prepareText(raw: string): string {
  return raw
    .replace(/```[\s\S]*?```/g, '')
    .replace(/`[^`\n]+`/g, '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/\*\*\*([^*\n]+)\*\*\*/g, '$1')
    .replace(/\*\*([^*\n]+)\*\*/g, '$1')
    .replace(/\*([^*\n]+)\*/g, '$1')
    .replace(/___([^_\n]+)___/g, '$1')
    .replace(/__([^_\n]+)__/g, '$1')
    .replace(/_([^_\n]+)_/g, '$1')
    .replace(/~~([^~\n]+)~~/g, '$1')
    .replace(/!\[[^\]]*\]\([^)]+\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
    .replace(/^>\s*/gm, '')
    .replace(/^[-*+]\s+/gm, '')
    .replace(/^\d+[.)]\s+/gm, '')
    .replace(/^[-_*]{3,}\s*$/gm, '')
    .replace(/\|[^\n]*/g, '')
    .replace(/[*_`~#>\\|]/g, '')
    .replace(/[\u{1F000}-\u{1FFFF}\u{2600}-\u{27BF}\u{2B00}-\u{2BFF}\u{FE00}-\u{FE0F}\u{1F900}-\u{1FAFF}]/gu, '')
    .replace(/\n{2,}/g, '. ')
    .replace(/\n/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
    .slice(0, 500)
}

export function useTTS() {
  const [ttsState, setTtsState] = useState<{ state: TTSState; playingId: string | null }>({
    state: 'idle',
    playingId: null,
  })
  const audioRef    = useRef<HTMLAudioElement | null>(null)
  const activeKeyRef = useRef<string | null>(null)  // source of truth; avoids stale closure

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.src = ''
      audioRef.current = null
    }
    activeKeyRef.current = null
    setTtsState({ state: 'idle', playingId: null })
  }, [])

  const speak = useCallback(async (
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
    activeKeyRef.current = key
    setTtsState({ state: 'loading', playingId: key })

    const speakText = prepareText(text)
    if (!speakText) {
      toast.error('Nothing speakable after stripping markdown.')
      stop()
      return
    }

    try {
      const res = await fetch('/tts', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: speakText, avatar, lang }),
      })

      // Bail if user stopped while fetch was in flight
      if (activeKeyRef.current !== key) return

      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(err.detail ?? res.statusText)
      }

      const data = await res.json()
      const audio_b64: string = data.audio_b64
      if (!audio_b64) throw new Error('No audio in server response')

      if (activeKeyRef.current !== key) return

      const byteStr = atob(audio_b64)
      const buf  = new ArrayBuffer(byteStr.length)
      const view = new Uint8Array(buf)
      for (let i = 0; i < byteStr.length; i++) view[i] = byteStr.charCodeAt(i)

      const blob = new Blob([buf], { type: 'audio/wav' })
      const url  = URL.createObjectURL(blob)

      const audio = new Audio(url)
      audioRef.current = audio

      audio.onplay  = () => setTtsState({ state: 'playing', playingId: key })
      audio.onended = () => {
        activeKeyRef.current = null
        setTtsState({ state: 'idle', playingId: null })
        URL.revokeObjectURL(url)
      }
      audio.onerror = () => {
        toast.error('Audio playback failed — try again.')
        activeKeyRef.current = null
        setTtsState({ state: 'error', playingId: null })
        URL.revokeObjectURL(url)
      }

      await audio.play()
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error('[TTS]', msg)
      toast.error(`Voice failed: ${msg.slice(0, 120)}`)
      activeKeyRef.current = null
      setTtsState({ state: 'error', playingId: null })
    }
  }, [stop])  // no playingId dep — ref handles it

  return { speak, stop, state: ttsState.state, playingId: ttsState.playingId }
}
