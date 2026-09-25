import { useCallback, useEffect, useRef, useState } from 'react'
import { X, Mic, MicOff, Settings2, Square } from 'lucide-react'
import { toast } from 'sonner'
import { apiFetch } from '@/lib/api'
import {
  SpeechSplitter, hasDevanagari, remainingSegments, splitForSpeech,
  type SpeechLang, type SplitterOptions,
} from '@/lib/speech-segments'
import { SpeechQueue, unlockAudio, type SpeechTiming } from '@/lib/speech-queue'
import type { LiveAnswer, Message } from '../hooks/useAvatara'
import { Bindu, VoiceMatrix, type BinduMood } from './pulli'

/*
 * VoiceMode — hands-free voice-first interface.
 *
 * Loop: listen (VAD) → transcribe (on the Mac, or browser speech recognition
 * as fallback) → send to Narad → speak the reply as it streams → listen again.
 * The streaming answer is split into sentences as they arrive; each is
 * synthesized in order (two ahead at most) and played gaplessly, so the first
 * sentence plays while the rest is still being written. Speaking while Narad
 * talks, tapping the orb, or sending a new message stops playback at once.
 */

type VoiceState = 'starting' | 'listening' | 'transcribing' | 'thinking' | 'speaking' | 'paused' | 'error'

type ReplyLanguage = 'en' | 'hi' | 'auto'
type HindiScript = 'devanagari' | 'roman'

interface VoicePrefs {
  reply_language: ReplyLanguage
  script: HindiScript
  keep_voice_on_mac: boolean
}

interface VoiceEngines {
  stt: string | null
  tts: string | null
}

/** Where the last reply's time to first audio went, for the settings sheet. */
interface FirstAudio {
  totalMs: number
  sttMs?: number
  firstTextMs?: number
  ttsMs?: number
  engine?: string
  cache?: string
}

interface Turn {
  knownIds: Set<string>      // messages that existed before this turn
  queue: SpeechQueue
  splitter: SpeechSplitter
  source: string             // the stream being spoken ('narad' or an avatar)
  fed: string                // that stream's text so far
  finalText: string | null   // the reply as persisted, once it arrived
  avatar: string
  heard: SpeechLang | null   // language the person spoke, from speech-to-text
  prefs: VoicePrefs
  sawStreaming: boolean
  closed: boolean
  silenced: boolean
  spokeEnd: number           // performance.now() when the person stopped speaking
  sttMs?: number
  sent: number
  firstText?: number
}

const DEFAULT_PREFS: VoicePrefs = { reply_language: 'en', script: 'devanagari', keep_voice_on_mac: false }

const VOICE_AVATAR_ORDER = ['Krishna', 'Rama', 'Parashurama'] as const

const STATE_LABEL: Record<VoiceState, string> = {
  starting:     'warming up…',
  listening:    'listening',
  transcribing: 'transcribing…',
  thinking:     'thinking…',
  speaking:     'speaking',
  paused:       'mic paused',
  error:        'voice unavailable',
}

const LANGUAGE_LABEL: Record<ReplyLanguage, string> = { en: 'English', hi: 'हिन्दी', auto: 'Auto' }

// RMS thresholds (0..1). Barge-in needs a louder, sustained signal so the
// speaker output doesn't interrupt itself (echoCancellation helps too).
const SPEECH_RMS = 0.028
const BARGE_RMS = 0.075
const SILENCE_MS = 1400
const MIN_SPEECH_MS = 350

/** The chat's reply_language: a language with an optional script subtag. */
function replyTag(prefs: VoicePrefs): string | undefined {
  const roman = prefs.script === 'roman'
  if (prefs.reply_language === 'hi') return roman ? 'hi-Latn' : 'hi'
  if (prefs.reply_language === 'auto' && roman) return 'auto-Latn'
  return undefined
}

/** Voice language for one segment: Devanagari is read in Hindi whatever the setting. */
function segmentLang(text: string, prefs: VoicePrefs, heard: SpeechLang | null): SpeechLang {
  if (hasDevanagari(text) || prefs.reply_language === 'hi') return 'hi'
  if (prefs.reply_language === 'auto' && heard === 'hi') return 'hi'
  return 'en'
}

function splitterOptions(prefs: VoicePrefs): SplitterOptions {
  return { lang: prefs.reply_language === 'en' ? 'en' : prefs.reply_language === 'hi' ? 'hi' : 'auto' }
}

function seconds(ms: number | undefined): string {
  return ms === undefined ? '–' : `${(ms / 1000).toFixed(1)} s`
}

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

function Choice<T extends string>({ value, options, onChange }: {
  value: T
  options: Array<[T, string]>
  onChange: (value: T) => void
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map(([key, label]) => (
        <button
          key={key}
          onClick={() => onChange(key)}
          aria-pressed={value === key}
          lang={/[\u0900-\u097F]/.test(label) ? 'hi' : undefined}
          className="px-4 rounded-full text-[14px] transition-colors"
          style={{
            minHeight: 44,
            lineHeight: 1.2,
            border: '1px solid rgba(252,250,242,0.26)',
            background: value === key ? 'rgba(252,250,242,0.92)' : 'transparent',
            color: value === key ? '#111111' : '#fcfaf2',
          }}
        >
          {label}
        </button>
      ))}
    </div>
  )
}

interface Props {
  open: boolean
  onClose: () => void
  messages: Message[]
  streaming: boolean
  /** The answer as it streams (text_delta), before the final reply arrives. */
  liveAnswer?: LiveAnswer | null
  onSend: (query: string, replyLanguage?: string) => void
  /** Stops a reply still being written, so a new question can go out. */
  onStopTurn?: () => void
}

export function VoiceMode({ open, onClose, messages, streaming, liveAnswer = null, onSend, onStopTurn }: Props) {
  const [state, setState] = useState<VoiceState>('starting')
  const [transcript, setTranscript] = useState('')
  const [caption, setCaption] = useState('')
  const [speakerName, setSpeakerName] = useState('Narad')
  const [prefs, setPrefs] = useState<VoicePrefs>(DEFAULT_PREFS)
  const [engines, setEngines] = useState<VoiceEngines>({ stt: null, tts: null })
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [firstAudio, setFirstAudio] = useState<FirstAudio | null>(null)
  const [level, setLevel] = useState(0)

  const stateRef = useRef<VoiceState>('starting')
  const sttModeRef = useRef<'server' | 'browser' | null>(null)
  const streamRef = useRef<MediaStream | null>(null)
  const recorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])
  const audioCtxRef = useRef<AudioContext | null>(null)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const rafRef = useRef<number>(0)
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null)
  const turnRef = useRef<Turn | null>(null)
  const pendingQueryRef = useRef<{ text: string; heard: SpeechLang | null; sttMs?: number } | null>(null)
  const spokeEndRef = useRef<number>(0)
  const speechStartRef = useRef<number>(0)   // ts when voice onset detected (0 = none)
  const silenceStartRef = useRef<number>(0)
  const bargeStartRef = useRef<number>(0)
  const prefsRef = useRef<VoicePrefs>(DEFAULT_PREFS)
  prefsRef.current = prefs
  const messagesRef = useRef<Message[]>(messages)
  messagesRef.current = messages
  const streamingRef = useRef(streaming)
  streamingRef.current = streaming
  const onSendRef = useRef(onSend)
  onSendRef.current = onSend
  const onStopTurnRef = useRef(onStopTurn)
  onStopTurnRef.current = onStopTurn

  const setVoiceState = useCallback((s: VoiceState) => {
    stateRef.current = s
    setState(s)
  }, [])

  // ------------------------------------------------------------ preferences

  const refreshEngines = useCallback(async (): Promise<boolean> => {
    try {
      const res = await apiFetch('/voice/status')
      if (!res.ok) return false
      const status = await res.json()
      setEngines({ stt: status?.stt?.engine ?? null, tts: status?.tts?.active ?? null })
      return status?.stt?.available === true
    } catch {
      return false
    }
  }, [])

  const savePrefs = useCallback((update: Partial<VoicePrefs>) => {
    const next = { ...prefsRef.current, ...update }
    prefsRef.current = next
    setPrefs(next)
    void apiFetch('/voice/preferences', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(update),
    })
      .then(res => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        if ('keep_voice_on_mac' in update) void refreshEngines()
      })
      .catch(() => toast.error('Could not save your voice settings'))
  }, [refreshEngines])

  // ------------------------------------------------------------ speaking

  /** Barge-in, stop, or a new question: silence this turn and abort its requests. */
  const cancelSpeech = useCallback(() => {
    const turn = turnRef.current
    if (turn) {
      turn.silenced = true
      turn.queue.stop()
    }
    setCaption('')
  }, [])

  const enqueue = useCallback((turn: Turn, segments: string[], source: string) => {
    for (const segment of segments) {
      turn.queue.enqueue(segment, {
        avatar: turn.avatar,
        lang: segmentLang(segment, turn.prefs, turn.heard),
        source,
      })
    }
  }, [])

  const closeTurn = useCallback((turn: Turn) => {
    if (turn.closed) return
    turn.closed = true
    turn.queue.close()
  }, [])

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
        const hint = prefsRef.current.reply_language
        form.append('lang', hint === 'auto' ? '' : hint)
        const sttStarted = performance.now()
        apiFetch('/voice/stt', { method: 'POST', body: form })
          .then(async res => {
            if (!res.ok) throw new Error((await res.json().catch(() => null))?.detail ?? `STT ${res.status}`)
            return res.json()
          })
          .then(data => {
            const text = String(data.text ?? '')
            const language = String(data.language ?? '')
            const heard: SpeechLang = language.startsWith('hi') || hasDevanagari(text) ? 'hi' : 'en'
            submitQuery(text, heard, performance.now() - sttStarted)
          })
          .catch(e => {
            toast.error(e instanceof Error ? e.message : 'Transcription failed')
            startListening()
          })
      }
      recorderRef.current = rec
      rec.start()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [setVoiceState])

  const startTurn = useCallback((text: string, heard: SpeechLang | null, sttMs?: number) => {
    const turnPrefs = prefsRef.current
    const turn: Turn = {
      knownIds: new Set(messagesRef.current.map(m => m.id)),
      queue: new SpeechQueue({
        onStart: (segment: string, timing: SpeechTiming, first: boolean) => {
          if (turnRef.current !== turn || turn.silenced) return
          setCaption(segment)
          if (stateRef.current !== 'speaking') setVoiceState('speaking')
          if (!first) return
          const measured: FirstAudio = {
            totalMs: (timing.startedAt ?? performance.now()) - turn.spokeEnd,
            sttMs: turn.sttMs,
            firstTextMs: turn.firstText !== undefined ? turn.firstText - turn.sent : undefined,
            ttsMs: timing.fetchedAt !== undefined ? timing.fetchedAt - timing.queuedAt : undefined,
            engine: timing.engine,
            cache: timing.cache,
          }
          setFirstAudio(measured)
          console.info('[voice] first audio', measured)
        },
        onIdle: () => {
          if (turnRef.current !== turn || turn.silenced) return
          setCaption('')
          const s = stateRef.current
          if (s === 'speaking' || s === 'thinking') startListening()
        },
        onError: message => toast.error(`Voice failed: ${message.slice(0, 120)}`),
      }),
      splitter: new SpeechSplitter(splitterOptions(turnPrefs)),
      source: '',
      fed: '',
      finalText: null,
      avatar: 'narad',
      heard,
      prefs: turnPrefs,
      sawStreaming: false,
      closed: false,
      silenced: false,
      spokeEnd: spokeEndRef.current || performance.now(),
      sttMs,
      sent: performance.now(),
    }
    turnRef.current = turn
    setTranscript(text)
    setSpeakerName('Narad')
    setVoiceState('thinking')
    onSendRef.current(text, replyTag(turnPrefs))
    // A send that never streams (a refused turn) must not strand the loop.
    window.setTimeout(() => {
      if (turnRef.current === turn && !turn.sawStreaming && !turn.closed && !streamingRef.current) closeTurn(turn)
    }, 6000)
  }, [closeTurn, setVoiceState, startListening])

  const submitQuery = useCallback((text: string, heard: SpeechLang | null = null, sttMs?: number) => {
    const q = text.trim()
    if (!q) { startListening(); return }
    cancelSpeech()
    if (streamingRef.current) {
      // The last reply is still being written: stop it, then ask.
      pendingQueryRef.current = { text: q, heard, sttMs }
      setTranscript(q)
      setVoiceState('thinking')
      onStopTurnRef.current?.()
      return
    }
    startTurn(q, heard, sttMs)
  }, [cancelSpeech, setVoiceState, startListening, startTurn])

  const endUtterance = useCallback(() => {
    spokeEndRef.current = performance.now()
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
            cancelSpeech()
            startListening()
          }
        } else {
          bargeStartRef.current = 0
        }
      }
      rafRef.current = requestAnimationFrame(tick)
    }
    rafRef.current = requestAnimationFrame(tick)
  }, [cancelSpeech, endUtterance, startListening])

  // --------------------------------------------------------------- lifecycle

  useEffect(() => {
    if (!open) return
    let cancelled = false
    unlockAudio()  // voice mode opens from a tap; the overlay unlocks again on any touch

    const boot = async () => {
      let loaded = DEFAULT_PREFS
      try {
        const res = await apiFetch('/voice/preferences')
        if (res.ok) loaded = { ...DEFAULT_PREFS, ...((await res.json())?.preferences ?? {}) }
      } catch { /* defaults */ }
      if (cancelled) return
      prefsRef.current = loaded
      setPrefs(loaded)
      const serverStt = await refreshEngines()
      if (cancelled) return

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
      if (loaded.keep_voice_on_mac) {
        // Browser recognition sends audio to the browser vendor: not with this setting.
        sttModeRef.current = null
        setVoiceState('error')
        toast.error('No speech-to-text on the Mac yet, and your voice stays on the Mac. Install mlx-whisper there, or turn the setting off.')
        return
      }
      // Browser speech recognition fallback (free, no install).
      const rec = getBrowserRecognition()
      if (rec) {
        rec.continuous = false
        rec.interimResults = true
        rec.lang = loaded.reply_language === 'hi' ? 'hi-IN' : 'en-IN'
        rec.onresult = (e: unknown) => {
          const ev = e as { results: ArrayLike<{ isFinal: boolean; 0: { transcript: string } }> }
          const last = ev.results[ev.results.length - 1]
          setTranscript(last[0].transcript)
          if (last.isFinal) {
            spokeEndRef.current = performance.now()
            const heard: SpeechLang | null = hasDevanagari(last[0].transcript) ? 'hi' : null
            submitQuery(last[0].transcript, heard)
          }
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
      cancelSpeech()
      turnRef.current = null
      pendingQueryRef.current = null
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
      setCaption('')
      setSettingsOpen(false)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open])

  // A question asked while the last reply was still streaming goes out once it stopped.
  useEffect(() => {
    if (!open || streaming || !pendingQueryRef.current) return
    const pending = pendingQueryRef.current
    pendingQueryRef.current = null
    startTurn(pending.text, pending.heard, pending.sttMs)
  }, [open, streaming, startTurn])

  // Speak the answer while it streams, then whatever the final reply adds.
  useEffect(() => {
    const turn = turnRef.current
    if (!open || !turn || turn.closed || turn.silenced) return
    if (streaming) turn.sawStreaming = true
    const reply = messages.find(m => m.role === 'assistant' && !turn.knownIds.has(m.id))

    if (reply?.text) {
      const text = reply.text.trim()
      if (turn.finalText === null) {
        const fed = turn.fed.trim()
        if (turn.source && fed && text.startsWith(fed)) {
          // The usual case: the reply is the streamed text; say the rest of it.
          enqueue(turn, [...turn.splitter.push(text.slice(fed.length)), ...turn.splitter.finish()], turn.source)
        } else {
          // It differs from what streamed: drop the unspoken, never repeat the spoken.
          turn.queue.dropUnspoken()
          if (!turn.source) {
            const avatar = VOICE_AVATAR_ORDER.find(a => reply.avatarsInvolved?.includes(a))
            turn.avatar = (avatar ?? 'Narad').toLowerCase()
            setSpeakerName(avatar ?? 'Narad')
          }
          enqueue(turn, remainingSegments(text, turn.queue.spoken(), splitterOptions(turn.prefs)), 'final')
        }
        turn.finalText = text
      } else if (text.length > turn.finalText.length && text.startsWith(turn.finalText)) {
        enqueue(turn, splitForSpeech(text.slice(turn.finalText.length), splitterOptions(turn.prefs)), 'final')
        turn.finalText = text
      }
    } else if (liveAnswer?.text) {
      if (liveAnswer.source !== turn.source || !liveAnswer.text.startsWith(turn.fed)) {
        // A text_reset, or the answer moved to another stream.
        if (turn.source) turn.queue.dropUnspoken(turn.source)
        turn.source = liveAnswer.source
        turn.fed = ''
        turn.splitter = new SpeechSplitter(splitterOptions(turn.prefs))
        turn.avatar = liveAnswer.source.toLowerCase()
        setSpeakerName(liveAnswer.source === 'narad' ? 'Narad' : liveAnswer.source)
      }
      if (turn.firstText === undefined) turn.firstText = performance.now()
      const delta = liveAnswer.text.slice(turn.fed.length)
      turn.fed = liveAnswer.text
      enqueue(turn, turn.splitter.push(delta), turn.source)
    } else if (turn.source && streaming) {
      // text_reset: what streamed was chatter before a tool call.
      turn.queue.dropUnspoken(turn.source)
      turn.source = ''
      turn.fed = ''
      turn.splitter = new SpeechSplitter(splitterOptions(turn.prefs))
    }

    if (!streaming && (turn.sawStreaming || reply)) closeTurn(turn)
  }, [open, streaming, liveAnswer, messages, enqueue, closeTurn])

  const toggleMic = () => {
    if (state === 'paused') startListening()
    else if (state === 'listening') {
      recorderRef.current?.stop()
      recorderRef.current = null
      try { recognitionRef.current?.stop() } catch { /* noop */ }
      setVoiceState('paused')
    } else if (state === 'speaking' || state === 'thinking') {
      // Stop talking (the reply still arrives on screen) and listen.
      cancelSpeech()
      startListening()
    }
  }

  if (!open) return null

  const orbScale = 1 + Math.min(level * 6, 0.35)
  const active = state === 'listening' || state === 'speaking'
  const stoppable = state === 'speaking' || state === 'thinking'
  const localSttMissing = prefs.keep_voice_on_mac && !engines.stt

  const mood: BinduMood = state === 'listening' ? 'listening'
    : state === 'thinking' || state === 'transcribing' || state === 'starting' ? 'thinking'
    : state === 'speaking' ? 'hello'
    : state === 'error' ? 'oops'
    : 'hush'
  const statusWord = state === 'speaking' ? `${speakerName} speaking` : STATE_LABEL[state].replace('…', '')

  return (
    <div
      className="fixed inset-0 z-50 flex flex-col items-center"
      role="dialog"
      aria-modal="true"
      aria-label="Voice mode"
      style={{
        background: 'var(--stage)',
        color: '#f5f5f5',
        // The stage is black in both themes: the drawing kit takes its night ink here.
        ['--kajal' as string]: '#f5f5f5',
        ['--glyph' as string]: 'rgba(255,255,255,0.18)',
        ['--dot-off' as string]: 'rgba(255,255,255,0.09)',
        ['--sindoor' as string]: '#f0673a',
      }}
      onPointerDown={unlockAudio}
    >
      <div className="pulli-ground" aria-hidden="true" style={{ position: 'absolute', inset: 0, pointerEvents: 'none', WebkitMaskImage: 'none', maskImage: 'none', opacity: 0.8 }} />
      <div className="relative w-full flex items-center justify-between pl-5 pr-3" style={{ minHeight: 60, marginTop: 'env(safe-area-inset-top)' }}>
        <span className="font-dot" style={{ fontSize: 14, color: 'rgba(245,245,245,0.62)' }}>
          {prefs.keep_voice_on_mac ? 'VOICE · HEARD ON THE MAC' : 'VOICE · ' + LANGUAGE_LABEL[prefs.reply_language].toUpperCase()}
        </span>
        <div className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => setSettingsOpen(o => !o)}
            aria-label="Voice settings"
            aria-expanded={settingsOpen}
            className="n-icon-btn opacity-80 hover:opacity-100"
          >
            <Settings2 size={20} />
          </button>
          <button type="button" onClick={onClose} aria-label="Exit voice mode" className="n-icon-btn opacity-80 hover:opacity-100">
            <X size={22} />
          </button>
        </div>
      </div>

      <div className="relative flex-1 w-full flex flex-col items-center justify-center gap-6 px-5 text-center">
        <Bindu mood={mood} size={132} style={{ color: '#f5f5f5' }} />
        <div role="status" className="font-dot" style={{ fontSize: 32, letterSpacing: '0.06em', color: state === 'paused' || state === 'error' ? 'rgba(245,245,245,0.62)' : 'var(--sindoor)' }}>
          {statusWord.toUpperCase()}
        </div>
        <VoiceMatrix
          cols={19}
          rows={9}
          pitch={15}
          level={state === 'listening' ? Math.min(1, 0.25 + level * 8) : state === 'speaking' ? 0.8 : 0.15}
          live={active}
        />
        {transcript && (
          <p className="max-w-md px-3 text-[21px] leading-snug" lang={/[\u0900-\u097F]/.test(transcript) ? 'hi' : undefined} style={{ color: '#f5f5f5' }}>
            {transcript}
          </p>
        )}
        {caption && state === 'speaking' && (
          <p className="max-w-md px-3 text-[15.5px] leading-relaxed" lang={/[\u0900-\u097F]/.test(caption) ? 'hi' : undefined} style={{ color: 'rgba(245,245,245,0.72)' }}>
            {caption}
          </p>
        )}
      </div>

      {/* The one control: talk, or stop Narad talking. */}
      <div className="relative flex flex-col items-center gap-2" style={{ paddingBottom: 'calc(28px + env(safe-area-inset-bottom))' }}>
        <button
          type="button"
          onClick={toggleMic}
          aria-label={stoppable ? 'Stop speaking' : state === 'paused' ? 'Turn the microphone on' : 'Pause the microphone'}
          className="rounded-full grid place-items-center"
          style={{
            width: 84,
            height: 84,
            border: 0,
            cursor: 'pointer',
            background: state === 'paused' ? 'rgba(245,245,245,0.14)' : 'var(--sindoor)',
            color: state === 'paused' ? '#f5f5f5' : '#000000',
            transform: `scale(${state === 'listening' ? orbScale : 1})`,
            transition: 'transform 90ms linear, background 150ms ease',
          }}
        >
          {state === 'paused' ? <MicOff size={32} /> : stoppable ? <Square size={26} fill="currentColor" /> : <Mic size={32} />}
        </button>
        <span className="font-dot" style={{ fontSize: 13, color: 'rgba(245,245,245,0.55)' }}>
          {stoppable ? 'TAP TO INTERRUPT' : state === 'paused' ? 'TAP TO TALK' : 'TAP TO PAUSE'}
        </span>
      </div>

      {settingsOpen && (
        <div
          role="dialog"
          aria-label="Voice settings"
          className="absolute inset-x-0 bottom-0 max-h-[80vh] overflow-y-auto rounded-t-2xl px-5 pt-3 sm:mx-auto sm:max-w-md"
          style={{ background: 'var(--stage-2)', borderRadius: '28px 28px 0 0', color: '#f5f5f5', paddingBottom: 'max(24px, env(safe-area-inset-bottom))' }}
        >
          <div className="flex items-center justify-between">
            <h2 className="text-[16px] font-semibold">Voice settings</h2>
            <button type="button" onClick={() => setSettingsOpen(false)} aria-label="Close voice settings" className="n-icon-btn -mr-2.5 opacity-80 hover:opacity-100">
              <X size={20} />
            </button>
          </div>

          <div className="mt-3 text-[15px] opacity-85">Narad replies in</div>
          <div className="mt-2">
            <Choice
              value={prefs.reply_language}
              options={[['en', 'English'], ['hi', 'हिन्दी'], ['auto', 'Same as I speak']]}
              onChange={value => savePrefs({ reply_language: value })}
            />
          </div>

          <div className="mt-4 text-[15px] opacity-85">Hindi written as</div>
          <div className="mt-2">
            <Choice
              value={prefs.script}
              options={[['devanagari', 'देवनागरी'], ['roman', 'Roman (Hinglish)']]}
              onChange={value => savePrefs({ script: value })}
            />
          </div>

          <button
            role="switch"
            aria-checked={prefs.keep_voice_on_mac}
            onClick={() => savePrefs({ keep_voice_on_mac: !prefs.keep_voice_on_mac })}
            className="mt-5 flex w-full items-start gap-3 text-left"
            style={{ minHeight: 48 }}
          >
            <span
              className="mt-0.5 inline-flex h-5 w-9 shrink-0 items-center rounded-full p-0.5 transition-colors"
              style={{ background: prefs.keep_voice_on_mac ? 'var(--sindoor)' : 'rgba(245,245,245,0.2)' }}
            >
              <span
                className="h-4 w-4 rounded-full transition-transform"
                style={{ background: 'var(--paper, #fcfaf2)', transform: prefs.keep_voice_on_mac ? 'translateX(16px)' : 'none' }}
              />
            </span>
            <span>
              <span className="block text-[15px]">Keep my voice on this Mac</span>
              <span className="block text-[13.5px] leading-snug opacity-70">
                Your speech is transcribed and replies are read aloud on the Mac only. Off, Sarvam (a trusted
                provider) handles it, which is far better in Hindi.
              </span>
            </span>
          </button>

          <div className="mt-5 font-mono text-[12px] leading-relaxed opacity-65">
            Voice in: {engines.stt ?? 'browser'} · voice out: {engines.tts ?? 'none'}
          </div>
          {localSttMissing && (
            <div className="mt-1 text-[13.5px]" style={{ color: '#e8a58f' }}>
              No speech-to-text is installed on the Mac yet (mlx-whisper or faster-whisper).
            </div>
          )}
          {firstAudio && (
            <div className="mt-2 font-mono text-[12px] leading-relaxed opacity-65">
              Last reply: first audio {seconds(firstAudio.totalMs)} after you stopped speaking
              (speech-to-text {seconds(firstAudio.sttMs)}, first words {seconds(firstAudio.firstTextMs)},
              voice {seconds(firstAudio.ttsMs)}{firstAudio.engine ? ` via ${firstAudio.engine}` : ''}
              {firstAudio.cache === 'hit' ? ', cached' : ''})
            </div>
          )}
        </div>
      )}

    </div>
  )
}
