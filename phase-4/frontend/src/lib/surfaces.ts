export type AppSurface = 'chat' | 'activity' | 'workspaces' | 'memory' | 'system'

export type DashboardSurface = Exclude<AppSurface, 'chat'>

export const SURFACE_ITEMS: Array<{
  id: AppSurface
  label: string
  icon: string
}> = [
  { id: 'chat', label: 'Chat', icon: '○' },
  { id: 'activity', label: 'Activity', icon: '◷' },
  { id: 'workspaces', label: 'Workflows', icon: '◆' },
  { id: 'memory', label: 'Memory', icon: '◎' },
  { id: 'system', label: 'System', icon: '◇' },
]
