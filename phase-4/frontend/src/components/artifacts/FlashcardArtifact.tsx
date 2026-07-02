import { useMemo, useState } from 'react'

interface Card {
  id: string
  front: string
  back: string
  tags?: string[]
}

interface Props {
  topic: string
  doc: {
    cards?: Card[]
  }
}

export function FlashcardArtifact({ topic, doc }: Props) {
  const cards = useMemo<Card[]>(() => {
    if (Array.isArray(doc.cards) && doc.cards.length > 0) return doc.cards
    return [
      {
        id: 'card-1',
        front: `What is ${topic}?`,
        back: `Explain ${topic} in one concise, plain-English definition.`,
      },
    ]
  }, [doc.cards, topic])
  const [current, setCurrent] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const card = cards[Math.min(current, cards.length - 1)]

  const go = (dir: 1 | -1) => {
    setCurrent(value => Math.max(0, Math.min(cards.length - 1, value + dir)))
    setFlipped(false)
  }

  return (
    <div className="flex flex-col h-full min-h-0">
      <div className="flex-1 min-h-0 overflow-y-auto p-4">
        <div
          onClick={() => setFlipped(value => !value)}
          className="flex items-center justify-center w-full rounded-lg cursor-pointer select-none"
          style={{
            minHeight: 240,
            background: flipped ? 'var(--kajal)' : 'var(--paper)',
            border: '1px solid color-mix(in srgb, var(--kajal) 12%, transparent)',
            transition: 'background 0.2s ease',
            padding: '20px 16px',
          }}
        >
          <p
            className="font-mono text-center leading-relaxed"
            style={{
              fontSize: 12,
              color: flipped ? 'rgba(252,250,242,0.85)' : 'rgba(45,42,38,0.8)',
            }}
          >
            {flipped ? card.back : card.front}
          </p>
        </div>

        <div className="mt-3 flex items-center justify-between gap-3">
          <p className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.35)' }}>
            click card to flip
          </p>
          <div className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.42)' }}>
            {current + 1} / {cards.length}
          </div>
        </div>

        {card.tags && card.tags.length > 0 && (
          <div className="mt-3 flex flex-wrap gap-2">
            {card.tags.map(tag => (
              <span
                key={tag}
                className="font-mono text-[9px] px-2 py-1 rounded-full"
                style={{ background: 'rgba(45,42,38,0.06)', color: 'rgba(45,42,38,0.48)' }}
              >
                {tag}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className="px-4 py-3 border-t flex items-center justify-between" style={{ borderColor: 'rgba(45,42,38,0.08)' }}>
        <button
          onClick={() => go(-1)}
          disabled={current === 0}
          className="font-mono text-[10px] px-2 py-1 rounded disabled:opacity-30"
          style={{ color: 'rgba(45,42,38,0.6)', border: '1px solid color-mix(in srgb, var(--kajal) 15%, transparent)' }}
        >
          ← previous
        </button>
        <button
          onClick={() => go(1)}
          disabled={current === cards.length - 1}
          className="font-mono text-[10px] px-2 py-1 rounded disabled:opacity-30"
          style={{ color: 'rgba(45,42,38,0.6)', border: '1px solid color-mix(in srgb, var(--kajal) 15%, transparent)' }}
        >
          next →
        </button>
      </div>
    </div>
  )
}
