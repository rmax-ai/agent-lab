import { useEffect, useMemo, useState } from 'react'
import { AgentSocket, api } from './api'
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

function Conversation({ channel, messages, connected }: { channel: string; messages: ChannelMessage[]; connected: boolean }) {
  const visible = useMemo(() => messages.filter((message) => message.channel === channel), [channel, messages])
  return <section className="conversation"><header><div><h1>{channel}</h1><p className="muted">Agent conversation</p></div><span className={`connection ${connected ? 'connected' : ''}`}>{connected ? 'live' : 'mock replay'}</span></header>{visible.length === 0 ? <p className="empty">0 results</p> : <div className="messages">{visible.map((message, index) => <article key={`${message.agent_id}-${index}`}><strong>{message.agent_id}</strong><p>{message.message}</p></article>)}</div>}</section>
}

export default function App() {
  const [tab, setTab] = useState<Tab>('Case')
  const [channel, setChannel] = useState('#onboarding')
  const [agents, setAgents] = useState<Agent[]>(mockAgents)
  const [cases, setCases] = useState<Case[]>(mockCases)
  const [events, setEvents] = useState<TraceEvent[]>(mockEvents)
  const [tasks, setTasks] = useState<HumanTask[]>(mockTasks)
  const [scenarios, setScenarios] = useState<ScenarioInfo[]>(mockScenarios)
  const [evals, setEvals] = useState<EvalResult[]>(mockEvals)
  const [messages, setMessages] = useState<ChannelMessage[]>(mockMessages)
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    void Promise.all([api.agents(), api.cases(), api.events('ONB-42'), api.tasks(), api.scenarios(), api.evals()]).then(([nextAgents, nextCases, nextEvents, nextTasks, nextScenarios, nextEvals]) => {
      setAgents(nextAgents); setCases(nextCases); setEvents(nextEvents); setTasks(nextTasks); setScenarios(nextScenarios); setEvals(nextEvals)
    })
    const socket = new AgentSocket({ onMessage: (message) => setMessages((items) => [...items, message]), onEvent: (event) => setEvents((items) => [...items, event]), onStatus: setConnected })
    socket.connect()
    return () => socket.close()
  }, [])

  const activeView = tab === 'World' ? <WorldView /> : tab === 'Trace' ? <TraceView events={events} /> : tab === 'Human Tasks' ? <TasksView tasks={tasks} onTasksChange={setTasks} /> : tab === 'Scenarios' ? <ScenariosView scenarios={scenarios} onScenariosChange={setScenarios} /> : tab === 'Evals' ? <EvalsView evals={evals} /> : null
  return <div className="app-shell"><header className="topbar"><div className="brand">Agent Lab <span>operator console</span></div><nav>{tabs.map((item) => <button key={item} className={tab === item ? 'active' : ''} onClick={() => setTab(item)}>{item}</button>)}</nav></header><div className="layout"><aside className="rail"><AgentsView agents={agents} /><ChannelsView selected={channel} onSelect={setChannel} /></aside>{tab === 'Case' ? <main className="case-layout"><Conversation channel={channel} messages={messages} connected={connected} /><CasesView cases={cases} /></main> : <div className="active-view">{activeView}</div>}</div></div>
}
