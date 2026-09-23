import { LogOut } from 'lucide-react'
import type { FamilyProfile } from '@/lib/api'

const PROFILE_COLORS: Record<string, string> = {
  sindoor: '#c2410c',
  matsya: '#2450a4',
  rama: '#a16207',
  krishna: '#0f766e',
  parashurama: '#9f1239',
  nila: '#3d477f',
  gulab: '#b0316b',
  tulsi: '#065f46',
}

export function ProfileBadge({
  profile,
  onSwitch,
  compact = false,
}: {
  profile: FamilyProfile
  onSwitch: () => void
  compact?: boolean
}) {
  return (
    <button
      type="button"
      className="profile-badge"
      onClick={onSwitch}
      title={`Switch from ${profile.display_name}'s private profile`}
      aria-label={`Switch profile. Current profile: ${profile.display_name}`}
    >
      <span
        className="profile-badge-avatar"
        style={{ background: PROFILE_COLORS[profile.color] || PROFILE_COLORS.sindoor }}
      >
        {profile.initial}
      </span>
      {!compact && <span className="profile-badge-name">{profile.display_name}</span>}
      {!compact && <LogOut size={11} className="profile-badge-exit" />}
    </button>
  )
}
