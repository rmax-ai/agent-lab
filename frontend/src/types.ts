export type AgentStatus = 'online' | 'offline' | 'degraded'
export type DomainState = 'complete' | 'blocked' | 'running' | 'pending'

export interface Agent {
  id: string
  status: AgentStatus
  tools: number
  knowledge_docs: number
}

export interface DomainStatus {
  access: DomainState
  device: DomainState
  systems: DomainState
  applications: DomainState
}

export interface Case {
  case_id: string
  employee_id: string
  status: string
  domain_status: DomainStatus
  blockers: number
  approvals: number
  events?: string
}

export interface TraceEvent {
  ts: string
  case_id: string
  workflow_id: string | null
  actor: string
  type: string
  payload: Record<string, unknown>
}

export interface HumanTask {
  human_task_id: string
  case_id: string
  workflow_id: string
  requested_by: string
  requested_from: string
  type: string
  context: Record<string, unknown>
  allowed_actions: string[]
  status: 'open' | 'resolved'
  decision?: string | null
  resolved_by?: string | null
  created_at?: string
  resolved_at?: string | null
}

export interface ScenarioInfo {
  id: string
  status: 'not_run' | 'passed' | 'failed'
  score: number | null
  detail: string | null
}

export interface EvalResult {
  scenario_id: string
  result: 'pass' | 'fail'
  score: number
  expected: string[]
  observed: string[]
  final_state?: string
}

export interface WorldState {
  employee: Record<string, unknown>
  device: Record<string, unknown>
  inventory: Record<string, unknown>
  access: Record<string, unknown>
  applications: Record<string, unknown>
}

export interface ChannelMessage {
  type: 'channel_message'
  agent_id: string
  channel: string
  message: string
}

export interface EventFrame extends TraceEvent {
  type: 'event'
}
