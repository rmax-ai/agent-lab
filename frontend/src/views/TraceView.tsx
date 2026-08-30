import { useMemo, useState } from 'react'
import { mockEvents } from '../mock'
import type { TraceEvent } from '../types'

export function TraceView({ events = mockEvents }: { events?: TraceEvent[] }) {
  const [caseId, setCaseId] = useState('ONB-42')
  const [type, setType] = useState('all')
  const eventTypes = useMemo(() => [...new Set(events.map((event) => event.type))].sort(), [events])
  const filtered = useMemo(() => events.filter((event) => event.case_id.includes(caseId) && (type === 'all' || event.type === type)), [events, caseId, type])
  const actors = new Set(filtered.map((event) => event.actor)).size
  return <main className="view"><header className="view-head"><div><h1>Trace timeline</h1><p className="muted">Observable actions, not private reasoning</p></div><div><label>Case <input value={caseId} onChange={(event) => setCaseId(event.target.value)} /></label><label>Type <select value={type} onChange={(event) => setType(event.target.value)}><option value="all">All types</option>{eventTypes.map((eventType) => <option key={eventType}>{eventType}</option>)}</select></label></div></header><p className="muted">{filtered.length} events, {actors} unique actors</p>{filtered.length === 0 ? <p className="empty">no events for this case</p> : <div className="table-wrap"><table><thead><tr><th>Time</th><th>Actor</th><th>Workflow</th><th>Type</th><th>Payload</th></tr></thead><tbody>{filtered.map((event, index) => <tr key={`${event.ts}-${index}`}><td>{event.ts.slice(11, 19)}</td><td>{event.actor}</td><td>{event.workflow_id ?? '—'}</td><td><strong>{event.type}</strong></td><td><details><summary>{Object.keys(event.payload).length} fields</summary><dl>{Object.entries(event.payload).map(([key, value]) => <div key={key}><dt>{key}</dt><dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd></div>)}</dl></details></td></tr>)}</tbody></table></div>}</main>
}
