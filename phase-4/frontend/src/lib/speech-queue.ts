import { apiFetch } from './api'

/*
 * One audio pipeline for spoken replies: segments are synthesized in order
 * with a small prefetch (at most `prefetch` ahead of what is playing) and
 * scheduled back to back on a single Web Audio clock, so they play without
 * gaps. `stop()` silences playback and aborts in-flight requests (barge-in).
 */

export interface SpeechTiming {
  /** performance.now() when the segment was queued, arrived and started playing. */
  queuedAt: number
  fetchedAt?: number
  startedAt?: number
  /** Synthesis time on the server, from its Server-Timing header. */
  serverMs?: number
  engine?: string
  cache?: string
}

export interface SpeechQueueHandlers {
  /** A segment started playing; `first` marks the first one of this queue. */
  onStart?: (text: string, timing: SpeechTiming, first: boolean) => void
  /** Closed, and every segment has played (or failed). */
  onIdle?: (played: number) => void
  /** First failure only, so a dead engine shows one toast, not one per sentence. */
  onError?: (message: string) => void
}

export interface SpeechVoice {
  avatar: string
  lang: string
  /** The stream that produced this segment, for dropUnspoken(). */
  source?: string
}

type ItemState = 'queued' | 'fetching' | 'ready' | 'scheduled' | 'playing' | 'played' | 'dropped' | 'failed'

const FINISHED: ItemState[] = ['played', 'dropped', 'failed']
const AHEAD: ItemState[] = ['fetching', 'ready', 'scheduled']

interface Item {
  text: string
  voice: SpeechVoice
  state: ItemState
  controller: AbortController | null
  audio: AudioBuffer | null
  node: AudioBufferSourceNode | null
  end: number
  startTimer: number | null
  timing: SpeechTiming
}

let sharedContext: AudioContext | null = null
let unlocked = false

function playbackContext(): AudioContext | null {
  if (typeof window === 'undefined') return null
  if (!sharedContext) {
    const Ctor = window.AudioContext
      ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return null
    sharedContext = new Ctor()
  }
  return sharedContext
}

/**
 * Call inside a tap handler. Android Chrome starts audio only after a user
 * gesture; resuming the context and playing one silent sample within the
 * gesture keeps later, gesture-less playback allowed.
 */
export function unlockAudio(): void {
  const ctx = playbackContext()
  if (!ctx) return
  if (ctx.state === 'suspended') void ctx.resume().catch(() => undefined)
  if (unlocked && ctx.state === 'running') return
  try {
    const node = ctx.createBufferSource()
    node.buffer = ctx.createBuffer(1, 1, ctx.sampleRate)
    node.connect(ctx.destination)
    node.start(0)
    unlocked = true
  } catch { /* closed or unsupported: playback reports it later */ }
}

function serverTiming(header: string | null, name: string): number | undefined {
  const match = header ? new RegExp(`(?:^|,)\\s*${name};[^,]*dur=([\\d.]+)`).exec(header) : null
  return match ? Number(match[1]) : undefined
}

function base64ToBuffer(b64: string): ArrayBuffer {
  const bytes = atob(b64)
  const out = new Uint8Array(bytes.length)
  for (let i = 0; i < bytes.length; i++) out[i] = bytes.charCodeAt(i)
  return out.buffer
}

async function fetchSpeech(text: string, voice: SpeechVoice, signal: AbortSignal) {
  const res = await apiFetch('/voice/tts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Accept: 'audio/wav' },
    body: JSON.stringify({ text, avatar: voice.avatar.toLowerCase(), lang: voice.lang }),
    signal,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => null) as { detail?: string } | null
    throw new Error(err?.detail ?? `TTS ${res.status}`)
  }
  const timing = res.headers.get('Server-Timing')
  const data = (res.headers.get('Content-Type') ?? '').includes('json')
    ? base64ToBuffer(((await res.json()) as { audio_b64: string }).audio_b64)
    : await res.arrayBuffer()
  return {
    data,
    engine: res.headers.get('X-Narad-TTS-Engine') ?? undefined,
    cache: res.headers.get('X-Narad-TTS-Cache') ?? undefined,
    serverMs: serverTiming(timing, 'tts'),
  }
}

export class SpeechQueue {
  private items: Item[] = []
  private closed = false
  private stopped = false
  private idleFired = false
  private errored = false
  private played = 0
  private playhead = 0   // AudioContext time at which the last scheduled segment ends
  private readonly handlers: SpeechQueueHandlers
  private readonly prefetch: number

  constructor(handlers: SpeechQueueHandlers = {}, prefetch = 2) {
    this.handlers = handlers
    this.prefetch = Math.max(1, prefetch)
  }

  enqueue(text: string, voice: SpeechVoice): void {
    if (this.stopped || !text.trim()) return
    this.items.push({
      text, voice, state: 'queued', controller: null, audio: null, node: null, end: 0,
      startTimer: null, timing: { queuedAt: performance.now() },
    })
    this.pump()
  }

  /** No more segments are coming; onIdle fires once the last one has played. */
  close(): void {
    this.closed = true
    this.checkIdle()
  }

  /** Texts that have started playing: they can't be taken back. */
  spoken(): string[] {
    return this.items.filter(item => item.state === 'playing' || item.state === 'played').map(item => item.text)
  }

  get playedCount(): number {
    return this.played
  }

  get failed(): boolean {
    return this.errored
  }

  /** Drop what hasn't started yet: all of it, or one source's after a text_reset. */
  dropUnspoken(source?: string): void {
    for (const item of this.items) {
      const unspoken = !FINISHED.includes(item.state) && item.state !== 'playing'
      if (unspoken && (source === undefined || item.voice.source === source)) this.cancel(item)
    }
    const live = this.items.filter(item => item.state === 'scheduled' || item.state === 'playing')
    this.playhead = live.length ? Math.max(...live.map(item => item.end)) : 0
    this.pump()
    this.checkIdle()
  }

  /** Barge-in: silence now, forget everything, abort requests. Never reports idle. */
  stop(): void {
    this.stopped = true
    for (const item of this.items) this.cancel(item)
    this.items = []
    this.playhead = 0
  }

  private cancel(item: Item): void {
    item.controller?.abort()
    item.controller = null
    if (item.startTimer !== null) window.clearTimeout(item.startTimer)
    item.startTimer = null
    if (item.node) {
      item.node.onended = null
      try { item.node.stop() } catch { /* never started */ }
      item.node.disconnect()
      item.node = null
    }
    if (!FINISHED.includes(item.state)) item.state = item.state === 'playing' ? 'played' : 'dropped'
  }

  private pump(): void {
    if (this.stopped) return
    // Fetch in order, never more than `prefetch` ahead of what is playing.
    let ahead = this.items.filter(item => AHEAD.includes(item.state)).length
    for (const item of this.items) {
      if (ahead >= this.prefetch) break
      if (item.state !== 'queued') continue
      ahead++
      void this.fetch(item)
    }
    this.schedule()
  }

  private async fetch(item: Item): Promise<void> {
    item.state = 'fetching'
    item.controller = new AbortController()
    try {
      const ctx = playbackContext()
      if (!ctx) throw new Error('Audio playback is not available in this browser')
      const got = await fetchSpeech(item.text, item.voice, item.controller.signal)
      const audio = await ctx.decodeAudioData(got.data)
      if (item.state !== 'fetching') return   // dropped or stopped meanwhile
      item.audio = audio
      item.state = 'ready'
      item.timing = {
        ...item.timing, fetchedAt: performance.now(),
        engine: got.engine, cache: got.cache, serverMs: got.serverMs,
      }
    } catch (err) {
      if (item.state !== 'fetching') return
      item.state = 'failed'   // skip it; the next segment still plays
      if (!(err instanceof Error && err.name === 'AbortError') && !this.errored) {
        this.errored = true
        this.handlers.onError?.(err instanceof Error ? err.message : String(err))
      }
    } finally {
      item.controller = null
    }
    this.pump()
    this.checkIdle()
  }

  /** Put every ready segment, in order, on the audio clock right after the previous one. */
  private schedule(): void {
    const ctx = sharedContext
    if (!ctx || this.stopped) return
    for (const item of this.items) {
      if (item.state !== 'queued' && item.state !== 'fetching' && item.state !== 'ready') continue
      if (item.state !== 'ready' || !item.audio) return   // keep order: wait for this one
      if (ctx.state === 'suspended') void ctx.resume().catch(() => undefined)
      const node = ctx.createBufferSource()
      node.buffer = item.audio
      node.connect(ctx.destination)
      const when = Math.max(ctx.currentTime + 0.03, this.playhead)
      node.start(when)
      item.node = node
      item.end = when + item.audio.duration
      item.state = 'scheduled'
      this.playhead = item.end
      node.onended = () => {
        this.markStarted(item)
        item.state = 'played'
        item.node = null
        this.pump()
        this.checkIdle()
      }
      this.armStart(item, ctx, when)
    }
  }

  /** Report the segment as started when the audio clock reaches it (not while it is suspended). */
  private armStart(item: Item, ctx: AudioContext, when: number): void {
    item.startTimer = window.setTimeout(() => {
      item.startTimer = null
      if (item.state !== 'scheduled') return
      if (ctx.state !== 'running' || ctx.currentTime < when - 0.01) {
        this.armStart(item, ctx, when)
        return
      }
      this.markStarted(item)
      this.pump()
    }, ctx.state === 'running' ? Math.max(20, (when - ctx.currentTime) * 1000) : 250)
  }

  private markStarted(item: Item): void {
    if (item.state !== 'scheduled') return
    item.state = 'playing'
    item.timing.startedAt = performance.now()
    this.played++
    this.handlers.onStart?.(item.text, item.timing, this.played === 1)
  }

  private checkIdle(): void {
    if (!this.closed || this.stopped || this.idleFired) return
    if (!this.items.every(item => FINISHED.includes(item.state))) return
    this.idleFired = true
    this.handlers.onIdle?.(this.played)
  }
}
