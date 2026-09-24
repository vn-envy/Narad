export type AppSurface = 'chat' | 'activity' | 'workspaces' | 'you' | 'memory' | 'system'

export type DashboardSurface = Exclude<AppSurface, 'chat'>

/**
 * Where the app can go. The phone's bottom bar shows the four `phone` items;
 * Memory and System open from You there. The desktop rail shows all of them.
 */
export const SURFACE_ITEMS: Array<{
  id: AppSurface
  label: string
  phone: boolean
}> = [
  { id: 'chat', label: 'Chat', phone: true },
  { id: 'activity', label: 'Activity', phone: true },
  { id: 'workspaces', label: 'Paths', phone: true },
  { id: 'memory', label: 'Memory', phone: false },
  { id: 'system', label: 'System', phone: false },
  { id: 'you', label: 'You', phone: true },
]
