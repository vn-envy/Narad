/**
 * useIsMobile — single source of truth for the phone breakpoint (M1.3).
 * ≤640px = phone layout: AwarenessBar becomes a bottom bar, side panels
 * become full-screen sheets, SplitPane stacks vertically.
 */
import { useEffect, useState } from 'react'

const QUERY = '(max-width: 640px)'

export function useIsMobile(): boolean {
  const [mobile, setMobile] = useState<boolean>(
    () => typeof window !== 'undefined' && window.matchMedia(QUERY).matches,
  )

  useEffect(() => {
    const mql = window.matchMedia(QUERY)
    const onChange = (e: MediaQueryListEvent) => setMobile(e.matches)
    mql.addEventListener('change', onChange)
    return () => mql.removeEventListener('change', onChange)
  }, [])

  return mobile
}
