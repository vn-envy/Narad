import { useEffect, useState, useCallback } from 'react'
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Separator } from '@/components/ui/separator'
import { Button } from '@/components/ui/button'
import { X, BookOpen, RefreshCw, FileText } from 'lucide-react'
import { toast } from 'sonner'
import { MadhubaniBorder } from './MadhubaniBorder'

const API = 'http://localhost:8000'

interface WikiPage {
  entity: string
  filename: string
  preview: string
  size_chars: number
}

const ENTITY_META: Record<string, { label: string; color: string }> = {
  decisions: { label: 'Decisions',  color: '#065f46' },
  features:  { label: 'Features',   color: '#1d4ed8' },
  goals:     { label: 'Goals',      color: '#6d28d9' },
  insights:  { label: 'Insights',   color: '#92610a' },
  context:   { label: 'Context',    color: '#57534e' },
}

function EntityBadge({ entity }: { entity: string }) {
  const meta = ENTITY_META[entity] ?? { label: entity, color: '#78716c' }
  return (
    <span
      className="font-mono text-[8px] px-1.5 py-px rounded uppercase tracking-wide"
      style={{ background: meta.color, color: '#fcfaf2' }}
    >
      {meta.label}
    </span>
  )
}

function WikiPageView({
  userId,
  page,
  onBack,
}: {
  userId: string
  page: WikiPage
  onBack: () => void
}) {
  const [content, setContent] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetch(`${API}/wiki/${userId}/${page.entity}`)
      .then(r => r.text())
      .then(text => { setContent(text); setDraft(text) })
      .catch(() => setContent('Failed to load page.'))
  }, [userId, page.entity])

  async function save() {
    setSaving(true)
    try {
      await fetch(`${API}/wiki/${userId}/${page.entity}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: draft }),
      })
      setContent(draft)
      setEditing(false)
      toast.success('Wiki page saved')
    } catch {
      toast.error('Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* Page header */}
      <div className="px-4 py-3 flex-shrink-0 flex items-center gap-2" style={{ background: 'var(--speckle)' }}>
        <button
          onClick={onBack}
          className="font-mono text-[9px] hover:opacity-70 transition-opacity"
          style={{ color: 'var(--kajal)' }}
        >
          ← back
        </button>
        <EntityBadge entity={page.entity} />
        <span className="font-mono text-[9px] ml-auto opacity-40" style={{ color: 'var(--kajal)' }}>
          {Math.ceil(page.size_chars / 1000)}k chars
        </span>
        <button
          onClick={() => setEditing(e => !e)}
          className="font-mono text-[9px] px-2 py-px rounded transition-colors"
          style={{
            background: editing ? 'var(--kajal)' : 'transparent',
            color: editing ? 'var(--paper)' : 'var(--kajal)',
            border: '1px solid color-mix(in srgb, var(--kajal) 30%, transparent)',
          }}
        >
          {editing ? 'Cancel' : 'Edit'}
        </button>
        {editing && (
          <Button
            size="sm"
            onClick={save}
            disabled={saving}
            className="h-6 px-2 font-mono text-[9px]"
          >
            {saving ? 'Saving…' : 'Save'}
          </Button>
        )}
      </div>

      <Separator className="opacity-20" />

      {/* Content */}
      <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
        <ScrollArea style={{ height: '100%' }}>
          {editing ? (
            <textarea
              value={draft}
              onChange={e => setDraft(e.target.value)}
              className="w-full h-full min-h-[400px] px-4 py-3 font-mono text-[11px] resize-none focus:outline-none"
              style={{
                background: 'var(--paper)',
                color: 'var(--kajal)',
                lineHeight: 1.6,
              }}
            />
          ) : (
            <div
              className="px-4 py-3 font-body text-[12px] leading-relaxed whitespace-pre-wrap"
              style={{ color: 'var(--kajal)' }}
            >
              {content ?? 'Loading…'}
            </div>
          )}
        </ScrollArea>
      </div>
    </div>
  )
}

export function ProjectWikiPanel({ userId = 'default' }: { userId?: string }) {
  const [open, setOpen] = useState(false)
  const [pages, setPages] = useState<WikiPage[]>([])
  const [selected, setSelected] = useState<WikiPage | null>(null)
  const [loading, setLoading] = useState(false)

  const loadPages = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/wiki/${userId}`)
      const data = await res.json()
      setPages(data.pages ?? [])
    } catch {
      /* server not ready */
    } finally {
      setLoading(false)
    }
  }, [userId])

  useEffect(() => {
    if (!open) return
    setSelected(null)
    loadPages()
  }, [open, loadPages])

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger
        className="flex items-center gap-1.5 px-2.5 py-1 rounded cursor-pointer relative organic-border hover:bg-kajal/30 transition-colors"
        style={{ background: 'rgba(252,250,242,0.10)', borderColor: 'rgba(252,250,242,0.20)' }}
      >
        <BookOpen size={11} style={{ color: 'var(--marigold)' }} />
        <span className="text-chip" style={{ color: 'rgba(252,250,242,0.70)' }}>WIKI</span>
        {pages.length > 0 && (
          <span
            className="absolute -top-1.5 -right-1.5 min-w-[15px] h-[15px] rounded-full font-mono text-[8px] font-bold flex items-center justify-center px-0.5"
            style={{ background: 'var(--marigold)', color: 'var(--paper)' }}
          >
            {pages.length}
          </span>
        )}
      </SheetTrigger>

      <SheetContent
        side="right"
        style={{ width: 420, maxWidth: 420, padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--paper)' }}
        className="border-l-2 border-kajal"
      >
        {/* Header */}
        <SheetHeader
          className="px-5 py-3.5 flex-shrink-0 relative overflow-hidden"
          style={{ background: 'var(--kajal)' }}
        >
          <div className="flex items-center gap-2">
            <SheetTitle className="flex items-baseline gap-2 flex-1">
              <BookOpen size={16} style={{ color: 'var(--marigold)' }} />
              <span className="label-hero text-[16px] leading-none" style={{ color: 'var(--paper)' }}>
                PROJECT WIKI
              </span>
              {pages.length > 0 && (
                <span className="font-mono text-[10px]" style={{ color: 'rgba(252,250,242,0.45)' }}>
                  {pages.length} pages
                </span>
              )}
            </SheetTitle>
            <button
              onClick={loadPages}
              className="p-1 rounded hover:bg-white/10 transition-colors"
              title="Refresh"
              style={{ color: 'rgba(252,250,242,0.55)' }}
            >
              <RefreshCw size={12} />
            </button>
            <SheetClose
              className="flex items-center justify-center w-7 h-7 rounded hover:bg-white/10 transition-colors"
              style={{ color: 'rgba(252,250,242,0.55)' }}
            >
              <X size={14} />
            </SheetClose>
          </div>
        </SheetHeader>

        <MadhubaniBorder position="bottom" />

        {/* Content */}
        {selected ? (
          <WikiPageView userId={userId} page={selected} onBack={() => setSelected(null)} />
        ) : (
          <>
            {/* Description */}
            <div className="px-4 py-3 flex-shrink-0" style={{ background: 'var(--speckle)' }}>
              <p className="font-body text-[11px] leading-[1.55] m-0 opacity-80" style={{ color: 'var(--kajal)' }}>
                <strong>Project Wiki</strong> — auto-compiled after every session by Scribe.
                Decisions, features, goals and insights your avatars have encountered are
                stored here as Markdown files you can read, edit, and version-control independently.
              </p>
            </div>

            <Separator className="opacity-20" />

            {/* Page list */}
            <div style={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <ScrollArea style={{ height: '100%' }}>
                <div className="px-4 py-3 flex flex-col gap-2">
                  {loading && (
                    <p className="font-body text-[11px] opacity-40 text-center py-6" style={{ color: 'var(--kajal)' }}>
                      Loading…
                    </p>
                  )}
                  {!loading && pages.length === 0 && (
                    <div className="text-center py-8">
                      <FileText size={24} className="mx-auto mb-2 opacity-20" style={{ color: 'var(--kajal)' }} />
                      <p className="font-body text-[11px] opacity-40 leading-relaxed" style={{ color: 'var(--kajal)' }}>
                        No wiki pages yet.{'\n'}Run a few sessions — Scribe will compile them here automatically.
                      </p>
                    </div>
                  )}
                  {pages.map(page => (
                    <button
                      key={page.entity}
                      onClick={() => setSelected(page)}
                      className="text-left flex flex-col gap-1 px-3 py-2.5 rounded organic-border hover:bg-kajal/5 transition-colors"
                      style={{ borderColor: 'color-mix(in srgb, var(--kajal) 15%, transparent)' }}
                    >
                      <div className="flex items-center gap-2">
                        <EntityBadge entity={page.entity} />
                        <span className="font-mono text-[9px] ml-auto opacity-30" style={{ color: 'var(--kajal)' }}>
                          {Math.ceil(page.size_chars / 1000)}k chars
                        </span>
                      </div>
                      {page.preview && (
                        <span className="font-body text-[11px] opacity-60 leading-snug" style={{ color: 'var(--kajal)' }}>
                          {page.preview}
                        </span>
                      )}
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
