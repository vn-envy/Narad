import agentContracts from '../../../../contracts/agent-contracts.json'

import type { AvatarName } from '../hooks/useAvatara'

type AgentContractJson = {
  agents: Array<{
    name: AvatarName
    disciplines: string[]
    ui: {
      deva: string
      abbrev: string
      color: string
      rgb: string
    }
  }>
}

const contract = agentContracts as AgentContractJson

export const AGENT_CONTRACTS = contract.agents
export const AVATAR_NAMES: AvatarName[] = AGENT_CONTRACTS.map(agent => agent.name)
export const AVATAR_DISCIPLINES: Record<AvatarName, string[]> = Object.fromEntries(
  AGENT_CONTRACTS.map(agent => [agent.name, agent.disciplines])
) as Record<AvatarName, string[]>
export const AVATAR_COLOURS: Record<AvatarName, string> = Object.fromEntries(
  AGENT_CONTRACTS.map(agent => [agent.name, agent.ui.color])
) as Record<AvatarName, string>
export const AVATAR_RGB: Record<AvatarName, string> = Object.fromEntries(
  AGENT_CONTRACTS.map(agent => [agent.name, agent.ui.rgb])
) as Record<AvatarName, string>
export const DEVA: Record<AvatarName, string> = Object.fromEntries(
  AGENT_CONTRACTS.map(agent => [agent.name, agent.ui.deva])
) as Record<AvatarName, string>
export const AVATAR_ABBREV: Record<AvatarName, string> = Object.fromEntries(
  AGENT_CONTRACTS.map(agent => [agent.name, agent.ui.abbrev])
) as Record<AvatarName, string>

export function isAvatarName(value: string): value is AvatarName {
  return value in AVATAR_COLOURS
}

export function avatarColour(value: string | null | undefined, fallback = '#57534e'): string {
  if (!value) {
    return fallback
  }
  return isAvatarName(value) ? AVATAR_COLOURS[value] : fallback
}
