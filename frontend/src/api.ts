import { mockAgents, mockCases, mockEvals, mockEvents, mockScenarios, mockTasks, mockWorld } from './mock'
import type { Agent, Case, ChannelMessage, EvalResult, HumanTask, ScenarioInfo, TraceEvent, WorldState } from './types'

const apiBase = import.meta.env.VITE_API_BASE ?? 'http://localhost:8000'
const mockMode = import.meta.env.VITE_MOCK === '1'

async function request<T>(path: string, fallback: T, init?: RequestInit): Promise<T> {
  if (mockMode) return fallback
  try {
    const response = await fetch(`${apiBase}${path}`, { headers: { 'Content-Type': 'application/json' }, ...init })
    if (!response.ok) throw new Error(`${response.status}`)
    return (await response.json()) as T
  } catch {
    return fallback
  }
}

export const api = {
  async agents(): Promise<Agent[]> { return (await request<{ agents: Agent[] }>('/agents', { agents: mockAgents })).agents },
  async cases(): Promise<Case[]> { return (await request<{ cases: Case[] }>('/cases', { cases: mockCases })).cases },
  async caseDetail(caseId: string): Promise<Case> { return request(`/cases/${caseId}`, mockCases.find((item) => item.case_id === caseId) ?? mockCases[0]) },
  async events(caseId: string): Promise<TraceEvent[]> { return (await request<{ events: TraceEvent[] }>(`/cases/${caseId}/events`, { events: mockEvents })).events },
  async tasks(): Promise<HumanTask[]> { return (await request<{ tasks: HumanTask[] }>('/tasks', { tasks: mockTasks })).tasks },
  async decideTask(taskId: string, decision: string): Promise<HumanTask> {
    const fallback = { ...(mockTasks.find((task) => task.human_task_id === taskId) ?? mockTasks[0]), status: 'resolved' as const, decision, resolved_by: 'M1' }
    return request(`/tasks/${taskId}/decision`, fallback, { method: 'POST', body: JSON.stringify({ decision, resolved_by: 'M1' }) })
  },
  async scenarios(): Promise<ScenarioInfo[]> { return (await request<{ scenarios: ScenarioInfo[] }>('/scenarios', { scenarios: mockScenarios })).scenarios },
  async runScenario(id: string): Promise<ScenarioInfo> { return request(`/scenarios/${id}/run`, mockScenarios.find((item) => item.id === id) ?? mockScenarios[0], { method: 'POST' }) },
  async evals(): Promise<EvalResult[]> { return (await request<{ evals: EvalResult[] }>('/evals', { evals: mockEvals })).evals },
  async world(employeeId: string): Promise<WorldState> { return request(`/world/inspect?employee_id=${encodeURIComponent(employeeId)}`, mockWorld) },
}

type SocketHandlers = { onMessage: (message: ChannelMessage) => void; onEvent: (event: TraceEvent) => void; onStatus?: (connected: boolean) => void }

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
    this.socket.onerror = () => this.socket?.close()
  }
  private scheduleReconnect(): void {
    if (this.stopped) return
    const delay = Math.min(1000 * 2 ** this.retry, 30000)
    this.retry += 1
    this.timer = window.setTimeout(() => this.connect(), delay)
  }
  close(): void { this.stopped = true; window.clearTimeout(this.timer); this.socket?.close() }
}
