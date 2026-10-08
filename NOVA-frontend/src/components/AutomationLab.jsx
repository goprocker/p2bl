import { useState } from 'react'

const SCENARIOS = {
  arrival: {
    label: '01 / Evening arrival',
    note: 'Comfort rule: occupied + low light + evening',
    observations: { occupied: true, light_level_lux: 32, local_hour: 19, away_mode: false },
    approved_standby_devices: [],
  },
  energy: {
    label: '02 / Away energy',
    note: 'Energy rule: empty + away + high power',
    observations: { occupied: false, away_mode: true, home_power_w: 1650, local_hour: 19 },
    approved_standby_devices: ['television', 'desk lamp'],
  },
  safety: {
    label: '03 / Unusual entry',
    note: 'Safety rule overrides normal lighting',
    observations: {
      occupied: true,
      light_level_lux: 24,
      away_mode: true,
      door_open: true,
      authorized_entry: false,
      local_hour: 19,
    },
    approved_standby_devices: [],
  },
}

const LOCAL_RULES = [
  { id: 'evening-arrival-light', name: 'Evening arrival lighting', group: 'comfort',
    priority: 'comfort', enabled: true,
    explanation: 'Occupied room, low ambient light, and evening time require lighting.' },
  { id: 'away-high-load', name: 'Away-mode energy reduction', group: 'energy',
    priority: 'energy', enabled: true,
    explanation: 'Empty home and high demand allow approved standby loads to switch off.' },
  { id: 'unusual-entry', name: 'Unexpected entry protection', group: 'safety',
    priority: 'safety', enabled: true,
    explanation: 'Door opened in away mode without authorization requires an alert.' },
]

function localEvaluate(payload, rules) {
  const started = performance.now()
  const facts = Object.entries(payload.observations)
    .filter(([name, value]) => value != null && (name !== 'occupied' || payload.occupancy_automation))
    .map(([name, value]) => ({ name, value, source: 'mock sensor' }))
  const values = Object.fromEntries(facts.map((fact) => [fact.name, fact.value]))
  const enabled = Object.fromEntries(rules.map((rule) => [rule.id, rule.enabled]))
  const matched = []
  const candidates = []
  const add = (ruleId, action) => {
    const rule = rules.find((item) => item.id === ruleId)
    matched.push({ ...rule, facts: facts.map((fact) => fact.name) })
    candidates.push({ ...action, rule_id: ruleId, priority: rule.priority,
      explanation: rule.explanation })
  }
  if (enabled['unusual-entry'] && values.door_open && values.away_mode && !values.authorized_entry) {
    add('unusual-entry', { kind: 'notification', target: 'resident', value: 'Unexpected entry detected' })
  }
  if (enabled['evening-arrival-light'] && values.occupied && values.light_level_lux < 100
      && values.local_hour >= 18 && values.local_hour < 23) {
    add('evening-arrival-light', { kind: 'device', target: 'living room light', value: 'on' })
  }
  if (enabled['away-high-load'] && values.occupied === false && values.away_mode
      && values.home_power_w >= 1000) {
    payload.approved_standby_devices.forEach((device) =>
      add('away-high-load', { kind: 'device', target: device, value: 'off' }))
  }
  if (payload.resident_command) {
    candidates.push({ kind: 'device', target: payload.resident_command.device,
      value: payload.resident_command.state, rule_id: 'resident-override', priority: 'resident',
      explanation: 'Resident override has priority for the next 30 minutes.' })
  }
  const rank = { safety: 5, resident: 4, comfort: 2, energy: 1 }
  const safetyLock = candidates.some((action) => action.priority === 'safety')
  const selected = []
  const rejected = []
  const targets = new Set()
  candidates.sort((a, b) => rank[b.priority] - rank[a.priority]).forEach((action) => {
    if (safetyLock && !['safety', 'resident'].includes(action.priority)) {
      rejected.push({ ...action, rejection_reason: 'Suppressed by active safety policy.' })
    } else if (targets.has(action.target)) {
      rejected.push({ ...action, rejection_reason: 'Higher-priority action controls this target.' })
    } else {
      targets.add(action.target)
      selected.push(action)
    }
  })
  const trace = [
    { agent: 'Sensing Agent', message: `Accepted ${Object.keys(payload.observations).length} mock observations.` },
    { agent: 'Context Agent', message: payload.occupancy_automation
      ? `Published ${facts.length} context facts.` : 'Occupancy consent paused; occupancy fact omitted.' },
    { agent: 'Rule Agent', message: `Matched ${matched.length} policies; proposed ${candidates.length} actions.` },
    ...(matched.some((rule) => rule.group === 'energy')
      ? [{ agent: 'Energy Agent', message: 'Limited actions to approved standby loads.' }] : []),
    { agent: 'Conflict Resolver', message: `Selected ${selected.length}; rejected ${rejected.length} by priority.` },
    { agent: 'Device Agent', message: 'Dry-run recorded proposed device commands.' },
    { agent: 'Notification Agent', message: 'Published explanations and alerts.' },
  ]
  return { facts, matched_rules: matched, selected_actions: selected,
    rejected_actions: rejected, trace,
    execution: selected.map((action) => ({ ...action,
      status: action.kind === 'notification' ? 'notified' : 'proposed' })),
    duration_ms: Math.max(1, Math.round(performance.now() - started)), dry_run: true }
}

function eveningTimestamp() {
  const date = new Date()
  date.setHours(19, 0, 0, 0)
  return date.toISOString()
}

export default function AutomationLab() {
  const [scenarioKey, setScenarioKey] = useState('arrival')
  const [context, setContext] = useState(SCENARIOS.arrival)
  const [rules, setRules] = useState(LOCAL_RULES)
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [dryRun, setDryRun] = useState(true)
  const [overrideOff, setOverrideOff] = useState(false)
  const [occupancyAutomation, setOccupancyAutomation] = useState(true)
  const engineMode = 'local demo'

  function chooseScenario(key) {
    setScenarioKey(key)
    setContext(SCENARIOS[key])
    setResult(null)
    setError('')
    setOverrideOff(false)
    setOccupancyAutomation(true)
  }

  async function runScenario() {
    setBusy(true)
    setError('')
    const payload = {
      room: 'living_room',
      observed_at: eveningTimestamp(),
      observations: context.observations,
      approved_standby_devices: context.approved_standby_devices,
      occupancy_automation: occupancyAutomation,
      dry_run: dryRun,
    }
    if (overrideOff) {
      payload.resident_command = {
          device: 'living room light',
          state: 'off',
          hold_minutes: 30,
      }
    }
    setResult(localEvaluate(payload, rules))
    setBusy(false)
  }

  function toggleRule(rule) {
    setRules((items) => items.map((item) =>
      item.id === rule.id ? { ...item, enabled: !item.enabled } : item))
  }

  return (
    <section className="automation-lab">
      <header className="lab-header">
        <div>
          <span className="eyebrow">RESEARCH PROTOTYPE / LOCAL RULE ENGINE</span>
          <h1>Context enters. Agents reason. Actions explain themselves.</h1>
          <p>Rule-based multi-agent smart-home simulation using mock sensor data.</p>
        </div>
        <div className="live-badge"><span /> {engineMode.toUpperCase()} / {dryRun ? 'SAFE DRY-RUN' : 'LIVE REQUEST'}</div>
      </header>

      <div className="scenario-strip">
        {Object.entries(SCENARIOS).map(([key, scenario]) => (
          <button key={key} className={scenarioKey === key ? 'active' : ''}
            onClick={() => chooseScenario(key)}>
            <strong>{scenario.label}</strong><span>{scenario.note}</span>
          </button>
        ))}
      </div>

      <div className="lab-grid">
        <article className="lab-panel context-panel">
          <div className="panel-kicker">A / CONTEXT INPUT</div>
          <h2>Mock sensor snapshot</h2>
          <div className="fact-editor">
            {Object.entries(context.observations).map(([name, value]) => (
              <div className="fact-row" key={name}>
                <span>{name.replaceAll('_', ' ')}</span><code>{String(value)}</code>
              </div>
            ))}
          </div>
          <label className="check-row">
            <input type="checkbox" checked={occupancyAutomation}
              onChange={(event) => setOccupancyAutomation(event.target.checked)} />
            Occupancy automation consent
          </label>
          {scenarioKey === 'arrival' && (
            <label className="check-row">
              <input type="checkbox" checked={overrideOff}
                onChange={(event) => setOverrideOff(event.target.checked)} />
              Add resident override: light OFF for 30 min
            </label>
          )}
          <label className="check-row">
            <input type="checkbox" checked={dryRun}
              onChange={(event) => setDryRun(event.target.checked)} />
            Dry-run — do not control real devices
          </label>
          <button className="evaluate-btn" onClick={runScenario} disabled={busy}>
            {busy ? 'Agents evaluating…' : 'Run agent pipeline'}
          </button>
          {error && <div className="lab-error">{error}</div>}
        </article>

        <article className="lab-panel rules-panel">
          <div className="panel-kicker">B / RESIDENT-APPROVED POLICIES</div>
          <h2>Active rule base</h2>
          {rules.map((rule) => (
            <button className={`rule-card ${rule.enabled ? '' : 'disabled'}`}
              onClick={() => toggleRule(rule)} key={rule.id}>
              <span className={`priority priority-${rule.priority}`}>{rule.priority}</span>
              <strong>{rule.name}</strong>
              <small>{rule.explanation}</small>
              <span className="rule-state">{rule.enabled ? 'ACTIVE' : 'PAUSED'}</span>
            </button>
          ))}
        </article>
      </div>

      <article className={`decision-board ${result ? 'has-result' : ''}`}>
        <div className="panel-kicker">C / EXPLAINABLE DECISION</div>
        {!result ? (
          <div className="empty-decision">Run a scenario to reveal every agent hand-off.</div>
        ) : (
          <>
            <div className="decision-summary">
              <div><span>matched</span><strong>{result.matched_rules.length}</strong></div>
              <div><span>selected</span><strong>{result.selected_actions.length}</strong></div>
              <div><span>rejected</span><strong>{result.rejected_actions.length}</strong></div>
              <div><span>latency</span><strong>{result.duration_ms}ms</strong></div>
            </div>
            <div className="pipeline">
              {result.trace.map((step, index) => (
                <div className="agent-step" key={`${step.agent}-${index}`}>
                  <span>{String(index + 1).padStart(2, '0')}</span>
                  <div><strong>{step.agent}</strong><p>{step.message}</p></div>
                </div>
              ))}
            </div>
            <div className="outcome-grid">
              <div>
                <h3>Selected actions</h3>
                {result.execution.map((action, index) => (
                  <div className="action-card selected" key={index}>
                    <strong>{action.target} → {action.value}</strong>
                    <span>{action.priority} / {action.status}</span>
                    <p>{action.explanation}</p>
                  </div>
                ))}
              </div>
              <div>
                <h3>Rejected conflicts</h3>
                {result.rejected_actions.length === 0 && <p className="muted">No conflicts.</p>}
                {result.rejected_actions.map((action, index) => (
                  <div className="action-card rejected" key={index}>
                    <strong>{action.target} → {action.value}</strong>
                    <span>{action.priority} / rejected</span>
                    <p>{action.rejection_reason}</p>
                  </div>
                ))}
              </div>
            </div>
          </>
        )}
      </article>
    </section>
  )
}
