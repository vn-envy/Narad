/**
 * Keeps the app exactly as tall as what the person can see.
 *
 * Android Chrome honours `interactive-widget=resizes-content` (index.html):
 * the keyboard shrinks the layout viewport and the composer rides on top of
 * it. Browsers that only shrink the visual viewport (iOS Safari, older
 * Chrome) would slide the page up and hide the composer; for them the app
 * shell follows `visualViewport.height` through --app-height. Pinch zoom also
 * changes the visual viewport, so only an unzoomed one is followed.
 *
 * While the keyboard is open, <html> carries `keyboard-open`, and the phone's
 * bottom navigation steps aside to give the conversation that room.
 */

const KEYBOARD_MIN_PX = 150

export function trackVisualViewport(): () => void {
  if (typeof window === 'undefined' || !window.visualViewport) return () => undefined
  const viewport = window.visualViewport
  const root = document.documentElement
  let tallest = viewport.height
  let frame = 0

  const update = () => {
    frame = 0
    if (Math.abs(viewport.scale - 1) > 0.01) return
    const height = Math.round(viewport.height)
    tallest = Math.max(tallest, height, window.innerHeight)
    root.style.setProperty('--app-height', `${height}px`)
    root.classList.toggle('keyboard-open', tallest - height > KEYBOARD_MIN_PX)
    // The browser may have scrolled the page to reveal a field; the shell is
    // already the right size, so put it back.
    if (window.scrollY !== 0 || document.scrollingElement?.scrollTop) window.scrollTo(0, 0)
  }
  const schedule = () => {
    if (!frame) frame = requestAnimationFrame(update)
  }
  // A rotation starts a new "tallest" height.
  const rotated = () => {
    tallest = 0
    schedule()
  }

  viewport.addEventListener('resize', schedule)
  viewport.addEventListener('scroll', schedule)
  window.addEventListener('orientationchange', rotated)
  update()
  return () => {
    viewport.removeEventListener('resize', schedule)
    viewport.removeEventListener('scroll', schedule)
    window.removeEventListener('orientationchange', rotated)
    if (frame) cancelAnimationFrame(frame)
  }
}
