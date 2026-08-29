import { mockCases } from '../mock'
import type { Case, DomainState } from '../types'

const labels: Record<string, string> = { access: 'Access', device: 'Device', systems: 'Systems', applications: 'Applications' }
const symbols: Record<DomainState, string> = { complete: '✓', blocked: '!', running: '…', pending: '○' }

export function CasesView({ cases = mockCases }: { cases?: Case[] }) {
  return <section className="cases"><h2>Case</h2>{cases.length === 0 ? <p className="empty">0 results</p> : cases.map((item) => <article className="case-card" key={item.case_id}><h1>{item.case_id}</h1><p className="muted">Employee {item.employee_id} · <span className="pill blocked">{item.status}</span></p><dl>{(Object.entries(item.domain_status) as [string, DomainState][]).map(([domain, status]) => <div key={domain}><dt>{labels[domain]}</dt><dd className={`domain ${status}`}>{symbols[status]} <span>{status}</span></dd></div>)}</dl><footer><span>Blockers: <strong>{item.blockers}</strong></span><span>Approvals: <strong>{item.approvals}</strong></span></footer></article>)}</section>
}
