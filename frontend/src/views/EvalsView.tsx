import { mockEvals } from '../mock'
import type { EvalResult } from '../types'

export function EvalsView({ evals = mockEvals }: { evals?: EvalResult[] }) {
  return <main className="view"><header className="view-head"><div><h1>Evaluation results</h1><p className="muted">Expected vs observed event names and final state</p></div></header>{evals.length === 0 ? <p className="empty">0 results</p> : <div className="eval-list">{evals.map((evaluation) => <article className="eval" key={evaluation.scenario_id}><header><strong>{evaluation.scenario_id}</strong><span className={`pill ${evaluation.result === 'pass' ? 'complete' : 'blocked'}`}>{evaluation.result.toUpperCase()} · {Math.round(evaluation.score * 100)}%</span></header><div className="compare"><div><h3>Expected</h3><ul>{evaluation.expected.map((item) => <li key={item}>{item}</li>)}</ul></div><div><h3>Observed</h3><ul>{evaluation.observed.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div></div><footer>Final state: <strong>{evaluation.final_state ?? '—'}</strong></footer></article>)}</div>}</main>
}
