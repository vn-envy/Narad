/*
 * Speech segments: turn a streaming markdown answer into short pieces that can
 * be read aloud one by one, so voice starts with the first sentence instead of
 * after the whole reply.
 *
 * Boundaries are sentence ends (. ? ! । ॥), line ends and list items. Decimals
 * (8.2), times (10:30), amounts (₹1,200.50), common abbreviations (Dr., e.g.,
 * vs.) and URLs never split. A long first sentence may end at a comma so the
 * first audio is not held back. Code blocks, tables and raw URLs are replaced
 * by a short spoken note; markdown symbols are dropped.
 *
 * No React and no imports: phase-1/test_voice_segments.py runs it under node.
 */

export type SpeechLang = 'en' | 'hi'

export interface SplitterOptions {
  /** Language of the spoken notes for code, tables and links; 'auto' follows the text. */
  lang?: SpeechLang | 'auto'
  /** The first segment may end at a comma once it is this long. */
  firstSoftChars?: number
  /** Later segments may end at a comma once they are this long. */
  softChars?: number
  /** No segment is longer than this. */
  maxChars?: number
  /** Shorter pieces wait to join the next one ("Sure." plus the next sentence). */
  minChars?: number
  /** After this many spoken characters, say the rest is on screen and stop. */
  totalChars?: number
}

const DEFAULTS: Required<SplitterOptions> = {
  lang: 'en',
  firstSoftChars: 120,
  softChars: 240,
  maxChars: 360,
  minChars: 20,
  totalChars: 3000,
}

const NOTES = {
  en: {
    code: "I've put the code on screen.",
    inlineCode: 'the code on screen',
    table: 'The table is on screen.',
    link: 'the link on screen',
    links: 'the links on screen',
    rest: 'The rest is on screen.',
  },
  hi: {
    code: 'कोड स्क्रीन पर है।',
    inlineCode: 'स्क्रीन पर दिया कोड',
    table: 'टेबल स्क्रीन पर है।',
    link: 'स्क्रीन पर दिया लिंक',
    links: 'स्क्रीन पर दिए लिंक',
    rest: 'बाकी जवाब स्क्रीन पर है।',
  },
} as const

type Notes = (typeof NOTES)[SpeechLang]

// A "." after these never ends a sentence ("Dr. Sharma", "e.g. rice", "डॉ. शर्मा").
const ABBREVIATIONS = new Set([
  'dr', 'mr', 'mrs', 'ms', 'prof', 'sr', 'jr', 'st', 'shri', 'smt', 'capt', 'col', 'gen', 'lt', 'rev',
  'e.g', 'i.e', 'eg', 'ie', 'cf', 'vs', 'viz', 'al', 'approx', 'ca', 'dept', 'govt', 'ltd', 'pvt',
  'co', 'inc', 'corp',
  'डॉ', 'श्री', 'श्रीमती', 'सुश्री', 'कु', 'प्रो', 'मि', 'पं', 'स्व',
])
// These are abbreviations only before a number ("No. 5", "Rs. 500", "रु. 500").
const NUMBER_ABBREVIATIONS = new Set(['no', 'nos', 'rs', 'fig', 'vol', 'ch', 'pp', 'sec', 'रु'])
// "10 a.m. tomorrow" goes on; "at 10 a.m. Carry the report" ends the sentence.
const TIME_SUFFIXES = new Set(['a.m', 'p.m'])

const DEVANAGARI = /[ऀ-ॿ]/
const DANDA = '।॥'
const TERMINAL = '.!?…'
const CLOSERS = '"\'”’)]*_`'
const SOFT = ',;:،'
const URL_RE = /(?:\bhttps?:\/\/|\bwww\.)[^\s<>"'`]+/gi
const LINK_SENTINEL = '\u0001'

export function hasDevanagari(text: string): boolean {
  return DEVANAGARI.test(text)
}

/** Letters and digits only, lowercased: for "was this already spoken?" checks. */
export function speechKey(text: string): string {
  return text.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '')
}

function isSpace(ch: string | undefined): boolean {
  return ch !== undefined && /\s/.test(ch)
}

/** The word before position `end` ("Dr" in "see Dr."). */
function wordBefore(text: string, end: number): string {
  let start = end
  while (start > 0 && !isSpace(text[start - 1]) && !'([{"\'“‘*_'.includes(text[start - 1])) start--
  return text.slice(start, end).toLowerCase()
}

/**
 * End index (exclusive) of the first complete sentence in `text`, or -1.
 * `complete` means nothing more will be appended (the line ended), so a
 * terminal mark at the very end counts.
 */
export function findSentenceEnd(text: string, complete: boolean): number {
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (DANDA.includes(ch)) {
      let j = i + 1
      while (j < text.length && (DANDA.includes(text[j]) || CLOSERS.includes(text[j]))) j++
      return j
    }
    if (!TERMINAL.includes(ch)) continue
    let j = i
    while (j < text.length && TERMINAL.includes(text[j])) j++
    const mark = text.slice(i, j)
    while (j < text.length && CLOSERS.includes(text[j])) j++
    if (j >= text.length) return complete ? j : -1  // "8." may yet become "8.2"
    if (!isSpace(text[j])) { i = j - 1; continue }   // 8.2, example.com, e.g
    if (mark.includes('.') || mark.includes('…')) {
      if (mark === '.') {
        const word = wordBefore(text, i)
        const initials = /^[a-z]$/.test(word) || (/^([a-z]\.)+[a-z]$/.test(word) && !TIME_SUFFIXES.has(word))
        if (ABBREVIATIONS.has(word) || initials) {
          i = j - 1
          continue
        }
        if (NUMBER_ABBREVIATIONS.has(word)) {
          const next = text.slice(j).trimStart()
          if (!next) return complete ? j : -1
          if (/^\d/.test(next)) { i = j - 1; continue }
        }
      }
      // "etc. and more", "wait... what": a lowercase word goes on with the sentence.
      let k = j
      while (k < text.length && isSpace(text[k])) k++
      if (k >= text.length) return complete ? j : -1
      if (/[a-z]/.test(text[k])) { i = j - 1; continue }
    }
    return j
  }
  return -1
}

/**
 * Index just after the first clause break (", " "; " ": " " — ") at or after
 * `from`, or -1. The first one, so a streamed and a whole text split alike.
 */
export function findSoftBreak(text: string, from: number): number {
  for (let p = Math.max(from, 1); p < text.length - 1; p++) {
    const ch = text[p]
    if (!isSpace(text[p + 1])) continue
    if (SOFT.includes(ch)) return p + 1
    if ('—–-'.includes(ch) && isSpace(text[p - 1])) return p + 1
  }
  return -1
}

/** Markdown and raw URLs out, spoken notes in. Returns '' when nothing is left to say. */
export function cleanForSpeech(raw: string, notes: Notes = NOTES.en): string {
  let text = raw
    .replace(/!\[[^\]]*\]\([^)]*\)/g, ' ')                        // images
    .replace(/\[([^\]]+)\]\((?:[^()\s]|\([^()\s]*\))*\)/g, '$1')  // [text](url) → text
    .replace(/<(https?:\/\/[^>\s]+)>/gi, '$1')                     // <autolinks>
    .replace(URL_RE, match => {
      const trail = /[.,;:!?)\]}]+$/.exec(match)?.[0] ?? ''
      return LINK_SENTINEL + trail
    })
    .replace(/`([^`\n]+)`/g, (_, code: string) => (code.length <= 40 ? code : notes.inlineCode))
    .replace(/<\/?[a-z][^>]*>/gi, ' ')                             // stray HTML tags
    .replace(/\[\^?\d+\]/g, '')                                    // footnote markers
    .replace(/\*\*|__|~~/g, '')
    .replace(/(^|[\s(])[*_]+(?=\S)/g, '$1')                        // opening *emphasis*
    .replace(/(\S)[*_]+(?=[\s).,;:!?।]|$)/g, '$1')                 // closing emphasis*
    .replace(/[*`|\\~^]/g, '')
    .replace(/(^|\s)#+(?=\s|$)/g, '$1')
    .replace(/[\p{Extended_Pictographic}‍️]/gu, '')
  text = text
    .replace(new RegExp(`${LINK_SENTINEL}(?:[\\s,;]*(?:and\\s+|or\\s+|और\\s+)?${LINK_SENTINEL})+`, 'g'), notes.links)
    .replace(new RegExp(LINK_SENTINEL, 'g'), notes.link)
    .replace(/\s+/g, ' ')
    .trim()
  return /[\p{L}\p{N}]/u.test(text) ? text : ''
}

type LineKind = 'unknown' | 'prose' | 'skip'

const FENCE = /^\s*(```|~~~)/
const TABLE_ROW = /^\s*\|/
const RULE = /^\s*([-*_])(\s*\1){2,}\s*$/
const LINE_MARKER = /^(?:\s*(?:#{1,6}\s+|>\s?|[-*+•]\s+|\d{1,3}[.)]\s+|\[[ xX]\]\s+))+/

/**
 * Feed the answer as it streams (`push`), then `finish` when it is complete.
 * Each call returns the segments that became ready to speak.
 */
export class SpeechSplitter {
  private readonly opts: Required<SplitterOptions>
  private line = ''            // raw text of the current, unfinished line
  private lineKind: LineKind = 'unknown'
  private lineUsed = 0         // chars of `line` already moved into `sentence`
  private heading = false
  private sentence = ''        // prose of the current line not yet emitted
  private inCode = false
  private inTable = false
  private carry = ''           // a short piece waiting to join the next one
  private emitted = 0
  private spokenChars = 0
  private last = ''
  private sawDevanagari = false
  private capped = false
  private out: string[] = []

  constructor(options: SplitterOptions = {}) {
    this.opts = { ...DEFAULTS, ...options }
  }

  /** True once the total cap was reached; later text is left to the screen. */
  get done(): boolean {
    return this.capped
  }

  push(delta: string): string[] {
    if (this.capped || !delta) return this.take()
    if (!this.sawDevanagari && hasDevanagari(delta)) this.sawDevanagari = true
    let rest = delta.replace(/\r/g, '')
    while (rest) {
      const nl = rest.indexOf('\n')
      if (nl < 0) {
        this.line += rest
        this.processLine(false)
        break
      }
      this.line += rest.slice(0, nl)
      rest = rest.slice(nl + 1)
      this.processLine(true)
    }
    return this.take()
  }

  finish(): string[] {
    if (this.line) this.processLine(true)
    if (this.carry) {
      const carried = this.carry
      this.carry = ''
      this.output(carried)
    }
    return this.take()
  }

  private take(): string[] {
    const ready = this.out
    this.out = []
    return ready
  }

  private notes(): Notes {
    const lang = this.opts.lang === 'auto' ? (this.sawDevanagari ? 'hi' : 'en') : this.opts.lang
    return NOTES[lang]
  }

  private processLine(complete: boolean): void {
    if (this.capped) return
    const line = this.line
    if (this.lineKind === 'unknown') this.classify(line, complete)
    if (this.lineKind === 'prose') {
      this.sentence += line.slice(this.lineUsed)
      this.lineUsed = line.length
      this.drain(complete)
    }
    if (complete) {
      this.line = ''
      this.lineKind = 'unknown'
      this.lineUsed = 0
      this.heading = false
    }
  }

  /** Decide what a line is once its first word is known (or it has ended). */
  private classify(line: string, complete: boolean): void {
    if (this.inCode) {
      if (!complete) return
      if (FENCE.test(line)) this.inCode = false
      this.lineKind = 'skip'
      return
    }
    if (!complete && !/\S\s/.test(line)) return
    if (FENCE.test(line)) {
      this.inCode = true
      this.lineKind = 'skip'
      this.note('code')
      return
    }
    if (TABLE_ROW.test(line)) {
      if (!complete) return
      this.lineKind = 'skip'
      if (!this.inTable) this.note('table')
      this.inTable = true
      return
    }
    this.inTable = false
    if (!line.trim() || RULE.test(line)) {
      if (complete) this.lineKind = 'skip'
      return
    }
    const marker = LINE_MARKER.exec(line)?.[0] ?? ''
    this.heading = /^\s*#/.test(marker)
    this.lineUsed = marker.length
    this.lineKind = 'prose'
  }

  private drain(complete: boolean): void {
    for (;;) {
      if (this.capped) return
      const end = findSentenceEnd(this.sentence, complete)
      if (end > 0) {
        this.emitProse(this.sentence.slice(0, end), end >= this.sentence.length && complete)
        this.sentence = this.sentence.slice(end).trimStart()
        continue
      }
      const first = this.emitted === 0 && !this.carry
      const soft = first ? this.opts.firstSoftChars : this.opts.softChars
      if (this.sentence.length < soft) break
      let cut = findSoftBreak(this.sentence, Math.floor(soft / 2))
      if (cut < 0 && this.sentence.length >= this.opts.maxChars) {
        const space = this.sentence.lastIndexOf(' ', this.opts.maxChars)
        cut = space > soft / 2 ? space : this.opts.maxChars
      }
      if (cut < 0) break
      this.emitProse(this.sentence.slice(0, cut), false)
      this.sentence = this.sentence.slice(cut).trimStart()
    }
    if (complete && this.sentence.trim()) {
      this.emitProse(this.sentence, true)
    }
    if (complete) this.sentence = ''
  }

  private emitProse(raw: string, lineEnd: boolean): void {
    let text = cleanForSpeech(raw, this.notes())
    if (!text) return
    if ((lineEnd || this.heading) && !/[.!?।॥…:;,]$/.test(text)) {
      text += hasDevanagari(text.slice(-1)) ? '।' : '.'
    }
    this.piece(text, false)
  }

  private note(kind: 'code' | 'table'): void {
    if (this.sentence.trim()) this.emitProse(this.sentence, true)
    this.sentence = ''
    this.piece(this.notes()[kind], true)
  }

  private piece(text: string, force: boolean): void {
    if (this.carry) {
      text = `${this.carry} ${text}`
      this.carry = ''
    }
    if (!force && text.length < this.opts.minChars) {
      this.carry = text
      return
    }
    this.output(text)
  }

  private output(text: string): void {
    if (this.capped) return
    if (speechKey(text) === speechKey(this.last)) return  // "the link on screen." twice
    if (this.emitted > 0 && this.spokenChars + text.length > this.opts.totalChars) {
      this.out.push(this.notes().rest)
      this.capped = true
      return
    }
    this.out.push(text)
    this.last = text
    this.emitted++
    this.spokenChars += text.length
  }
}

/** Every segment of a finished text. */
export function splitForSpeech(text: string, options: SplitterOptions = {}): string[] {
  const splitter = new SpeechSplitter(options)
  return [...splitter.push(text), ...splitter.finish()]
}

/**
 * Segments of `finalText` still to speak when it no longer matches what was
 * streamed: anything whose words were already spoken is skipped.
 */
export function remainingSegments(finalText: string, spoken: string[], options: SplitterOptions = {}): string[] {
  const said = speechKey(spoken.join(' '))
  return splitForSpeech(finalText, options).filter(segment => {
    const key = speechKey(segment)
    return key && !said.includes(key)
  })
}
