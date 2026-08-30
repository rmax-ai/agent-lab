import { useMemo, useState } from 'react'
import { mockScenarios } from '../mock'
import type { ScenarioInfo } from '../types'

type ScenarioSource = 'live' | 'mock' | 'unavailable'
type ScenarioList = ScenarioInfo[] & { source?: ScenarioSource }
const domains = ['all', 'devices', 'access', 'integration', 'hidden'] as const

function ExpectedList({ title, items }: { title: string; items?: string[] }) {
  return <div><h3>{title}</h3><ul>{(items ?? []).map((item) => <li key={item}>{item}</li>)}</ul></div>
}

export function ScenariosView({ scenarios = mockScenarios, onScenariosChange: _onScenariosChange }: { scenarios?: ScenarioInfo[]; onScenariosChange?: (scenarios: ScenarioInfo[]) => void }) {
  const [domain, setDomain] = useState<(typeof domains)[number]>('all')
  const source = (scenarios as ScenarioList).source
  const availableDomains = domains.filter((item) => item !== 'hidden' || scenarios.some((scenario) => scenario.hidden || scenario.domain === 'hidden'))
  const visible = useMemo(() => scenarios.filter((scenario) => domain === 'all' || scenario.domain === domain), [scenarios, domain])
  return <main className="view"><header className="view-head"><div><h1>Scenario browser</h1><p className="muted">{scenarios.length} scenarios</p></div></header><p className="actions">{availableDomains.map((item) => <button key={item} className={domain === item ? 'active' : ''} onClick={() => setDomain(item)}>{item}</button>)}</p>{scenarios.length === 0 ? <p className="empty">{source === 'unavailable' ? 'scenario routes unavailable' : '0 scenarios'}</p> : visible.length === 0 ? <p className="empty">0 scenarios</p> : <div className="eval-list">{visible.map((scenario) => <article className="eval" key={scenario.id}><header><div><strong>{scenario.id}</strong><small>{scenario.file ?? '—'}</small></div><div><span className="pill running">{scenario.domain ?? 'unknown'}</span>{scenario.hidden && <span className="pill blocked">hidden</span>}</div></header>{!scenario.hidden && <div className="compare"><ExpectedList title="Required events" items={scenario.required_events} /><ExpectedList title="Allowed final states" items={scenario.allowed_final_states} /><ExpectedList title="Forbidden events" items={scenario.forbidden_events} /></div>}</article>)}</div>}</main>
}
