import type {
  Agent,
  Case,
  ChannelMessage,
  EvalResult,
  HumanTask,
  ScenarioInfo,
  TraceEvent,
  WorldState,
} from './types'

export const mockAgents: Agent[] = [
  { id: 'onboarding-agent', status: 'online', tools: 3, knowledge_docs: 8 },
  { id: 'access-agent', status: 'online', tools: 5, knowledge_docs: 6 },
  { id: 'device-agent', status: 'online', tools: 5, knowledge_docs: 6 },
  { id: 'systems-agent', status: 'degraded', tools: 4, knowledge_docs: 5 },
  { id: 'applications-agent', status: 'online', tools: 4, knowledge_docs: 7 },
]

export const mockCases: Case[] = [
  {
    case_id: 'ONB-42', employee_id: 'E42', status: 'at_risk',
    domain_status: { access: 'complete', device: 'blocked', systems: 'running', applications: 'complete' },
    blockers: 1, approvals: 1, events: '/cases/ONB-42/events',
  },
]

const traceRows: Array<[string, string, string, Record<string, unknown>]> = [
  ['10:31:00', 'onboarding-agent', 'CASE_CREATED', { employee_id: 'E42' }],
  ['10:31:02', 'onboarding-agent', 'WORKFLOW_DELEGATED', { to: 'device-agent', domain: 'Device' }],
  ['10:31:03', 'onboarding-agent', 'WORKFLOW_DELEGATED', { to: 'systems-agent', domain: 'Systems' }],
  ['10:31:04', 'device-agent', 'TOOL_CALL', { tool: 'check_inventory', employee_id: 'E42' }],
  ['10:31:04', 'mockworld', 'TOOL_RESULT', { available: 0, model: 'MacBook Pro 14' }],
  ['10:31:07', 'device-agent', 'KNOWLEDGE_READ', { document: 'substitution-policy.md' }],
  ['10:31:10', 'device-agent', 'BLOCKER_CREATED', { reason: 'MBP14 inventory exhausted' }],
  ['10:31:11', 'device-agent', 'HUMAN_TASK_CREATED', { human_task_id: 'HT-1', type: 'APPROVAL' }],
  ['10:31:40', 'systems-agent', 'TOOL_CALL', { tool: 'create_identity', employee_id: 'E42' }],
  ['10:31:45', 'mockworld', 'TOOL_RESULT', { identity: 'created' }],
  ['10:32:15', 'human', 'APPROVAL_GRANTED', { human_task_id: 'HT-2', resolved_by: 'M1' }],
  ['10:32:17', 'device-agent', 'TOOL_CALL', { tool: 'reserve_device', model: 'MacBook Air 15' }],
  ['10:32:20', 'device-agent', 'OUTCOME_VERIFIED', { device_assigned: false, status: 'blocked' }],
]

export const mockEvents: TraceEvent[] = traceRows.map(([time, actor, type, payload], index) => ({
  ts: `2026-08-29T${time}.000Z`, case_id: 'ONB-42', workflow_id: index < 3 ? 'WF-O-42' : 'WF-D-42',
  actor, type, payload,
}))

export const mockTasks: HumanTask[] = [
  { human_task_id: 'HT-1', case_id: 'ONB-42', workflow_id: 'WF-D-42', requested_by: 'device-agent', requested_from: 'manager', type: 'APPROVAL', context: { request: 'Approve MacBook Air 15 substitution for MacBook Pro 14', reason: 'MBP14 inventory is 0' }, allowed_actions: ['approve', 'reject'], status: 'open', created_at: '2026-08-29T10:31:11Z' },
  { human_task_id: 'HT-2', case_id: 'ONB-42', workflow_id: 'WF-A-42', requested_by: 'access-agent', requested_from: 'manager', type: 'APPROVAL', context: { request: 'Approve baseline access package' }, allowed_actions: ['approve', 'reject'], status: 'resolved', decision: 'approve', resolved_by: 'M1', created_at: '2026-08-29T10:30:34Z', resolved_at: '2026-08-29T10:32:15Z' },
]

export const mockScenarios: ScenarioInfo[] = [
  { id: 'device-happy-path', status: 'passed', score: 1, detail: 'Device assigned and verified' },
  { id: 'device-missing-location', status: 'passed', score: 1, detail: 'Location requested safely' },
  { id: 'device-inventory-exhausted', status: 'passed', score: 0.92, detail: 'Substitution approval created' },
  { id: 'device-delivery-failure', status: 'failed', score: 0.45, detail: 'Replacement request missing' },
  { id: 'device-replacement-approval', status: 'passed', score: 0.96, detail: 'Approved replacement reserved' },
]

export const mockEvals: EvalResult[] = mockScenarios.map((scenario) => ({
  scenario_id: scenario.id,
  result: scenario.status === 'failed' ? 'fail' : 'pass', score: scenario.score ?? 0,
  expected: scenario.id === 'device-delivery-failure' ? ['delivery_failure_detected', 'replacement_requested'] : ['outcome_verified'],
  observed: scenario.id === 'device-delivery-failure' ? ['delivery_failure_detected', 'none'] : ['outcome_verified'],
  final_state: scenario.id === 'device-delivery-failure' ? 'BLOCKED' : 'COMPLETE',
}))

export const mockWorld: WorldState = {
  employee: { id: 'E42', role: 'Software Engineer', location: 'Amsterdam', start_date: '2026-09-01' },
  device: { required: 'MacBook Pro 14', assigned: 'none', status: 'awaiting approval' },
  inventory: { MBP14: 0, MBA15: 7 },
  access: { identity: 'created', baseline: 'complete' },
  applications: { GitHub: 'provisioned', Linear: 'provisioned', Slack: 'pending' },
}

export const mockMessages: ChannelMessage[] = [
  { type: 'channel_message', agent_id: 'onboarding-agent', channel: '#onboarding', message: 'Case ONB-42 opened for E42. Delegating domain workflows.' },
  { type: 'channel_message', agent_id: 'device-agent', channel: '#devices', message: 'MacBook Pro 14 inventory is exhausted; requesting substitution approval.' },
  { type: 'channel_message', agent_id: 'systems-agent', channel: '#systems', message: 'Identity created for E42; baseline configuration is running.' },
]
