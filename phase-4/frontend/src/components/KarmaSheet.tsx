import { useState } from 'react'
import {
  Sheet,
  SheetClose,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { X } from 'lucide-react'
import { KarmaPanel } from './KarmaPanel'

interface Props {
  userId?: string
  children?: React.ReactNode
}

export function KarmaSheet({ userId = 'default', children }: Props) {
  const [open, setOpen] = useState(false)

  return (
    <Sheet open={open} onOpenChange={setOpen}>
      <SheetTrigger style={{ all: 'unset', cursor: 'pointer' }} onClick={() => setOpen(true)}>
        {children ?? (
          <div
            className="flex items-center gap-1.5 px-2.5 py-1 rounded cursor-pointer relative organic-border hover:bg-kajal/30 transition-colors"
            style={{ background: 'rgba(252,250,242,0.10)', borderColor: 'rgba(252,250,242,0.20)' }}
          >
            <span className="font-deva text-[13px]" style={{ color: 'var(--marigold)', fontFamily: 'var(--font-deva)' }}>कर्म</span>
            <span className="text-chip" style={{ color: 'rgba(252,250,242,0.70)' }}>KARMA</span>
          </div>
        )}
      </SheetTrigger>

      <SheetContent
        side="right"
        style={{ width: 420, maxWidth: 420, padding: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: 'var(--paper)' }}
        className="border-l-2 border-kajal"
      >
        <SheetHeader
          className="px-5 py-3.5 flex-shrink-0 relative overflow-hidden"
          style={{ background: 'var(--kajal)' }}
        >
          <div className="flex items-center gap-2">
            <SheetTitle className="flex items-center gap-2 flex-1">
              <span className="font-deva text-[20px]" style={{ color: 'var(--marigold)', fontFamily: 'var(--font-deva)' }}>कर्म</span>
              <span className="label-hero text-[16px] leading-none" style={{ color: 'var(--paper)' }}>KARMA LOG</span>
            </SheetTitle>
            <SheetClose
              className="flex items-center justify-center w-7 h-7 rounded hover:bg-white/10 transition-colors flex-shrink-0"
              style={{ color: 'rgba(252,250,242,0.55)' }}
            >
              <X size={14} />
            </SheetClose>
          </div>
        </SheetHeader>
        {/* KarmaPanel renders inside the sheet — compact=true hides the legend */}
        <div style={{ flex: 1, overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
          <KarmaPanel userId={userId} compact={false} />
        </div>
      </SheetContent>
    </Sheet>
  )
}
