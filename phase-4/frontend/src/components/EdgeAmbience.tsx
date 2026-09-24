/**
 * Edge ambience: the page stays white (black at night); colour appears only at
 * the screen's edges, in the colour of whichever avatāra is working, breathing
 * slowly. Several at once take turns. While Narad is choosing an avatar the
 * edges glow sindoor. When no one is working there is no glow at all.
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
  // Keep the last colours while the glow fades out, so it never flashes grey.
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
          className="edge-glow"
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
