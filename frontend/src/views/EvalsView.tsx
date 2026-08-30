import { mockEvals } from '../mock'
import type { EvalModel, EvalResult } from '../types'

type EvalList = EvalResult[] & { source?: 'live' | 'mock' | 'unavailable' }
function isModel(value: unknown): value is EvalModel { return Boolean(value && typeof value === 'object' && 'dimensions' in value && 'threshold' in value && 'packs' in value) }

export function EvalsView({ evals = mockEvals }: { evals?: EvalResult[] }) {
  const source = (evals as EvalList).source
  const model = isModel(evals[0]) ? evals[0] : undefined
  if (model) {
    const total = model.dimensions.reduce((sum, dimension) => sum + dimension.weight, 0)
    return <main className="view"><header className="view-head"><div><h1>Evaluation model</h1><p className="muted">PASS threshold: {model.threshold.toFixed(1)}</p></div></header><div className="eval-list"><article className="eval"><header><strong>Weighted dimensions</strong></header><div className="compare">{model.dimensions.map((dimension) => <div key={dimension.name}><h3>{dimension.name}</h3><strong>{dimension.weight.toFixed(1)}</strong><div style={{ height: 6, marginTop: 8, background: '#21262d', borderRadius: 99 }}><div style={{ width: `${(dimension.weight / total) * 100}%`, height: '100%', background: '#58a6ff', borderRadius: 99 }} /></div></div>)}</div></article><article className="eval"><header><strong>Pack inventory</strong></header><div className="compare">{(['devices', 'access', 'integration'] as const).map((domain) => <div key={domain}><h3>{domain}</h3><ul>{model.packs[domain].map((id) => <li key={id}>{id}</li>)}</ul></div>)}<div><h3>Hidden</h3><strong>{model.packs.hidden_count} scenarios</strong></div></div></article></div>{source === 'live' && <p className="muted">Live run results are produced by the pack harness (pytest) or the CLI (agent-lab scenario run); the console shows the evaluation model.</p>}</main>
  }
  return <main className="view"><header className="view-head"><div><h1>Evaluation results</h1><p className="muted">Mock replay results</p></div></header>{evals.length === 0 ? <p className="empty">{source === 'unavailable' ? 'evaluation routes unavailable' : '0 results'}</p> : <div className="eval-list">{evals.map((evaluation) => <article className="eval" key={evaluation.scenario_id}><header><strong>{evaluation.scenario_id}</strong><span className={`pill ${evaluation.result === 'pass' ? 'complete' : 'blocked'}`}>{evaluation.result.toUpperCase()} · {Math.round(evaluation.score * 100)}%</span></header><div className="compare"><div><h3>Expected</h3><ul>{evaluation.expected.map((item) => <li key={item}>{item}</li>)}</ul></div><div><h3>Observed</h3><ul>{evaluation.observed.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div></div><footer>Final state: <strong>{evaluation.final_state ?? '—'}</strong></footer></article>)}</div>}</main>
}
