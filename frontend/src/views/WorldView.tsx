import { useEffect, useState } from 'react'
import { api } from '../api'
import { mockWorld } from '../mock'
import type { WorldState } from '../types'

function Readout({ title, data }: { title: string; data: Record<string, unknown> }) {
  return <section className="readout"><h2>{title}</h2>{Object.entries(data).map(([key, value]) => <div key={key}><span>{key.replaceAll('_', ' ')}</span><strong>{String(value)}</strong></div>)}</section>
}

export function WorldView() {
  const [employeeId, setEmployeeId] = useState('E42')
  const [world, setWorld] = useState<WorldState>(mockWorld)
  useEffect(() => { void api.world(employeeId).then(setWorld) }, [employeeId])
  return <main className="view"><header className="view-head"><div><h1>World inspector</h1><p className="muted">Human-privileged reality view</p></div><label>Employee <select value={employeeId} onChange={(event) => setEmployeeId(event.target.value)}><option>E42</option></select></label></header><div className="readout-grid"><Readout title="Employee" data={world.employee} /><Readout title="Device" data={world.device} /><Readout title="Inventory" data={world.inventory} /><Readout title="Access" data={world.access} /><Readout title="Applications" data={world.applications} /></div></main>
}
