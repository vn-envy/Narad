/**
 * Edge lights: the page stays white (black at night). While an avatāra
 * works, the outermost column of dots on both sides of the screen takes its
 * colour and breathes; several at once take turns. While Narad is choosing
 * who goes, the dots are sindoor. When no one is working, nothing lights.
 */
import { useEffect, useState } from 'react'
import type { AvatarState } from '../hooks/useAvatara'

interface Props {
  avatars: Record<string, { name: string; state: AvatarState }>
  naradActive: boolean
}

export function ambienceColours(avatars: Props['avatars'], naradActive: boolean): string[] {
  const working = Object.values(avatars)
    .filter(a => a.state === 'active')
    .map(a => `var(--avatar-${a.name.toLowerCase()})`)
  if (working.length) return working
  return naradActive ? ['var(--avatar-narad)'] : []
}

export function EdgeAmbience({ avatars, naradActive }: Props) {
  const colours = ambienceColours(avatars, naradActive)
  const on = colours.length > 0
  const key = colours.join('|')
  // Keep the last colours while the dots fade out, so they never flash grey.
  const [shown, setShown] = useState<string[]>(colours)
  useEffect(() => {
    if (on) setShown(key.split('|'))
  }, [key, on])
  const turns = shown.length > 1
  const period = 3.2 * Math.max(1, shown.length)
  return (
    <div className="edge-ambience" data-on={on ? 'true' : 'false'} aria-hidden="true">
      {shown.map((colour, i) => (
        <span
          key={colour}
          className="edge-dots"
          data-turns={turns ? 'true' : 'false'}
          style={{
            ['--glow' as string]: colour,
            animationDuration: turns ? `${period}s` : undefined,
            animationDelay: turns ? `${-(i * period) / shown.length}s` : undefined,
          }}
        />
      ))}
    </div>
  )
}
