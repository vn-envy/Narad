// Cache and push policy of public/sw.js, run in Node: `npm test`.
// The worker is loaded into a sandbox with a stub `self`, so these tests
// exercise the same functions the browser runs.
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import vm from 'node:vm'

const ORIGIN = 'https://narad.example.com'
const source = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8')

function loadWorker() {
  const listeners = {}
  const context = vm.createContext({
    URL,
    Response: class {},
    self: {
      location: { origin: ORIGIN },
      addEventListener: (type, handler) => { listeners[type] = handler },
    },
  })
  vm.runInContext(source, context)
  return { worker: context, listeners }
}

const { worker, listeners } = loadWorker()
const request = (path, { method = 'GET', mode = 'cors' } = {}) => ({
  url: path.startsWith('http') ? path : ORIGIN + path,
  method,
  mode,
})

test('registers every handler it needs', () => {
  for (const type of ['install', 'activate', 'fetch', 'push', 'notificationclick', 'message', 'pushsubscriptionchange']) {
    assert.equal(typeof listeners[type], 'function', type)
  }
})

test('navigations to the app are network-first; other documents pass through', () => {
  assert.equal(worker.requestStrategy(request('/', { mode: 'navigate' }), ORIGIN), 'navigate')
  assert.equal(worker.requestStrategy(request('/?approval=abc', { mode: 'navigate' }), ORIGIN), 'navigate')
  assert.equal(worker.requestStrategy(request('/index.html', { mode: 'navigate' }), ORIGIN), 'navigate')
  for (const path of ['/media/runs/asha/report/index.html', '/google/callback?code=x', '/health']) {
    assert.equal(worker.requestStrategy(request(path, { mode: 'navigate' }), ORIGIN), 'passthrough', path)
  }
})

test('only the app shell is cached; API responses and media never are', () => {
  assert.equal(worker.requestStrategy(request('/assets/index-abc123.js'), ORIGIN), 'asset')
  assert.equal(worker.requestStrategy(request('/assets/index-abc123.css'), ORIGIN), 'asset')
  assert.equal(worker.requestStrategy(request('/icons/icon-192.png'), ORIGIN), 'refresh')
  assert.equal(worker.requestStrategy(request('/manifest.webmanifest'), ORIGIN), 'refresh')
  for (const path of [
    '/inbox', '/inbox?user_id=asha', '/chat', '/profiles/session', '/push/devices',
    '/care-circle', '/media/computer-use/asha/shot.png', '/media/runs/asha/x.mp4', '/sw.js',
  ]) {
    assert.equal(worker.requestStrategy(request(path), ORIGIN), 'passthrough', path)
  }
  assert.equal(worker.requestStrategy(request('/assets/x.js', { method: 'POST' }), ORIGIN), 'passthrough')
})

test('Google Fonts are cached; other origins pass through', () => {
  assert.equal(worker.requestStrategy(request('https://fonts.gstatic.com/s/inter/v1/a.woff2'), ORIGIN), 'font')
  assert.equal(worker.requestStrategy(request('https://fonts.googleapis.com/css2?family=Inter'), ORIGIN), 'font')
  assert.equal(worker.requestStrategy(request('https://team.cloudflareaccess.com/cdn-cgi/access/login'), ORIGIN), 'passthrough')
  assert.equal(worker.requestStrategy(request('https://evil.example/assets/x.js'), ORIGIN), 'passthrough')
})

test('an Access redirect goes through; an unreachable Mac falls back to the shell', () => {
  assert.equal(worker.navigationOutcome({ type: 'opaqueredirect', status: 0 }), 'use')
  assert.equal(worker.navigationOutcome({ type: 'basic', status: 200 }), 'use')
  assert.equal(worker.navigationOutcome({ type: 'basic', status: 401 }), 'use')
  assert.equal(worker.navigationOutcome({ type: 'basic', status: 403 }), 'use')
  for (const status of [502, 503, 504, 520, 522, 524, 530]) {
    assert.equal(worker.navigationOutcome({ type: 'basic', status }), 'fallback', String(status))
  }
})

test('a sign-in page, a redirect or an error is never cached as the shell', () => {
  const ok = { status: 200, type: 'basic', redirected: false, url: `${ORIGIN}/` }
  assert.equal(worker.isCacheableShellResponse(ok, ORIGIN), true)
  assert.equal(worker.isCacheableShellResponse({ ...ok, redirected: true }, ORIGIN), false)
  assert.equal(worker.isCacheableShellResponse({ ...ok, url: 'https://team.cloudflareaccess.com/login' }, ORIGIN), false)
  assert.equal(worker.isCacheableShellResponse({ ...ok, type: 'opaqueredirect', status: 0 }, ORIGIN), false)
  assert.equal(worker.isCacheableShellResponse({ ...ok, type: 'cors' }, ORIGIN), false)
  assert.equal(worker.isCacheableShellResponse({ ...ok, status: 530 }, ORIGIN), false)
  assert.equal(worker.isCacheableShellResponse(null, ORIGIN), false)
})

test('push payloads are bounded and only open same-origin paths', () => {
  const payload = worker.parsePushPayload(JSON.stringify({
    title: 'Narad needs your OK', body: 'Open Narad to review it.', url: '/?approval=p1',
    tag: 'approval-p1', kind: 'approval_request', id: 'vahana-1', unread: 3,
  }), ORIGIN)
  assert.equal(payload.url, '/?approval=p1')
  assert.equal(payload.unread, 3)
  assert.equal(payload.kind, 'approval_request')
  assert.equal(worker.parsePushPayload(JSON.stringify({ url: 'https://evil.example/x' }), ORIGIN).url, '/')
  assert.equal(worker.parsePushPayload(JSON.stringify({ url: '//evil.example/x' }), ORIGIN).url, '/')
  assert.equal(worker.parsePushPayload(JSON.stringify({ url: 'javascript:alert(1)' }), ORIGIN).url, '/')
  const fallback = worker.parsePushPayload('not json', ORIGIN)
  assert.equal(fallback.title, 'Narad')
  assert.equal(fallback.unread, null)
  assert.equal(worker.parsePushPayload(JSON.stringify({ title: 'x'.repeat(500) }), ORIGIN).title.length, 80)
})

test('the build stamps a version and the shell list', () => {
  assert.match(source, /const BUILD_VERSION = '__NARAD_BUILD_VERSION__'/)
  assert.match(source, /const SHELL_ASSETS = \/\* __NARAD_SHELL_ASSETS__ \*\/ \['\/'\]/)
})
