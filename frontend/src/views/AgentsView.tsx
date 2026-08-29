import { mockAgents } from '../mock'
import type { Agent } from '../types'

export function AgentsView({ agents = mockAgents }: { agents?: Agent[] }) {
  return <section><h2>Agents</h2>{agents.length === 0 ? <p className="empty">0 results</p> : <ul className="agent-list">{agents.map((agent) => <li key={agent.id}><span className={`dot ${agent.status}`} /><span><strong>{agent.id}</strong><small>{agent.tools} tools · {agent.knowledge_docs} knowledge docs</small></span><span className={`pill ${agent.status}`}>{agent.status}</span></li>)}</ul>}</section>
}
