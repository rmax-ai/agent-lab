import { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { mockWorld } from '../mock'
import type { TraceEvent, WorldState } from '../types'

function text(value: unknown): string {
  if (value === undefined || value === null || value === '') return '—'
  return typeof value === 'object' ? JSON.stringify(value) : String(value)
}

function findValue(value: unknown, names: string[]): unknown {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const record = value as Record<string, unknown>
  for (const name of names) if (record[name] !== undefined) return record[name]
  return undefined
}

function Readout({ title, rows }: { title: string; rows: Array<[string, unknown]> }) {
  return <section className="readout"><h2>{title}</h2>{rows.map(([key, value]) => <div key={key}><span>{key}</span><strong>{text(value)}</strong></div>)}</section>
}

type Belief = Record<string, unknown>
function beliefsFrom(events: TraceEvent[]): Belief {
  return events.filter((event) => ['TOOL_CALL', 'TOOL_RESULT', 'OUTCOME_VERIFIED'].includes(event.type)).reduce<Belief>((belief, event) => {
    const payload = event.payload
    const device = findValue(payload, ['device_id', 'assigned_device', 'reserved_device_id', 'device'])
    if (device !== undefined) belief.device = device
    const entitlement = findValue(payload, ['entitlement', 'entitlement_id', 'granted_entitlement', 'group_id'])
    if (entitlement !== undefined) belief.entitlements = entitlement
    const verified = findValue(payload, ['verified', 'device_assigned'])
    if (verified !== undefined) belief.verified = verified
    const status = findValue(payload, ['status', 'order_status'])
    if (status !== undefined) belief.order_status = status
    return belief
  }, {})
}

function DiffRow({ field, reality, belief }: { field: string; reality: unknown; belief: unknown }) {
  const reported = belief !== undefined
  const match = reported && text(reality) === text(belief)
  const state = !reported ? 'running' : match ? 'complete' : 'blocked'
  const label = !reported ? 'unknown/no-report' : match ? 'match' : 'mismatch'
  return <tr><td>{field}</td><td>{text(reality)}</td><td>{text(belief)}</td><td><span className={`pill ${state}`}>{label}</span></td></tr>
}

export function WorldView() {
  const [employeeId, setEmployeeId] = useState('E42')
  const [caseId, setCaseId] = useState('ONB-42')
  const [world, setWorld] = useState<WorldState>(mockWorld)
  const [events, setEvents] = useState<TraceEvent[]>([])
  const [error, setError] = useState<string | null>(null)
  useEffect(() => { void api.world(employeeId).then(setWorld).catch((reason: unknown) => setError(reason instanceof Error ? reason.message : 'World unavailable')) }, [employeeId])
  useEffect(() => { void api.events(caseId).then((nextEvents) => { setEvents(nextEvents); setError(null) }).catch((reason: unknown) => { setEvents([]); setError(reason instanceof Error ? reason.message : 'Events unavailable') }) }, [caseId])
  const belief = useMemo(() => beliefsFrom(events), [events])
  const assigned = world.device.assigned_device ?? findValue(world.device, ['assigned', 'device_id'])
  const order = world.device.order ?? findValue(world.device, ['status', 'order_status'])
  const entitlements = world.access.entitlements ?? world.access.groups ?? findValue(world.access, ['baseline'])
  const identity = world.access.identity ?? findValue(world.employee, ['status'])
  const knownIds = ['E42', ...Array.from({ length: 5 }, (_, index) => `E${101 + index}`), ...Array.from({ length: 12 }, (_, index) => `E${301 + index}`)]
  return <main className="view"><header className="view-head"><div><h1>World inspector</h1><p className="muted">Human-privileged reality vs agent belief</p></div><div><label>Employee <input list="employees" value={employeeId} onChange={(event) => setEmployeeId(event.target.value)} /></label><datalist id="employees">{knownIds.map((id) => <option key={id} value={id} />)}</datalist><label>Case <input value={caseId} onChange={(event) => setCaseId(event.target.value)} /></label></div></header>{error && <p className="muted">{error}</p>}<div className="readout-grid"><Readout title="Identity / status" rows={[["Employee", world.employee.id ?? employeeId], ["Role", world.employee.role], ["Status", world.employee.status], ["Identity", identity]]} /><Readout title="Device assignment" rows={[["Required SKU", world.device.required_sku ?? findValue(world.device, ['required'])], ["Assigned device", assigned], ["Order status", order]]} /><Readout title="Entitlements" rows={[["Granted", entitlements], ["Pending requests", world.access_requests?.map((request) => `${request.id}: ${request.status}`).join(', ')]]} /><Readout title="Agent belief" rows={[["Reserved device", belief.device], ["Granted entitlement", belief.entitlements], ["Verified", belief.verified], ["Order status", belief.order_status]]} /></div>{events.length === 0 ? <p className="empty">no agent reports for this case yet</p> : <div className="table-wrap"><table><thead><tr><th>Field</th><th>Reality</th><th>Belief</th><th>Diff</th></tr></thead><tbody><DiffRow field="Device assignment" reality={assigned} belief={belief.device} /><DiffRow field="Entitlements" reality={entitlements} belief={belief.entitlements} /><DiffRow field="Order status" reality={order} belief={belief.order_status} /><DiffRow field="Verified" reality={Boolean(assigned)} belief={belief.verified} /></tbody></table></div>}</main>
}
