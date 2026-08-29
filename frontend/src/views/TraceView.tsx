import { useMemo, useState } from 'react'
import { mockEvents } from '../mock'
import type { TraceEvent } from '../types'

export function TraceView({ events = mockEvents }: { events?: TraceEvent[] }) {
  const [caseId, setCaseId] = useState('ONB-42')
  const filtered = useMemo(() => events.filter((event) => event.case_id.includes(caseId)), [events, caseId])
  return <main className="view"><header className="view-head"><div><h1>Trace timeline</h1><p className="muted">Observable actions, not private reasoning</p></div><label>Case <input value={caseId} onChange={(event) => setCaseId(event.target.value)} /></label></header>{filtered.length === 0 ? <p className="empty">0 results</p> : <ol className="trace">{filtered.map((event, index) => <li key={`${event.ts}-${index}`}><time>{event.ts.slice(11, 19)}</time><span>{event.actor}</span><strong>{event.type}</strong><code>{Object.entries(event.payload).map(([key, value]) => `${key}=${String(value)}`).join(' ')}</code></li>)}</ol>}</main>
}
