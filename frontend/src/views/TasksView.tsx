import { useState } from 'react'
import { api } from '../api'
import { mockTasks } from '../mock'
import type { HumanTask } from '../types'

export function TasksView({ tasks = mockTasks, onTasksChange }: { tasks?: HumanTask[]; onTasksChange?: (tasks: HumanTask[]) => void }) {
  const [busy, setBusy] = useState<string | null>(null)
  const decide = async (task: HumanTask, decision: string) => { setBusy(task.human_task_id); const updated = await api.decideTask(task.human_task_id, decision); onTasksChange?.(tasks.map((item) => item.human_task_id === updated.human_task_id ? updated : item)); setBusy(null) }
  return <main className="view"><header className="view-head"><div><h1>Human Tasks</h1><p className="muted">Persisted workflow decisions</p></div></header>{tasks.length === 0 ? <p className="empty">0 results</p> : <div className="table-wrap"><table><thead><tr><th>Task</th><th>Case</th><th>Requested by</th><th>Context</th><th>Status</th><th>Decision</th></tr></thead><tbody>{tasks.map((task) => <tr key={task.human_task_id}><td>{task.human_task_id}<small>{task.type}</small></td><td>{task.case_id}</td><td>{task.requested_by}</td><td>{String(task.context.request ?? '')}</td><td><span className={`pill ${task.status === 'open' ? 'running' : 'complete'}`}>{task.status}</span></td><td>{task.status === 'open' ? <span className="actions"><button disabled={busy === task.human_task_id} onClick={() => void decide(task, 'approve')}>Approve</button><button className="danger" disabled={busy === task.human_task_id} onClick={() => void decide(task, 'reject')}>Reject</button></span> : `${task.decision} · ${task.resolved_by}`}</td></tr>)}</tbody></table></div>}</main>
}
