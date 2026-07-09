import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Mic, MicOff, Languages } from 'lucide-react'
import { toast } from 'sonner'
import { apiPath } from '@/lib/api'
import { prepareText } from '../hooks/useTTS'
import type { Message } from '../hooks/useAvatara'

/*
 * VoiceMode — hands-free voice-first interface.
 *
 * Loop: listen (VAD) → transcribe (local whisper on the server, or browser
 * speech recognition as fallback) → send to Narad → speak the reply (tiered
 * local TTS) → listen again. Speaking while Narad talks interrupts playback.
 */

type VoiceState = 'starting' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'paused' | 'error'

const VOICE_AVATAR_ORDER = ['Krishna', 'Rama', 'Parashurama'] as const

const STATE_LABEL: Record<VoiceState, string> = {
  starting:     'warming up…',
  listening:    'listening',
  transcribing: 'transcribing…',
  thinking:     'thinking…',
  speaking:     'speaking',
  paused:       'mic paused — tap the orb',
  error:        'voice unavailable',
}

// RMS thresholds (0..1). Barge-in needs a louder, sustained signal so the
// speaker output doesn't interrupt itself (echoCancellation helps too).
const SPEECH_RMS = 0.028
const BARGE_RMS = 0.075
const SILENCE_MS = 1400
const MIN_SPEECH_MS = 350

interface SpeechRecognitionLike {
  continuous: boolean
  interimResults: boolean
  lang: string
  start: () => void
  stop: () => void
  onresult: ((e: unknown) => void) | null
  onerror: ((e: unknown) => void) | null
  onend: (() => void) | null
}

function getBrowserRecognition(): SpeechRecognitionLike | null {
  const w = window as unknown as Record<string, unknown>
  const Ctor = (w.SpeechRecognition ?? w.webkitSpeechRecognition) as (new () => SpeechRecognitionLike) | undefined
  return Ctor ? new Ctor() : null
}

interface Props {
  open: boolean
  onClose: () => void
  messages: Message[]
  streaming: boolean
  onSend: (query: string) => void
}

export function VoiceMode({ open, onClose, messages, streaming, onSend }: Props) {
  const [state, setState] = useState<VoiceState>('starting')
  const [transcript, setTranscript] = useState('')
  const [reply, setReply] = useState('')
  const [speakerName, setSpeakerName] = useState('Narad')
  const [lang, setLang] = useState<'en' | 'hi'>('en')
  const [level, setLevel] = useState(0)

  const stateRef = useRef<VoiceState>('starting')
  const sttModeRef = useRef<'server' | 'browser' | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const rafRef = useRef<number>(0)
  const audioRef = useRef<HTMLAudioElement | null>(null)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const spokenIdsRef = useRef<Set<string>>(new Set())
  const speechStartRef = useRef<number>(0)   // ts when voice onset detected (0 = none)
  const silenceStartRef = useRef<number>(0)
  const bargeStartRef = useRef<number>(0)
  const langRef = useRef<'en' | 'hi'>('en')
  langRef.current = lang

  const setVoiceState = useCallback((s: VoiceState) => {
    stateRef.current = s
    setState(s)
  }, [])

  const stopPlayback = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.src = ''
      audioRef.current = null
    }
  }, [])

  // ------------------------------------------------------------- send + speak

  const submitQuery = useCallback((text: string) => {
    const q = text.trim()
    if (!q) { setVoiceState('listening'); return }
    setTranscript(q)
    setVoiceState('thinking')
    onSend(q)
  }, [onSend, setVoiceState])

  const speakReply = useCallback(async (msg: Message) => {
    const text = prepareText(msg.text)
    if (!text) { setVoiceState('listening'); return }
    const avatar = VOICE_AVATAR_ORDER.find(a => msg.avatarsInvolved?.includes(a)) ?? 'Narad'
    setSpeakerName(avatar)
    setReply(msg.text.slice(0, 280))
    setVoiceState('speaking')
    try {
      const res = await fetch(apiPath('/voice/tts'), {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, avatar: avatar.toLowerCase(), lang: langRef.current }),
      })
      if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `TTS ${res.status}`)
      const data = await res.json()
      const audio = new Audio(`data:audio/wav;base64,${data.audio_b64}`)
      audioRef.current = audio
      audio.onended = () => {
        audioRef.current = null
        if (stateRef.current === 'speaking') startListening()
      }
      await audio.play()
    } catch (e) {
      toast.error(e instanceof Error ? e.message : 'Voice output failed')
      startListening()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setVoiceState])

  // ------------------------------------------------------------ listening loop

  const startListening = useCallback(() => {
    setVoiceState('listening')
    speechStartRef.current = 0
    silenceStartRef.current = 0
    if (sttModeRef.current === 'browser') {
      try { recognitionRef.current?.start() } catch { /* already started */ }
    } else if (sttModeRef.current === 'server' && streamRef.current) {
      chunksRef.current = []
      const rec = new MediaRecorder(streamRef.current)
      rec.ondataavailable = e => { if (e.data.size > 0) chunksRef.current.push(e.data) }
      rec.onstop = () => {
        const hadSpeech = speechStartRef.current > 0
        const blob = new Blob(chunksRef.current, { type: rec.mimeType || 'audio/webm' })
        chunksRef.current = []
        if (!hadSpeech || stateRef.current !== 'transcribing') return
        const form = new FormData()
        form.append('audio', blob, 'utterance.webm')
        fetch(apiPath('/voice/stt'), { method: 'POST', body: form })
          .then(async res => {
            if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `STT ${res.status}`)
            return res.json()
          })
          .then(data => submitQuery(String(data.text ?? '')))
          .catch(e => {
            toast.error(e instanceof Error ? e.message : 'Transcription failed')
            startListening()
          })
      }
      recorderRef.current = rec
      rec.start()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setVoiceState, submitQuery])

  const endUtterance = useCallback(() => {
    setVoiceState('transcribing')
    recorderRef.current?.stop()
    recorderRef.current = null
  }, [setVoiceState])

  // RMS meter + VAD + barge-in, driven by requestAnimationFrame.
  const monitor = useCallback(() => {
    const analyser = analyserRef.current
    if (!analyser) return
    const buf = new Float32Array(analyser.fftSize)
    const tick = () => {
      analyser.getFloatTimeDomainData(buf)
      let sum = 0
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i]
      const rms = Math.sqrt(sum / buf.length)
      setLevel(rms)
      const now = performance.now()
      const s = stateRef.current
      if (s === 'listening' && sttModeRef.current === 'server') {
        if (rms > SPEECH_RMS) {
          if (!speechStartRef.current) speechStartRef.current = now
          silenceStartRef.current = 0
        } else if (speechStartRef.current) {
          if (!silenceStartRef.current) silenceStartRef.current = now
          const spoke = now - speechStartRef.current > MIN_SPEECH_MS
          if (spoke && now - silenceStartRef.current > SILENCE_MS) endUtterance()
        }
      } else if (s === 'speaking') {
        if (rms > BARGE_RMS) {
          if (!bargeStartRef.current) bargeStartRef.current = now
          if (now - bargeStartRef.current > 300) {  // sustained → interrupt
            bargeStartRef.current = 0
            stopPlayback()
            startListening()
          }
        } else {
          bargeStartRef.current = 0
        }
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [endUtterance, startListening, stopPlayback])

  // --------------------------------------------------------------- lifecycle

  useEffect(() => {
    if (!open) return
    let cancelled = false
    spokenIdsRef.current = new Set(messages.map(m => m.id))  // never speak history

    const boot = async () => {
      let serverStt = false
      try {
        const res = await fetch(apiPath('/voice/status'))
        serverStt = res.ok && (await res.json())?.stt?.available === true
      } catch { /* server unreachable → try browser */ }

      if (serverStt) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true },
          })
          if (cancelled) { stream.getTracks().forEach(t => t.stop()); return }
          streamRef.current = stream
          const ctx = new AudioContext()
          const analyser = ctx.createAnalyser()
          analyser.fftSize = 1024
          ctx.createMediaStreamSource(stream).connect(analyser)
          audioCtxRef.current = ctx
          analyserRef.current = analyser
          sttModeRef.current = 'server'
          monitor()
          startListening()
          return
        } catch {
          toast.error('Microphone access denied')
        }
      }
      // Browser speech recognition fallback (free, no install).
      const rec = getBrowserRecognition()
      if (rec) {
        rec.continuous = false
        rec.interimResults = true
        rec.lang = 'en-IN'
        rec.onresult = (e: unknown) => {
          const ev = e as { results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }
          const last = ev.results[ev.results.length - 1]
          setTranscript(last[0].transcript)
          if (last.isFinal) submitQuery(last[0].transcript)
        }
        rec.onerror = () => { if (stateRef.current === 'listening') setVoiceState('paused') }
        rec.onend = () => { if (stateRef.current === 'listening') { try { rec.start() } catch { /* noop */ } } }
        recognitionRef.current = rec
        sttModeRef.current = 'browser'
        startListening()
      } else {
        sttModeRef.current = null
        setVoiceState('error')
        toast.error('No voice input available — install narad-harness[voice] on the server')
      }
    }
    void boot()

    return () => {
      cancelled = true
      cancelAnimationFrame(rafRef.current)
      stopPlayback()
      try { recognitionRef.current?.stop() } catch { /* noop */ }
      recognitionRef.current = null
      recorderRef.current?.stop()
      recorderRef.current = null
      streamRef.current?.getTracks().forEach(t => t.stop())
      streamRef.current = null
      void audioCtxRef.current?.close()
      audioCtxRef.current = null
      analyserRef.current = null
      setVoiceState('starting')
      setTranscript('')
      setReply('')
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // Speak each new assistant reply once streaming settles.
  useEffect(() => {
    if (!open || streaming || stateRef.current !== 'thinking') return
    const last = messages[messages.length - 1]
    if (last && last.role === 'assistant' && !spokenIdsRef.current.has(last.id)) {
      spokenIdsRef.current.add(last.id)
      void speakReply(last)
    }
  }, [open, streaming, messages, speakReply])

  const toggleMic = () => {
    if (state === 'paused') startListening()
    else if (state === 'listening') {
      recorderRef.current?.stop()
      recorderRef.current = null
      try { recognitionRef.current?.stop() } catch { /* noop */ }
      setVoiceState('paused')
    } else if (state === 'speaking') {
      stopPlayback()
      startListening()
    }
  }

  if (!open) return null

  const orbScale = 1 + Math.min(level * 6, 0.35)
  const active = state === 'listening' || state === 'speaking'

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center justify-center"
      style={{ background: 'radial-gradient(ellipse at 50% 42%, #3a352e 0%, var(--kajal, #2d2a26) 70%)' }}
    >
      <button
        onClick={onClose}
        aria-label="Exit voice mode"
        className="absolute top-5 right-5 p-2 rounded-full transition-opacity opacity-60 hover:opacity-100"
        style={{ color: 'var(--paper, #fcfaf2)', background: 'rgba(252,250,242,0.08)' }}
      >
        <X size={18} />
      </button>

      <button
        onClick={() => setLang(l => (l === 'en' ? 'hi' : 'en'))}
        className="absolute top-5 left-5 flex items-center gap-1.5 px-3 py-1.5 rounded-full text-[11px] font-mono transition-opacity opacity-60 hover:opacity-100"
        style={{ color: 'var(--paper, #fcfaf2)', background: 'rgba(252,250,242,0.08)', border: '1px solid rgba(252,250,242,0.15)' }}
      >
        <Languages size={12} />
        {lang === 'en' ? 'English' : 'हिन्दी'}
      </button>

      {/* Orb */}
      <button
        onClick={toggleMic}
        aria-label="Toggle microphone"
        className="relative rounded-full outline-none"
        style={{ width: 170, height: 170, background: 'transparent', border: 'none', cursor: 'pointer' }}
      >
        <span
          className="absolute inset-0 rounded-full"
          style={{
            transform: `scale(${state === 'listening' ? orbScale : 1})`,
            transition: 'transform 90ms linear',
            background: state === 'speaking'
              ? 'radial-gradient(circle, rgba(200,90,58,0.9) 0%, rgba(200,90,58,0.25) 70%)'
              : state === 'thinking' || state === 'transcribing'
                ? 'radial-gradient(circle, rgba(252,250,242,0.35) 0%, rgba(252,250,242,0.08) 70%)'
                : 'radial-gradient(circle, rgba(252,250,242,0.85) 0%, rgba(252,250,242,0.18) 70%)',
            opacity: state === 'paused' ? 0.35 : 1,
            animation: state === 'thinking' || state === 'transcribing' ? 'voicePulse 1.6s ease-in-out infinite' : 'none',
          }}
        />
        <span className="absolute inset-0 flex items-center justify-center" style={{ color: 'var(--kajal, #2d2a26)' }}>
          {state === 'paused' ? <MicOff size={38} /> : <Mic size={38} />}
        </span>
      </button>

      <div className="mt-8 font-mono text-[12px] uppercase tracking-widest" style={{ color: 'rgba(252,250,242,0.55)' }}>
        {state === 'speaking' ? `${speakerName} — speaking` : STATE_LABEL[state]}
        {active && <span className="inline-block w-1.5 h-1.5 rounded-full ml-2 align-middle" style={{ background: '#c85a3a', animation: 'voicePulse 1.2s infinite' }} />}
      </div>

      {transcript && (
        <p className="mt-5 max-w-md px-8 text-center text-[15px] leading-relaxed" style={{ color: 'var(--paper, #fcfaf2)', fontFamily: 'var(--font-body)' }}>
          “{transcript}”
        </p>
      )}
      {reply && state === 'speaking' && (
        <p className="mt-3 max-w-md px-8 text-center text-[13px] leading-relaxed" style={{ color: 'rgba(252,250,242,0.6)', fontFamily: 'var(--font-body)' }}>
          {reply}{reply.length >= 280 ? '…' : ''}
        </p>
      )}

      <style>{`@keyframes voicePulse { 0%,100% { opacity: 1 } 50% { opacity: 0.45 } }`}</style>
    </div>
  )
}
