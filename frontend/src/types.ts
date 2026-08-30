export type AgentStatus = 'online' | 'offline' | 'degraded'
export type DomainState = 'complete' | 'blocked' | 'running' | 'pending'

export interface Agent {
  id: string
  status: AgentStatus
  tools: number
  knowledge_docs: number
  connected_at?: string | null
}

export type DomainStatus = Partial<Record<'access' | 'device' | 'systems' | 'applications', DomainState>>

/** Backend case list rows; `domain_status` is present only on GET /cases/{case_id}. */
export interface Case {
  case_id: string
  employee_id: string
  status: string
  blockers: number
  open_approvals?: number
  created_at?: string
  context?: Record<string, unknown>
  domain_status: DomainStatus
  /** UI alias normalized from the backend's `open_approvals` list field. */
  approvals?: number
  events?: string
}

/** Canonical SDK Event serialized by GET /cases/{case_id}/events. */
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
  /** Live backend uses a decision object; string support is retained for mock replay. */
  decision?: Record<string, unknown> | string | null
  resolved_by?: string | null
  created_at: string
  resolved_at?: string | null
}

export interface ScenarioInfo {
  id: string
  /** Retained for the legacy mock replay. */
  status?: 'not_run' | 'passed' | 'failed'
  score?: number | null
  detail?: string | null
  domain?: 'devices' | 'access' | 'integration' | 'hidden'
  file?: string
  hidden?: boolean
  required_events?: string[]
  allowed_final_states?: string[]
  forbidden_events?: string[]
}

/** Legacy mock evaluation result. Live evaluation uses EvalModel instead. */
export interface EvalResult {
  scenario_id: string
  result: 'pass' | 'fail'
  score: number
  expected: string[]
  observed: string[]
  final_state?: string
}

export interface EvalModel {
  dimensions: Array<{ name: string; weight: number }>
  threshold: number
  packs: {
    devices: string[]
    access: string[]
    integration: string[]
    hidden_count: number
  }
}

export interface WorldEmployee extends Record<string, unknown> {
  id?: string
  name?: string
  role?: string
  location?: string
  manager_id?: string | null
  manager_name?: string | null
  start_date?: string
  status?: string
}

export interface WorldDevice extends Record<string, unknown> {
  required_sku?: string
  assigned_device?: Record<string, unknown> | null
  order?: Record<string, unknown> | null
}

export interface WorldAccess extends Record<string, unknown> {
  identity?: Record<string, unknown> | string | null
  entitlements?: Array<Record<string, unknown>>
  groups?: Array<Record<string, unknown>>
}

export interface WorldAccessRequest extends Record<string, unknown> {
  id: string
  employee_id: string
  group_id: string | null
  description: string | null
  status: string
}

/** Composed from MockWorld employee, device, access, and access-request endpoints. */
export interface WorldState {
  employee: WorldEmployee
  device: WorldDevice
  access: WorldAccess
  access_requests?: WorldAccessRequest[]
  /** Legacy mock-replay fields retained until WorldView is upgraded in D.2b. */
  inventory: Record<string, unknown>
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
