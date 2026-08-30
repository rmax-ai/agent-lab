import { useEffect, useMemo, useState } from 'react'
import { AgentSocket, api, mockMode } from './api'
import { mockAgents, mockCases, mockEvals, mockEvents, mockMessages, mockScenarios, mockTasks } from './mock'
import type { Agent, Case, ChannelMessage, EvalResult, HumanTask, ScenarioInfo, TraceEvent } from './types'
import { AgentsView } from './views/AgentsView'
import { CasesView } from './views/CasesView'
import { ChannelsView } from './views/ChannelsView'
import { EvalsView } from './views/EvalsView'
import { ScenariosView } from './views/ScenariosView'
import { TasksView } from './views/TasksView'
import { TraceView } from './views/TraceView'
import { WorldView } from './views/WorldView'

const tabs = ['Case', 'World', 'Trace', 'Human Tasks', 'Scenarios', 'Evals'] as const
type Tab = (typeof tabs)[number]

type ConnectionState = 'live' | 'mock replay' | 'backend unreachable'

function ConnectionBadge({ status }: { status: ConnectionState }) {
  const color = status === 'live' ? '#7ee787' : status === 'mock replay' ? '#e3b341' : '#ff7b72'
  return <span className={`connection ${status === 'live' ? 'connected' : ''}`} style={{ color, borderColor: color }}>{status}</span>
}

function Conversation({ channel, messages, status }: { channel: string; messages: ChannelMessage[]; status: ConnectionState }) {
  const visible = useMemo(() => messages.filter((message) => message.channel === channel), [channel, messages])
  return <section className="conversation"><header><div><h1>{channel}</h1><p className="muted">Agent conversation</p></div><ConnectionBadge status={status} /></header>{visible.length === 0 ? <p className="empty">0 results</p> : <div className="messages">{visible.map((message, index) => <article key={`${message.agent_id}-${index}`}><strong>{message.agent_id}</strong><p>{message.message}</p></article>)}</div>}</section>
}

export default function App() {
  const [tab, setTab] = useState<Tab>('Case')
  const [channel, setChannel] = useState('#onboarding')
  const [agents, setAgents] = useState<Agent[]>(mockMode ? mockAgents : [])
  const [cases, setCases] = useState<Case[]>(mockMode ? mockCases : [])
  const [events, setEvents] = useState<TraceEvent[]>(mockMode ? mockEvents : [])
  const [tasks, setTasks] = useState<HumanTask[]>(mockMode ? mockTasks : [])
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>(mockMode ? mockScenarios : [])
  const [evals, setEvals] = useState<EvalResult[]>(mockMode ? mockEvals : [])
  const [messages, setMessages] = useState<ChannelMessage[]>(mockMode ? mockMessages : [])
  const [loading, setLoading] = useState<Record<string, boolean>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [connection, setConnection] = useState<ConnectionState>(mockMode ? 'mock replay' : 'live')

  useEffect(() => {
    let mounted = true
    const load = async <T,>(name: string, promise: Promise<T>, apply: (value: T) => void) => {
      setLoading((items) => ({ ...items, [name]: true }))
      try {
        const value = await promise
        if (!mounted) return
        apply(value)
        setErrors((items) => { const { [name]: _ignored, ...rest } = items; return rest })
        if (!mockMode) setConnection('live')
      } catch (error) {
        if (!mounted) return
        setErrors((items) => ({ ...items, [name]: error instanceof Error ? error.message : 'Request failed' }))
        if (!mockMode) setConnection('backend unreachable')
      } finally {
        if (mounted) setLoading((items) => ({ ...items, [name]: false }))
      }
    }

    void load('agents', api.agents(), setAgents)
    void load('tasks', api.tasks(), setTasks)
    void load('channels', api.channels(), () => undefined)
    void load('scenarios', api.scenarios(), (result) => setScenarios(result.items))
    void load('evals', api.evals(), (result) => setEvals(result.items))
    void load('cases', api.cases(), (nextCases) => {
      setCases(nextCases)
      const firstCase = nextCases[0]
      if (firstCase) void load('events', api.events(firstCase.case_id), setEvents)
    })

    const socket = new AgentSocket({
      onMessage: (message) => setMessages((items) => [...items, message]),
      onEvent: (event) => setEvents((items) => [...items, event]),
      onStatus: (connected) => { if (!mockMode && !connected) setConnection('backend unreachable') },
      onError: () => { if (!mockMode) setConnection('backend unreachable') },
    })
    socket.connect()
    return () => { mounted = false; socket.close() }
  }, [])

  const activeView = tab === 'World' ? <WorldView /> : tab === 'Trace' ? <TraceView events={events} /> : tab === 'Human Tasks' ? <TasksView tasks={tasks} onTasksChange={setTasks} /> : tab === 'Scenarios' ? <ScenariosView scenarios={scenarios} onScenariosChange={setScenarios} /> : tab === 'Evals' ? <EvalsView evals={evals} /> : null
  const pending = Object.entries(loading).filter(([, active]) => active).map(([name]) => name)
  return <div className="app-shell"><header className="topbar"><div className="brand">Agent Lab <span>operator console</span></div><nav>{tabs.map((item) => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item}</button>)}</nav><ConnectionBadge status={connection} /></header><div className="layout"><aside className="rail"><AgentsView agents={agents} /><ChannelsView selected={channel} onSelect={setChannel} />{pending.length > 0 && <p className="muted">Loading: {pending.join(', ')}</p>}{Object.entries(errors).map(([name, message]) => <p className="muted" key={name}>{name}: {message}</p>)}</aside>{tab === 'Case' ? <main className="case-layout"><Conversation channel={channel} messages={messages} status={connection} /><CasesView cases={cases} /></main> : <div className="active-view">{activeView}</div>}</div></div>
}
