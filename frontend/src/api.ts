import { mockAgents, mockCases, mockEvals, mockEvents, mockScenarios, mockTasks, mockWorld } from './mock'
import type { Agent, Case, ChannelMessage, EvalResult, HumanTask, ScenarioInfo, TraceEvent, WorldState } from './types'

export const apiBase = import.meta.env.VITE_API_BASE ?? 'http://localhost:8080'
export const worldBase = import.meta.env.VITE_WORLD_BASE ?? 'http://localhost:8000'
export const mockMode = import.meta.env.VITE_MOCK === '1'

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

export type CollectionResult<T> = { items: T[]; source: 'live' | 'mock' | 'unavailable' }

async function request<T>(base: string, path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(`${base}${path}`, { headers: { 'Content-Type': 'application/json', ...init?.headers }, ...init })
  } catch {
    throw new ApiError(0, 'Backend unreachable')
  }
  if (!response.ok) {
    let message = `Request failed (${response.status})`
    try {
      const body = await response.json() as { error?: { description?: string } }
      message = body.error?.description ?? message
    } catch { /* Non-JSON errors retain the status message. */ }
    throw new ApiError(response.status, message)
  }
  return response.json() as Promise<T>
}

function caseForView(item: Case): Case {
  return {
    ...item,
    domain_status: item.domain_status ?? {},
    approvals: item.open_approvals ?? item.approvals ?? 0,
  }
}

export const api = {
  async agents(): Promise<Agent[]> {
    if (mockMode) return mockAgents
    const { agents } = await request<{ agents: Array<Omit<Agent, 'id'> & { agent_id: string }> }>(apiBase, '/agents')
    return agents.map(({ agent_id, ...agent }) => ({ ...agent, id: agent_id }))
  },
  async channels(): Promise<string[]> {
    if (mockMode) return ['#onboarding', '#access', '#devices', '#systems', '#applications']
    return (await request<{ channels: string[] }>(apiBase, '/channels')).channels
  },
  async cases(): Promise<Case[]> {
    if (mockMode) return mockCases
    return (await request<Case[]>(apiBase, '/cases')).map(caseForView)
  },
  async caseDetail(caseId: string): Promise<Case> {
    if (mockMode) return mockCases.find((item) => item.case_id === caseId) ?? mockCases[0]
    return caseForView(await request<Case>(apiBase, `/cases/${encodeURIComponent(caseId)}`))
  },
  async events(caseId: string): Promise<TraceEvent[]> {
    if (mockMode) return mockEvents
    return (await request<{ events: TraceEvent[] }>(apiBase, `/cases/${encodeURIComponent(caseId)}/events`)).events
  },
  async tasks(caseId?: string): Promise<HumanTask[]> {
    if (mockMode) return mockTasks
    const query = caseId ? `?case_id=${encodeURIComponent(caseId)}` : ''
    return request<HumanTask[]>(apiBase, `/tasks${query}`)
  },
  async decideTask(taskId: string, decision: string | Record<string, unknown>, resolvedBy = 'M1'): Promise<HumanTask> {
    if (mockMode) {
      const task = mockTasks.find((item) => item.human_task_id === taskId) ?? mockTasks[0]
      return { ...task, status: 'resolved', decision, resolved_by: resolvedBy }
    }
    const body = typeof decision === 'string' ? { decision } : decision
    return request<HumanTask>(apiBase, `/tasks/${encodeURIComponent(taskId)}/decision`, {
      method: 'POST', body: JSON.stringify({ decision: body, resolved_by: resolvedBy }),
    })
  },
  async scenarios(): Promise<CollectionResult<ScenarioInfo>> {
    if (mockMode) return { items: mockScenarios, source: 'mock' }
    return { items: [], source: 'unavailable' }
  },
  async runScenario(id: string): Promise<ScenarioInfo> {
    if (mockMode) return mockScenarios.find((item) => item.id === id) ?? mockScenarios[0]
    throw new ApiError(501, 'Scenarios are not available from the live backend yet')
  },
  async evals(): Promise<CollectionResult<EvalResult>> {
    if (mockMode) return { items: mockEvals, source: 'mock' }
    return { items: [], source: 'unavailable' }
  },
  async world(employeeId: string): Promise<WorldState> {
    if (mockMode) return mockWorld
    const employeePath = encodeURIComponent(employeeId)
    const [employee, device, access, accessRequests] = await Promise.all([
      request<WorldState['employee']>(worldBase, `/world/employees/${employeePath}`),
      request<WorldState['device']>(worldBase, `/world/devices/${employeePath}`),
      request<WorldState['access']>(worldBase, `/world/access/${employeePath}`),
      request<WorldState['access_requests']>(worldBase, `/world/access/${employeePath}/requests`),
    ])
    return { employee, device, access, access_requests: accessRequests, inventory: {}, applications: {} }
  },
}

type SocketHandlers = {
  onMessage: (message: ChannelMessage) => void
  onEvent: (event: TraceEvent) => void
  onStatus?: (connected: boolean) => void
  onError?: (error: ApiError) => void
}

export class AgentSocket {
  private socket?: WebSocket
  private retry = 0
  private stopped = false
  private timer?: number
  constructor(private readonly handlers: SocketHandlers) {}
  connect(): void {
    if (mockMode || this.stopped) return
    const wsBase = apiBase.replace(/^http/, 'ws')
    this.socket = new WebSocket(`${wsBase}/ws/agents`)
    this.socket.onopen = () => {
      this.retry = 0
      this.handlers.onStatus?.(true)
      this.socket?.send(JSON.stringify({ type: 'subscribe', channels: ['#onboarding', '#access', '#devices', '#systems', '#applications'], events: true }))
    }
    this.socket.onmessage = (frame) => {
      try {
        const data = JSON.parse(String(frame.data)) as ChannelMessage | TraceEvent
        if ('channel' in data) this.handlers.onMessage(data)
        if (data.type === 'event') this.handlers.onEvent(data)
      } catch { /* Ignore malformed live frames. */ }
    }
    this.socket.onclose = () => { this.handlers.onStatus?.(false); this.scheduleReconnect() }
    this.socket.onerror = () => {
      this.handlers.onError?.(new ApiError(0, 'Backend WebSocket unreachable'))
      this.socket?.close()
    }
  }
  private scheduleReconnect(): void {
    if (this.stopped) return
    const delay = Math.min(1000 * 2 ** this.retry, 30000)
    this.retry += 1
    this.timer = window.setTimeout(() => this.connect(), delay)
  }
  close(): void { this.stopped = true; window.clearTimeout(this.timer); this.socket?.close() }
}
