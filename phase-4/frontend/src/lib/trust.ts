/** Privacy receipts, feedback and consent: the plain-language layer over
 *  privacy_gateway.privacy_receipt, GET /privacy/egress, POST /feedback and
 *  GET/POST /consent. Counts only ever arrive here; there are no values to show. */
import { apiFetch, apiUrl } from './api'

export type TrustLang = 'en' | 'hi'

export interface ReceiptDestination {
  provider: string
  tier: 'trusted' | 'redact' | 'web' | 'blocked' | string
  sources: string[]
  calls: number
  replaced: Record<string, number>
}

export interface PrivacyReceipt {
  v: number
  turn_id: string | null
  stayed_local: boolean
  destinations: ReceiptDestination[]
  refused: Record<string, number>
}

/** One row of the caller's egress ledger (GET /privacy/egress). */
export interface EgressRow {
  ts: string
  source: string
  model: string
  provider: string
  tier: string
  entities?: Record<string, number>
  blocked?: string
  turn_id?: string
}

export interface ConsentStatus {
  profile: string
  current_version: string
  accepted: boolean
  owner: boolean
  needs_consent: boolean
  enforced?: boolean
  decided_at?: string | null
  sheet?: { lang: TrustLang; languages: TrustLang[]; markdown: string }
}

/** Mirrors pilot_metrics.FEEDBACK_REASONS (phase-1/test_pilot_metrics.py checks). */
export const FEEDBACK_REASONS: Array<{ id: string; en: string; hi: string }> = [
  { id: 'wrong', en: 'Wrong', hi: 'गलत' },
  { id: 'incomplete', en: 'Incomplete', hi: 'अधूरा' },
  { id: 'not_what_i_asked', en: 'Not what I asked', hi: 'मैंने यह नहीं पूछा' },
  { id: 'too_slow', en: 'Too slow', hi: 'बहुत धीमा' },
  { id: 'unsafe', en: 'Unsafe', hi: 'असुरक्षित' },
  { id: 'unneeded_approval', en: 'Asked me to approve for no reason', hi: 'बेवजह मंज़ूरी मांगी' },
  { id: 'language', en: 'Wrong language', hi: 'भाषा ठीक नहीं' },
  { id: 'other', en: 'Something else', hi: 'कुछ और' },
]

const PROVIDERS: Record<string, string> = {
  anthropic: 'Claude',
  'narad-claude-sdk': 'Claude',
  openai: 'OpenAI',
  gemini: 'Gemini',
  vertex_ai: 'Gemini',
  azure: 'Azure OpenAI',
  bedrock: 'AWS Bedrock',
  deepseek: 'DeepSeek',
  nebius: 'Nebius',
  fireworks_ai: 'Fireworks',
  together_ai: 'Together',
  openrouter: 'OpenRouter',
  deepinfra: 'DeepInfra',
  groq: 'Groq',
  cerebras: 'Cerebras',
  mimo: 'MiMo',
  typesafe: 'Jev',
  smallest: 'Smallest.ai',
  sarvam: 'Sarvam',
  xai: 'xAI',
  exa: 'Exa',
  tavily: 'Tavily',
  firecrawl: 'Firecrawl',
  arxiv: 'arXiv',
  semantic_scholar: 'Semantic Scholar',
  huggingface: 'Hugging Face',
  deepwiki: 'DeepWiki',
  community_sites: 'Reddit, Hacker News and GitHub',
}

export function providerName(provider: string, lang: TrustLang = 'en'): string {
  if (PROVIDERS[provider]) return PROVIDERS[provider]
  if (provider === 'website') return lang === 'hi' ? 'एक वेबसाइट' : 'A website'
  if (provider === 'custom') return lang === 'hi' ? 'एक कस्टम सेवा' : 'A custom service'
  return lang === 'hi' ? 'एक अनजान सेवा' : 'An unknown service'
}

const KINDS: Record<string, { en: [string, string]; hi: string }> = {
  PERSON: { en: ['name', 'names'], hi: 'नाम' },
  PHONE: { en: ['phone number', 'phone numbers'], hi: 'फ़ोन नंबर' },
  EMAIL: { en: ['email address', 'email addresses'], hi: 'ईमेल पते' },
  ADDRESS: { en: ['address', 'addresses'], hi: 'पते' },
  AADHAAR: { en: ['Aadhaar number', 'Aadhaar numbers'], hi: 'आधार नंबर' },
  PAN: { en: ['PAN', 'PANs'], hi: 'PAN नंबर' },
  UPI: { en: ['UPI ID', 'UPI IDs'], hi: 'UPI ID' },
  IFSC: { en: ['bank branch code', 'bank branch codes'], hi: 'IFSC कोड' },
  PASSPORT: { en: ['passport number', 'passport numbers'], hi: 'पासपोर्ट नंबर' },
  CARD: { en: ['card number', 'card numbers'], hi: 'कार्ड नंबर' },
  ACCOUNT: { en: ['account number', 'account numbers'], hi: 'खाता नंबर' },
  ID: { en: ['ID number', 'ID numbers'], hi: 'पहचान नंबर' },
}

export function totalReplaced(replaced: Record<string, number> | undefined): number {
  return Object.values(replaced ?? {}).reduce((sum, n) => sum + (Number(n) || 0), 0)
}

/** "2 names and 1 phone number" / "2 नाम और 1 फ़ोन नंबर". */
export function replacedPhrase(replaced: Record<string, number> | undefined, lang: TrustLang): string {
  const parts = Object.entries(replaced ?? {})
    .filter(([, n]) => n > 0)
    .map(([kind, n]) => {
      const label = KINDS[kind]
      if (lang === 'hi') return `${n} ${label?.hi ?? 'अन्य जानकारी'}`
      const [one, many] = label?.en ?? ['other detail', 'other details']
      return `${n} ${n === 1 ? one : many}`
    })
  if (parts.length <= 1) return parts[0] ?? ''
  const joiner = lang === 'hi' ? ' और ' : ' and '
  return `${parts.slice(0, -1).join(', ')}${joiner}${parts[parts.length - 1]}`
}

function detailsCount(n: number, lang: TrustLang): string {
  if (lang === 'hi') return `${n} ${n === 1 ? 'जानकारी' : 'जानकारियां'}`
  return `${n} ${n === 1 ? 'detail' : 'details'}`
}

const MODEL_TIERS = new Set(['trusted', 'redact'])

/** The destination that wrote the answer, else the first one. */
function primaryDestination(receipt: PrivacyReceipt): ReceiptDestination | undefined {
  const { destinations } = receipt
  return destinations.find(d => MODEL_TIERS.has(d.tier) && d.sources.includes('answer'))
    ?? destinations.find(d => MODEL_TIERS.has(d.tier))
    ?? destinations[0]
}

/** The chip under an answer: one calm line. */
export function receiptChipLabel(receipt: PrivacyReceipt, lang: TrustLang): string {
  if (receipt.stayed_local || receipt.destinations.length === 0) {
    return lang === 'hi' ? 'आपके Mac पर ही रहा' : 'Stayed on your Mac'
  }
  const primary = primaryDestination(receipt)!
  const name = providerName(primary.provider, lang)
  let label: string
  if (primary.tier === 'web') {
    const search = primary.sources.includes('search')
    label = lang === 'hi'
      ? (search ? 'सर्च इंजन ने सवाल देखा' : 'वेबसाइट ने पेज का पता देखा')
      : (search ? 'Search engine saw the question' : 'A website saw the page address')
  } else if (primary.tier === 'redact') {
    const n = totalReplaced(primary.replaced)
    label = lang === 'hi'
      ? `${name} ने इसे देखा, ${n ? `${detailsCount(n, lang)} बदलकर` : 'बदलने को कुछ नहीं था'}`
      : `${name} saw this ${n ? `with ${detailsCount(n, lang)} replaced` : 'with nothing to replace'}`
  } else {
    label = lang === 'hi' ? `${name} ने इसे देखा` : `${name} saw this`
  }
  const more = receipt.destinations.length - 1
  if (more > 0) label += lang === 'hi' ? ` · ${more} और` : ` · ${more} more`
  return label
}

/** Ledger source (raw, or a receipt's grouped one) to what it was for. */
export function sourceKind(source: string): string {
  if (['answer', 'memory', 'search', 'web', 'voice', 'lesson', 'check', 'image', 'video', 'learning', 'reminder'].includes(source)) {
    return source
  }
  if (source === 'agent' || source === 'background') return 'answer'
  if (source === 'embedding') return 'memory'
  if (source === 'stt' || source === 'tts') return 'voice'
  if (source === 'imagen') return 'image'
  if (source === 'veo') return 'video'
  if (source === 'tapas' || source === 'sankalpa') return 'learning'
  if (source.startsWith('guru') || source === 'guided_presenter') return 'lesson'
  if (source.startsWith('jev:')) return 'check'
  if (source.startsWith('kala_scheduler')) return 'reminder'
  return 'other'
}

const PURPOSES: Record<string, { en: string; hi: string }> = {
  answer: { en: 'to write an answer', hi: 'जवाब लिखने के लिए' },
  memory: { en: 'to search your memories', hi: 'आपकी यादें खोजने के लिए' },
  search: { en: 'to search the web', hi: 'वेब पर खोजने के लिए' },
  web: { en: 'to open a page', hi: 'पेज खोलने के लिए' },
  voice: { en: 'for voice', hi: 'आवाज़ के लिए' },
  lesson: { en: 'for a lesson', hi: 'पाठ के लिए' },
  check: { en: 'for a quick check', hi: 'एक जांच के लिए' },
  image: { en: 'to make an image', hi: 'तस्वीर बनाने के लिए' },
  video: { en: 'to make a video', hi: 'वीडियो बनाने के लिए' },
  learning: { en: 'to learn from how it went', hi: 'बातचीत से सीखने के लिए' },
  reminder: { en: 'for a scheduled reminder', hi: 'तय रिमाइंडर के लिए' },
  other: { en: "for Narad's work", hi: 'नारद के काम के लिए' },
}

export function purpose(source: string, lang: TrustLang): string {
  return (PURPOSES[sourceKind(source)] ?? PURPOSES.other)[lang]
}

const TIERS: Record<string, { en: string; hi: string }> = {
  trusted: { en: 'trusted', hi: 'भरोसेमंद' },
  redact: { en: 'names hidden', hi: 'नाम छिपाकर' },
  web: { en: 'web', hi: 'वेब' },
  blocked: { en: 'blocked', hi: 'बंद' },
}

export function tierLabel(tier: string, lang: TrustLang): string {
  return (TIERS[tier] ?? { en: tier, hi: tier })[lang]
}

const REFUSALS: Record<string, { en: string; hi: string }> = {
  policy: { en: 'the service is blocked', hi: 'यह सेवा बंद है' },
  leak: { en: 'a personal detail was still there after replacing', hi: 'बदलने के बाद भी कोई निजी जानकारी बची थी' },
  redactor_unavailable: { en: "the Mac's name-finding model is not installed", hi: 'Mac पर नाम पहचानने वाला मॉडल इंस्टॉल नहीं है' },
  media: { en: "names can't be hidden in a photo or file", hi: 'फ़ोटो या फ़ाइल में नाम नहीं छिपाए जा सकते' },
  raw_content: { en: 'voice only goes to trusted services', hi: 'आवाज़ सिर्फ़ भरोसेमंद सेवाओं को जाती है' },
}

export function refusalReason(reason: string, lang: TrustLang): string {
  const key = reason.split(':', 1)[0]
  return (REFUSALS[key] ?? { en: 'a privacy rule said no', hi: 'एक प्राइवेसी नियम ने रोका' })[lang]
}

/** One plain sentence for a destination in a receipt or a ledger entry. */
export function destinationSentence(
  provider: string,
  tier: string,
  source: string,
  replaced: Record<string, number> | undefined,
  lang: TrustLang,
): string {
  const name = providerName(provider, lang)
  const kind = sourceKind(source)
  const what = replacedPhrase(replaced, lang)
  if (tier === 'web') {
    const words = kind === 'search'
      ? (lang === 'hi' ? 'खोज के शब्द' : 'the search words')
      : (lang === 'hi' ? 'पेज का पता' : 'the page address')
    if (lang === 'hi') return `${name} को ${words} मिले${what ? `; उनमें ${what} की जगह प्लेसहोल्डर थे` : ''}।`
    return `${name} received ${words}${what ? `, with ${what} left as placeholders` : ''}.`
  }
  const why = purpose(kind, lang)
  if (tier === 'redact') {
    if (lang === 'hi') {
      return what
        ? `${name} को आपका टेक्स्ट ${why} मिला, पर ${what} की जगह <PERSON_1> जैसे प्लेसहोल्डर थे। असली जानकारी आपके Mac पर ही रही।`
        : `${name} को आपका टेक्स्ट ${why} मिला। उसमें बदलने लायक कोई निजी जानकारी नहीं मिली।`
    }
    return what
      ? `${name} received your text ${why}, with ${what} swapped for placeholders like <PERSON_1>. The real ones stayed on your Mac.`
      : `${name} received your text ${why}. No personal details were found that needed replacing.`
  }
  if (lang === 'hi') return `${name} को आपका टेक्स्ट ${why} वैसा ही मिला जैसा लिखा गया था। यह भरोसेमंद सेवा है: इसकी शर्तें इस पर ट्रेनिंग मना करती हैं।`
  return `${name} received your text ${why}, as written. It is a trusted service: its terms rule out training on it.`
}

/** Replies mostly in Devanagari get Hindi labels. */
export function textLang(text: string): TrustLang {
  const devanagari = (text.match(/[ऀ-ॿ]/g) ?? []).length
  const latin = (text.match(/[A-Za-z]/g) ?? []).length
  return devanagari > latin ? 'hi' : 'en'
}

// ── API ───────────────────────────────────────────────────────────────────────

export async function postFeedback(body: {
  session_id: string
  turn_id: string
  rating: 'up' | 'down'
  reason?: string | null
}): Promise<boolean> {
  try {
    const response = await apiFetch('/feedback', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    return response.ok
  } catch {
    return false
  }
}

export async function fetchEgress(limit = 200): Promise<EgressRow[]> {
  const response = await apiFetch(apiUrl('/privacy/egress', { limit }))
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json() as { calls?: EgressRow[] }
  return Array.isArray(data.calls) ? data.calls : []
}

export async function fetchConsent(lang: TrustLang): Promise<ConsentStatus> {
  const response = await apiFetch(apiUrl('/consent', { part: 'a', lang }))
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  return response.json() as Promise<ConsentStatus>
}

export async function acceptConsent(version: string): Promise<ConsentStatus> {
  const response = await apiFetch('/consent', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ version, accepted: true }),
  })
  const data = await response.json().catch(() => ({})) as ConsentStatus & { detail?: string }
  if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`)
  return data
}

/** useAvatara fires this when the server answers 403 consent_required. */
export const CONSENT_REQUIRED_EVENT = 'narad:consent-required'
