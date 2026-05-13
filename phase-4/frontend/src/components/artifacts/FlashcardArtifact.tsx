import { useState, useEffect } from 'react'
import { useCopilotAction, useCopilotReadable, useCopilotChat } from '@copilotkit/react-core'
import { TextMessage, Role } from '@copilotkit/runtime-client-gql'

interface Card { q: string; a: string }

export function FlashcardArtifact({ topic }: { topic: string }) {
  const [cards, setCards] = useState<Card[]>([])
  const [current, setCurrent] = useState(0)
  const [flipped, setFlipped] = useState(false)
  const [generating, setGenerating] = useState(true)

  const { appendMessage } = useCopilotChat()

  useCopilotReadable({
    description: 'Flashcard deck currently displayed to the learner',
    value: cards,
  })

  useCopilotAction({
    name: 'addFlashcard',
    description: 'Add a flashcard to the deck. Call this 6–8 times to seed the initial deck.',
    parameters: [
      { name: 'question', type: 'string', description: 'The question / front of the card' },
      { name: 'answer',   type: 'string', description: 'The answer / back of the card' },
    ],
    handler: ({ question, answer }) => {
      setCards(prev => [...prev, { q: question, a: answer }])
      setGenerating(false)
    },
  })

  useEffect(() => {
    const timer = setTimeout(() => {
      appendMessage(new TextMessage({
        role: Role.User,
        content: `Generate 7 flashcards covering the key concepts of: "${topic}". Call addFlashcard once per card. Do not write any prose — only call the action.`,
      }))
    }, 400)
    return () => clearTimeout(timer)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [topic])

  const go = (dir: 1 | -1) => {
    setCurrent(c => Math.max(0, Math.min(cards.length - 1, c + dir)))
    setFlipped(false)
  }

  if (generating && cards.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <p className="font-mono text-[10px]" style={{ color: 'rgba(45,42,38,0.35)' }}>
          generating flashcards…
        </p>
      </div>
    )
  }

  const card = cards[current]

  return (
    <div className="flex flex-col items-center gap-3 p-4 h-full">
      {/* Card */}
      <div
        onClick={() => setFlipped(f => !f)}
        className="flex items-center justify-center w-full rounded-lg cursor-pointer select-none"
        style={{
          flex: '1 1 0',
          minHeight: 0,
          background: flipped ? 'var(--kajal)' : 'var(--paper)',
          border: '1px solid color-mix(in srgb, var(--kajal) 12%, transparent)',
          transition: 'background 0.2s ease',
          padding: '20px 16px',
        }}
      >
        <p
          className="font-mono text-center leading-relaxed"
          style={{
            fontSize: 11,
            color: flipped ? 'rgba(252,250,242,0.85)' : 'rgba(45,42,38,0.8)',
          }}
        >
          {flipped ? card.a : card.q}
        </p>
      </div>

      {/* Flip hint */}
      <p className="font-mono text-[8px]" style={{ color: 'rgba(45,42,38,0.3)' }}>
        {flipped ? 'answer' : 'click to reveal'}
      </p>

      {/* Navigation */}
      <div className="flex items-center gap-3">
        <button
          onClick={() => go(-1)}
          disabled={current === 0}
          className="font-mono text-[10px] px-2 py-1 rounded disabled:opacity-30"
          style={{ color: 'rgba(45,42,38,0.6)', background: 'transparent', border: '1px solid color-mix(in srgb, var(--kajal) 15%, transparent)' }}
        >
          ←
        </button>
        <span className="font-mono text-[9px]" style={{ color: 'rgba(45,42,38,0.4)' }}>
          {current + 1} / {cards.length}
        </span>
        <button
          onClick={() => go(1)}
          disabled={current === cards.length - 1}
          className="font-mono text-[10px] px-2 py-1 rounded disabled:opacity-30"
          style={{ color: 'rgba(45,42,38,0.6)', background: 'transparent', border: '1px solid color-mix(in srgb, var(--kajal) 15%, transparent)' }}
        >
          →
        </button>
      </div>
    </div>
  )
}
