import { useState } from 'react'
import { api } from '../api'
import { mockScenarios } from '../mock'
import type { ScenarioInfo } from '../types'

export function ScenariosView({ scenarios = mockScenarios, onScenariosChange }: { scenarios?: ScenarioInfo[]; onScenariosChange?: (scenarios: ScenarioInfo[]) => void }) {
  const [running, setRunning] = useState<string | null>(null)
  const run = async (scenario: ScenarioInfo) => { setRunning(scenario.id); const updated = await api.runScenario(scenario.id); onScenariosChange?.(scenarios.map((item) => item.id === scenario.id ? updated : item)); setRunning(null) }
  const passed = scenarios.filter((item) => item.status === 'passed').length
  return <main className="view"><header className="view-head"><div><h1>Device Agent scenarios</h1><p className="muted">{passed} / {scenarios.length} passed</p></div></header>{scenarios.length === 0 ? <p className="empty">0 results</p> : <div className="table-wrap"><table><thead><tr><th>Scenario</th><th>Result</th><th>Score</th><th>Detail</th><th /></tr></thead><tbody>{scenarios.map((scenario) => <tr key={scenario.id}><td>{scenario.id}</td><td><span className={`pill ${scenario.status === 'failed' ? 'blocked' : scenario.status === 'passed' ? 'complete' : 'running'}`}>{scenario.status === 'passed' ? 'PASS' : scenario.status === 'failed' ? 'FAIL' : 'NOT RUN'}</span></td><td>{scenario.score === null ? '—' : `${Math.round(scenario.score * 100)}%`}</td><td>{scenario.detail ?? '—'}</td><td><button disabled={running === scenario.id} onClick={() => void run(scenario)}>{running === scenario.id ? 'Running…' : 'Run'}</button></td></tr>)}</tbody></table></div>}</main>
}
